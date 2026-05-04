"""Async client for ai-auto.io.

Endpoints used:
  POST /generate            -- create a video or image generation
  GET  /generations/{id}    -- poll status
  GET  /generations/{id}/download  -- download MP4 bytes
  GET  /generations/{id}/image     -- download JPEG bytes (image jobs)
  GET  /auth/me             -- account info / quotas

Auth: Bearer <key> in Authorization header. The docs also show X-API-Key for
some examples; Bearer works across both video and image endpoints we hit.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

BASE_URL = "https://api.ai-auto.io/api/saas"

# How often we poll a job for completion, in seconds.
POLL_INTERVAL = 6
# Hard ceiling on a single job's wall-clock wait. Seedance 4K/15s usually finishes
# inside a few minutes; this gives plenty of headroom before we error out.
POLL_TIMEOUT = 60 * 25


class AIAutoError(RuntimeError):
    pass


class TransientError(AIAutoError):
    """Retryable: rate limit or 5xx."""


class PermanentError(AIAutoError):
    """Don't retry: 400/401/403/404."""


@dataclass
class Generation:
    id: str
    status: str
    video_url: str | None
    raw: dict[str, Any]


def file_to_data_url(path: Path | str) -> str:
    p = Path(path)
    suffix = p.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "jpeg")
    return f"data:image/{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def bytes_to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class AIAutoClient:
    def __init__(
        self,
        api_key: str,
        video_concurrency: int = 3,
        image_concurrency: int = 4,
        timeout: float = 120.0,
    ):
        if not api_key:
            raise PermanentError("AI_AUTO_API_KEY is empty. Run `cinema setup`.")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._video_sem = asyncio.Semaphore(video_concurrency)
        self._image_sem = asyncio.Semaphore(image_concurrency)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, headers=self._headers, timeout=timeout
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AIAutoClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(TransientError),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.request(method, path, **kwargs)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise TransientError(f"{resp.status_code} on {path}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    raise PermanentError(
                        f"{resp.status_code} on {path}: {resp.text[:500]}"
                    )
                return resp
        raise AIAutoError("unreachable")

    async def me(self) -> dict[str, Any]:
        resp = await self._request("GET", "/auth/me")
        return resp.json()

    async def _start_generation(self, body: dict[str, Any]) -> str:
        resp = await self._request("POST", "/generate", json=body)
        data = resp.json()
        gen = data.get("generation") or data
        gen_id = gen.get("id")
        if not gen_id:
            raise PermanentError(f"No generation id in response: {data}")
        return gen_id

    async def _poll(self, gen_id: str) -> Generation:
        elapsed = 0
        while elapsed < POLL_TIMEOUT:
            resp = await self._request("GET", f"/generations/{gen_id}")
            data = resp.json()
            gen = data.get("generation") or data
            status = (gen.get("status") or "").lower()
            if status in ("completed", "succeeded", "success", "done"):
                return Generation(
                    id=gen_id,
                    status=status,
                    video_url=gen.get("video_url"),
                    raw=gen,
                )
            if status in ("failed", "error", "canceled", "cancelled"):
                raise PermanentError(f"Generation {gen_id} failed: {gen}")
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
        raise TransientError(f"Generation {gen_id} timed out after {POLL_TIMEOUT}s")

    async def generate_video(
        self,
        *,
        prompt: str,
        model: str = "seedance_2",
        aspect_ratio: str = "16:9",
        resolution: str = "4k",
        seconds: int = 15,
        i2v_frame_start: str | None = None,
        i2v_frame_end: str | None = None,
        i2v_reference_images: list[str] | None = None,
        i2v_mode: str | None = None,
    ) -> Generation:
        body: dict[str, Any] = {
            "prompt": prompt,
            "mode": "shorts",
            "model": model,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "seconds": seconds,
        }
        if i2v_frame_start:
            body["i2v_mode"] = i2v_mode or "frames"
            body["i2v_frame_start"] = i2v_frame_start
            if i2v_frame_end:
                body["i2v_frame_end"] = i2v_frame_end
        elif i2v_reference_images:
            body["i2v_mode"] = i2v_mode or "ingredients"
            body["i2v_reference_images"] = i2v_reference_images[:2]

        async with self._video_sem:
            gen_id = await self._start_generation(body)
            return await self._poll(gen_id)

    async def generate_image(
        self,
        *,
        prompt: str,
        image_model: str,
        aspect_ratio: str = "16:9",
        resolution: str = "1k",
        reference_images: list[str] | None = None,
    ) -> Generation:
        body: dict[str, Any] = {
            "prompt": prompt,
            "mode": "images",
            "model": "standard",
            "image_model": image_model,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if reference_images:
            # ai-auto.io image models that support refs (e.g. nano_banana_pro,
            # gtv_seedream_4_5) read up to 2 images from this field and lock
            # identity / wardrobe / setting from them.
            body["reference_images"] = list(reference_images)[:2]
        async with self._image_sem:
            gen_id = await self._start_generation(body)
            return await self._poll(gen_id)

    async def download_video(self, gen_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with self._client.stream(
            "GET", f"/generations/{gen_id}/download", follow_redirects=True
        ) as resp:
            if resp.status_code >= 400:
                raise PermanentError(
                    f"Video download {gen_id} failed: {resp.status_code}"
                )
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1 << 16):
                    f.write(chunk)
        return dest

    async def download_image(self, gen_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = await self._request("GET", f"/generations/{gen_id}/image")
        dest.write_bytes(resp.content)
        return dest
