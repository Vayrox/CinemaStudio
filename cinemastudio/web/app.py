"""FastAPI app for CinemaStudio."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cinemastudio import settings
from cinemastudio.pipeline import generate as gen_mod
from cinemastudio.pipeline import moodboard as mb_mod
from cinemastudio.pipeline import script as script_mod
from cinemastudio.providers.ai_auto import AIAutoClient
from cinemastudio.web.console_adapter import capture_pipeline_logs
from cinemastudio.web.jobs import JobContext, JobRunner

PROJECTS_ROOT = Path(__file__).parent.parent.parent / "projects"
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def _slug_from_logline(logline: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", logline.lower()).strip("-")
    return s[:40].rstrip("-") or "untitled"


def create_app() -> FastAPI:
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    settings.ensure_config_file()

    app = FastAPI(title="CinemaStudio", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    runner = JobRunner(PROJECTS_ROOT)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---------------------------------------------------------------- helpers

    def _list_projects() -> list[dict[str, Any]]:
        out = []
        if not PROJECTS_ROOT.exists():
            return out
        for path in sorted(PROJECTS_ROOT.iterdir()):
            if not path.is_dir():
                continue
            sp_path = path / "screenplay.json"
            data: dict[str, Any] = {"slug": path.name, "title": path.name, "shots": 0, "scenes": 0, "logline": ""}
            if sp_path.exists():
                try:
                    sp = json.loads(sp_path.read_text())
                    data.update(
                        title=sp.get("title", path.name),
                        shots=len(sp.get("shots", [])),
                        scenes=len(sp.get("scenes", [])),
                        logline=sp.get("logline", ""),
                    )
                except Exception:
                    pass
            clips_dir = path / "clips"
            data["clips"] = (
                len([p for p in clips_dir.iterdir() if p.suffix == ".mp4"])
                if clips_dir.exists()
                else 0
            )
            keyframes_dir = path / "keyframes"
            data["keyframes"] = (
                len([p for p in keyframes_dir.iterdir() if p.suffix in (".jpg", ".png")])
                if keyframes_dir.exists()
                else 0
            )
            data["mtime"] = path.stat().st_mtime
            data["job"] = runner.status(path.name)
            out.append(data)
        return sorted(out, key=lambda d: -d["mtime"])

    def _project_payload(slug: str) -> dict[str, Any]:
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            raise HTTPException(404, f"Project {slug} not found.")
        sp_path = project_dir / "screenplay.json"
        screenplay = None
        if sp_path.exists():
            try:
                screenplay = json.loads(sp_path.read_text())
            except Exception:
                screenplay = None

        keyframes_dir = project_dir / "keyframes"
        moodboards_dir = project_dir / "moodboards"
        clips_dir = project_dir / "clips"

        keyframes = (
            sorted(p.name for p in keyframes_dir.iterdir() if p.suffix in (".jpg", ".png"))
            if keyframes_dir.exists()
            else []
        )
        moodboards = (
            sorted(p.name for p in moodboards_dir.iterdir() if p.suffix in (".jpg", ".png"))
            if moodboards_dir.exists()
            else []
        )
        clips = (
            sorted(p.name for p in clips_dir.iterdir() if p.suffix == ".mp4")
            if clips_dir.exists()
            else []
        )
        return {
            "slug": slug,
            "screenplay": screenplay,
            "keyframes": keyframes,
            "moodboards": moodboards,
            "clips": clips,
            "has_edl": (project_dir / "edl.csv").exists(),
            "job": runner.status(slug),
        }

    def _config():
        return settings.get()

    def _keys_status() -> dict[str, bool]:
        cfg = _config()
        return {
            "ai_auto": bool(getattr(cfg, "AI_AUTO_API_KEY", "")),
            "anthropic": bool(getattr(cfg, "ANTHROPIC_API_KEY", "")),
        }

    # ---------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        keys = _keys_status()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "projects": _list_projects(),
                "keys": keys,
                "config": _config(),
                "needs_setup": not (keys["ai_auto"] and keys["anthropic"]),
            },
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> HTMLResponse:
        cfg = _config()
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "config": cfg,
                "keys": _keys_status(),
                "saved": request.query_params.get("saved") == "1",
            },
        )

    @app.post("/setup")
    async def setup_save(
        ai_auto_api_key: str = Form(""),
        anthropic_api_key: str = Form(""),
        default_aspect_ratio: str = Form("16:9"),
        default_resolution: str = Form("4k"),
        default_image_resolution: str = Form("4k"),
        default_shot_seconds: int = Form(15),
        video_concurrency: int = Form(3),
        image_concurrency: int = Form(4),
    ) -> RedirectResponse:
        cfg = _config()
        ai_auto = ai_auto_api_key.strip() or cfg.AI_AUTO_API_KEY
        anthropic = anthropic_api_key.strip() or cfg.ANTHROPIC_API_KEY
        settings.write_keys(ai_auto_key=ai_auto, anthropic_key=anthropic)

        # Update non-secret config fields with simple line replacements.
        text = settings.CONFIG_PATH.read_text()
        text = _replace_assignment_str(text, "DEFAULT_ASPECT_RATIO", default_aspect_ratio)
        text = _replace_assignment_str(text, "DEFAULT_RESOLUTION", default_resolution)
        text = _replace_assignment_str(text, "DEFAULT_IMAGE_RESOLUTION", default_image_resolution)
        text = _replace_assignment_int(text, "DEFAULT_SHOT_SECONDS", default_shot_seconds)
        text = _replace_assignment_int(text, "VIDEO_CONCURRENCY", video_concurrency)
        text = _replace_assignment_int(text, "IMAGE_CONCURRENCY", image_concurrency)
        settings.CONFIG_PATH.write_text(text)
        return RedirectResponse("/setup?saved=1", status_code=303)

    @app.get("/new", response_class=HTMLResponse)
    async def new_project_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "new.html",
            {
                "config": _config(),
                "keys": _keys_status(),
            },
        )

    @app.post("/new")
    async def new_project(
        logline: str = Form(...),
        slug: str = Form(""),
        aspect_ratio: str = Form(...),
        target_minutes: float = Form(1.0),
        shot_seconds: int = Form(15),
        resolution: str = Form("4k"),
        run_full: str = Form(""),
    ) -> RedirectResponse:
        slug_clean = (slug.strip() or _slug_from_logline(logline)).lower()
        slug_clean = re.sub(r"[^a-z0-9-]+", "-", slug_clean).strip("-") or "untitled"
        project_dir = PROJECTS_ROOT / slug_clean
        project_dir.mkdir(parents=True, exist_ok=True)

        # Write a manifest so we remember settings between runs.
        manifest = {
            "logline": logline,
            "aspect_ratio": aspect_ratio,
            "target_minutes": target_minutes,
            "shot_seconds": shot_seconds,
            "resolution": resolution,
            "created_at": time.time(),
        }
        (project_dir / "project.json").write_text(json.dumps(manifest, indent=2))

        kind = "full" if run_full == "on" else "script"
        await _start(slug_clean, kind)
        return RedirectResponse(f"/projects/{slug_clean}", status_code=303)

    @app.get("/projects/{slug}", response_class=HTMLResponse)
    async def project_page(request: Request, slug: str) -> HTMLResponse:
        payload = _project_payload(slug)
        manifest = {}
        manifest_path = PROJECTS_ROOT / slug / "project.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception:
                manifest = {}
        return templates.TemplateResponse(
            request,
            "project.html",
            {
                "data": payload,
                "manifest": manifest,
                "config": _config(),
                "keys": _keys_status(),
            },
        )

    @app.post("/projects/{slug}/run/{kind}")
    async def run_step(slug: str, kind: str) -> JSONResponse:
        if kind not in {"script", "moodboard", "generate", "full"}:
            raise HTTPException(400, "unknown step")
        ok = await _start(slug, kind)
        if not ok:
            return JSONResponse({"ok": False, "error": "already running"}, status_code=409)
        return JSONResponse({"ok": True})

    @app.post("/projects/{slug}/cancel")
    async def cancel(slug: str) -> JSONResponse:
        ok = await runner.cancel(slug)
        return JSONResponse({"ok": ok})

    @app.get("/projects/{slug}/events")
    async def events(slug: str) -> StreamingResponse:
        async def stream():
            async for entry in runner.subscribe(slug):
                if entry is None:
                    yield f"event: end\ndata: {{}}\n\n"
                    return
                yield f"data: {json.dumps(entry)}\n\n"
            yield f"event: end\ndata: {{}}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/projects/{slug}/status")
    async def project_status(slug: str) -> JSONResponse:
        return JSONResponse(_project_payload(slug))

    @app.get("/files/{slug}/{kind}/{filename}")
    async def serve_file(slug: str, kind: str, filename: str) -> FileResponse:
        if kind not in {"keyframes", "moodboards", "clips"}:
            raise HTTPException(404, "unknown asset kind")
        target = (PROJECTS_ROOT / slug / kind / filename).resolve()
        root = (PROJECTS_ROOT / slug / kind).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(400, "bad path") from exc
        if not target.exists():
            raise HTTPException(404, "not found")
        return FileResponse(str(target))

    @app.get("/files/{slug}/edl")
    async def serve_edl(slug: str) -> FileResponse:
        path = PROJECTS_ROOT / slug / "edl.csv"
        if not path.exists():
            raise HTTPException(404, "no EDL yet")
        return FileResponse(str(path), filename=f"{slug}_edl.csv", media_type="text/csv")

    @app.get("/files/{slug}/screenplay")
    async def serve_screenplay(slug: str) -> FileResponse:
        path = PROJECTS_ROOT / slug / "screenplay.json"
        if not path.exists():
            raise HTTPException(404, "no screenplay yet")
        return FileResponse(str(path), media_type="application/json")

    @app.get("/api/quota")
    async def quota() -> JSONResponse:
        cfg = _config()
        if not cfg.AI_AUTO_API_KEY:
            raise HTTPException(400, "ai-auto.io key not set")
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            info = await client.me()
        return JSONResponse(info)

    # ---------------------------------------------------------------- runners

    async def _start(slug: str, kind: str) -> bool:
        manifest = _read_manifest(slug)

        async def fn(ctx: JobContext) -> None:
            cfg = _config()
            if kind in {"script", "full"} and not cfg.ANTHROPIC_API_KEY:
                raise RuntimeError("Anthropic API key missing. Visit /setup.")
            if not cfg.AI_AUTO_API_KEY and kind != "script":
                raise RuntimeError("ai-auto.io API key missing. Visit /setup.")

            aspect_ratio = manifest.get("aspect_ratio") or cfg.DEFAULT_ASPECT_RATIO
            resolution = manifest.get("resolution") or cfg.DEFAULT_RESOLUTION
            shot_seconds = int(manifest.get("shot_seconds") or cfg.DEFAULT_SHOT_SECONDS)
            target_minutes = float(manifest.get("target_minutes") or 1.0)
            logline = manifest.get("logline") or ""

            with capture_pipeline_logs(ctx.log):
                if kind in {"script", "full"}:
                    if not logline:
                        raise RuntimeError("No logline saved for this project. Recreate it from /new.")
                    ctx.log("info", f"Generating screenplay for: {logline!r}")
                    sp = await asyncio.to_thread(
                        script_mod.generate_screenplay,
                        api_key=cfg.ANTHROPIC_API_KEY,
                        logline=logline,
                        aspect_ratio=aspect_ratio,
                        target_minutes=target_minutes,
                        shot_seconds=shot_seconds,
                    )
                    script_mod.save_screenplay(sp, ctx.project_dir)
                    ctx.log("success", f"Screenplay saved: {len(sp.shots)} shots, {len(sp.scenes)} scenes.")

                if kind in {"moodboard", "full"}:
                    sp = script_mod.load_screenplay(ctx.project_dir)
                    image_resolution = getattr(cfg, "DEFAULT_IMAGE_RESOLUTION", "4k")
                    ctx.log("info", "Building moodboards + per-shot keyframes...")
                    async with AIAutoClient(
                        api_key=cfg.AI_AUTO_API_KEY,
                        video_concurrency=cfg.VIDEO_CONCURRENCY,
                        image_concurrency=cfg.IMAGE_CONCURRENCY,
                    ) as client:
                        await mb_mod.build_moodboards(
                            client=client,
                            screenplay=sp,
                            project_dir=ctx.project_dir,
                            moodboard_image_model=cfg.MOODBOARD_IMAGE_MODEL,
                            keyframe_image_model=cfg.KEYFRAME_IMAGE_MODEL,
                            aspect_ratio=aspect_ratio,
                            image_resolution=image_resolution,
                        )
                    ctx.log("success", "Moodboards + keyframes ready.")

                if kind in {"generate", "full"}:
                    sp = script_mod.load_screenplay(ctx.project_dir)
                    keyframes_dir = ctx.project_dir / "keyframes"
                    keyframe_paths = {
                        shot.index: keyframes_dir / f"shot_{shot.index:03d}.jpg"
                        for shot in sp.shots
                    }
                    missing = [i for i, p in keyframe_paths.items() if not p.exists()]
                    if missing:
                        raise RuntimeError(
                            f"Missing keyframes for shots {missing}. Run the moodboard step first."
                        )
                    ctx.log("info", "Animating shots with Seedance 2.0...")
                    async with AIAutoClient(
                        api_key=cfg.AI_AUTO_API_KEY,
                        video_concurrency=cfg.VIDEO_CONCURRENCY,
                        image_concurrency=cfg.IMAGE_CONCURRENCY,
                    ) as client:
                        await gen_mod.animate_all(
                            client=client,
                            screenplay=sp,
                            keyframe_paths=keyframe_paths,
                            project_dir=ctx.project_dir,
                            aspect_ratio=aspect_ratio,
                            resolution=resolution,
                            seconds=shot_seconds,
                            video_model=cfg.VIDEO_MODEL,
                        )
                    edl = gen_mod.write_edl(sp, ctx.project_dir, shot_seconds)
                    ctx.log("success", f"Clips ready. EDL written to {edl.name}.")

        return await runner.start(slug, kind, fn)

    def _read_manifest(slug: str) -> dict[str, Any]:
        path = PROJECTS_ROOT / slug / "project.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return {}
        return {}

    return app


def _replace_assignment_str(source: str, name: str, value: str) -> str:
    safe = value.replace("\\", "\\\\").replace('"', '\\"')
    return _replace_assignment_raw(source, name, f'"{safe}"')


def _replace_assignment_int(source: str, name: str, value: int) -> str:
    return _replace_assignment_raw(source, name, str(int(value)))


def _replace_assignment_raw(source: str, name: str, literal: str) -> str:
    out = []
    replaced = False
    for line in source.splitlines():
        if line.startswith(f"{name} ="):
            out.append(f"{name} = {literal}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{name} = {literal}")
    return "\n".join(out) + ("\n" if source.endswith("\n") else "")
