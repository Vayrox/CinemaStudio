"""FastAPI app for CinemaStudio."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cinemastudio import library, settings
from cinemastudio.pipeline import character as char_mod
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

    # ---------------------------------------------------------------- character helpers

    def _safe_char_name(name: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
        return s[:48].rstrip("-") or "character"

    def _project_char_path(project_dir: Path, name: str) -> Path:
        return project_dir / "characters" / f"{_safe_char_name(name)}.jpg"

    def _read_char_json(project_dir: Path) -> list[dict[str, Any]]:
        path = project_dir / "characters.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def _write_char_json(project_dir: Path, chars: list[dict[str, Any]]) -> None:
        (project_dir / "characters.json").write_text(json.dumps(chars, indent=2))

    def _sync_project_characters(project_dir: Path) -> list[dict[str, Any]]:
        """Merge characters from screenplay.json into characters.json without
        overwriting user-edited descriptions. Returns the merged list."""
        chars = _read_char_json(project_dir)
        sp_path = project_dir / "screenplay.json"
        if sp_path.exists():
            try:
                sp = json.loads(sp_path.read_text())
                existing = {c["name"].strip().lower(): c for c in chars}
                for c in sp.get("characters", []) or []:
                    key = c["name"].strip().lower()
                    if key not in existing:
                        chars.append({
                            "name": c["name"],
                            "description": c.get("description", ""),
                            "source": "script",
                        })
            except Exception:
                pass
        _write_char_json(project_dir, chars)
        return chars

    def _shot_counts(project_dir: Path) -> dict[str, int]:
        """Map character name (lower) → number of shots they appear in."""
        sp = project_dir / "screenplay.json"
        if not sp.exists():
            return {}
        try:
            data = json.loads(sp.read_text())
        except Exception:
            return {}
        counts: dict[str, int] = {}
        for shot in data.get("shots", []) or []:
            for n in shot.get("character_names", []) or []:
                key = n.strip().lower()
                if key:
                    counts[key] = counts.get(key, 0) + 1
        return counts

    def _list_project_characters(slug: str) -> list[dict[str, Any]]:
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            return []
        chars = _sync_project_characters(project_dir)
        counts = _shot_counts(project_dir)
        max_count = max(counts.values()) if counts else 0
        out = []
        for c in chars:
            entry = dict(c)
            key = c["name"].strip().lower()
            entry["safe_name"] = _safe_char_name(c["name"])
            entry["has_portrait"] = _project_char_path(project_dir, c["name"]).exists()
            entry["shot_count"] = counts.get(key, 0)
            entry["is_lead"] = max_count > 0 and entry["shot_count"] == max_count
            out.append(entry)
        # Order: leads first, then by shot count desc, then alphabetic.
        out.sort(key=lambda e: (-int(e["is_lead"]), -e["shot_count"], e["name"].lower()))
        return out

    def _rename_in_screenplay(project_dir: Path, old_name: str, new_name: str) -> None:
        """Rename `old_name` to `new_name` everywhere in screenplay.json."""
        sp_path = project_dir / "screenplay.json"
        if not sp_path.exists():
            return
        try:
            data = json.loads(sp_path.read_text())
        except Exception:
            return
        old_lower = old_name.strip().lower()
        if not old_lower or old_lower == new_name.strip().lower():
            return
        pattern = re.compile(rf"\b{re.escape(old_name)}\b", flags=re.IGNORECASE)
        for c in data.get("characters", []) or []:
            if (c.get("name") or "").strip().lower() == old_lower:
                c["name"] = new_name
        for shot in data.get("shots", []) or []:
            names = shot.get("character_names") or []
            shot["character_names"] = [
                new_name if n.strip().lower() == old_lower else n for n in names
            ]
            for field in ("description", "motion_prompt", "keyframe_prompt"):
                if field in shot and isinstance(shot[field], str):
                    shot[field] = pattern.sub(lambda _m: new_name, shot[field])
        for scene in data.get("scenes", []) or []:
            for field in ("title", "summary", "mood", "location"):
                if field in scene and isinstance(scene[field], str):
                    scene[field] = pattern.sub(lambda _m: new_name, scene[field])
        sp_path.write_text(json.dumps(data, indent=2))

    def _rename_in_characters_json(project_dir: Path, old_name: str, new_name: str) -> None:
        chars = _read_char_json(project_dir)
        old_lower = old_name.strip().lower()
        for c in chars:
            if (c.get("name") or "").strip().lower() == old_lower:
                c["name"] = new_name
        _write_char_json(project_dir, chars)

    def _rename_portrait_file(project_dir: Path, old_name: str, new_name: str) -> None:
        if _safe_char_name(old_name) == _safe_char_name(new_name):
            return
        old_path = _project_char_path(project_dir, old_name)
        new_path = _project_char_path(project_dir, new_name)
        if old_path.exists():
            old_path.rename(new_path)

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
            "characters": _list_project_characters(slug),
            "has_edl": (project_dir / "edl.csv").exists(),
            "job": runner.status(slug),
        }

    def _config():
        return settings.get()

    def _keys_status() -> dict[str, Any]:
        cfg = _config()
        provider, key = settings.script_provider_key(cfg)
        return {
            "ai_auto": bool(getattr(cfg, "AI_AUTO_API_KEY", "")),
            "anthropic": bool(getattr(cfg, "ANTHROPIC_API_KEY", "")),
            "google": bool(getattr(cfg, "GOOGLE_API_KEY", "")),
            "script_provider": provider,
            "script_provider_ready": bool(key),
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
                "needs_setup": not (keys["ai_auto"] and keys["script_provider_ready"]),
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
        google_api_key: str = Form(""),
        script_provider: str = Form("google"),
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
        google = google_api_key.strip() or getattr(cfg, "GOOGLE_API_KEY", "")
        provider = script_provider if script_provider in ("anthropic", "google") else "google"
        settings.write_keys(
            ai_auto_key=ai_auto,
            anthropic_key=anthropic,
            google_key=google,
            script_provider=provider,
        )

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
        if kind not in {"keyframes", "moodboards", "clips", "characters"}:
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

    # ---------------------------------------------------------------- idea generator

    @app.post("/api/loglines")
    async def loglines(seed: str = Form(...), count: int = Form(10)) -> JSONResponse:
        seed = seed.strip()
        if not seed:
            raise HTTPException(400, "seed is empty")
        cfg = _config()
        provider, key = settings.script_provider_key(cfg)
        if not key:
            label = "Google AI Studio" if provider == "google" else "Anthropic"
            raise HTTPException(400, f"{label} API key missing. Visit /setup.")
        loglines = await asyncio.to_thread(
            script_mod.generate_loglines,
            provider=provider,
            api_key=key,
            seed=seed,
            count=max(1, min(20, int(count))),
        )
        return JSONResponse({"loglines": loglines})

    # ---------------------------------------------------------------- project characters

    async def _generate_project_portrait(
        project_dir: Path, character: dict[str, Any], cfg
    ) -> Path:
        out = _project_char_path(project_dir, character["name"])
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            await char_mod.generate_portrait(
                client,
                name=character["name"],
                description=character.get("description", ""),
                image_model=cfg.CHARACTER_IMAGE_MODEL,
                out_path=out,
            )
        return out

    @app.get("/api/projects/{slug}/characters")
    async def list_project_characters(slug: str) -> JSONResponse:
        if not (PROJECTS_ROOT / slug).exists():
            raise HTTPException(404, "project not found")
        return JSONResponse({"characters": _list_project_characters(slug)})

    @app.post("/api/projects/{slug}/characters")
    async def add_project_character(
        slug: str,
        name: str = Form(...),
        description: str = Form(""),
    ) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            raise HTTPException(404, "project not found")
        name = name.strip()
        if not name:
            raise HTTPException(400, "name required")
        chars = _read_char_json(project_dir)
        if any(c["name"].strip().lower() == name.lower() for c in chars):
            raise HTTPException(409, "character already exists")
        chars.append({"name": name, "description": description.strip(), "source": "manual"})
        _write_char_json(project_dir, chars)
        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    @app.post("/api/projects/{slug}/characters/{char_slug}/update")
    async def update_project_character(
        slug: str,
        char_slug: str,
        name: str = Form(""),
        description: str = Form(""),
    ) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        chars = _read_char_json(project_dir)
        match = next((c for c in chars if _safe_char_name(c["name"]) == char_slug), None)
        if not match:
            raise HTTPException(404, "character not found")
        old_name = match["name"]
        if name.strip():
            match["name"] = name.strip()
        if description is not None:
            match["description"] = description.strip()
        _write_char_json(project_dir, chars)
        # If name changed, rename the portrait file too.
        if name.strip() and _safe_char_name(name) != _safe_char_name(old_name):
            old_path = _project_char_path(project_dir, old_name)
            new_path = _project_char_path(project_dir, name)
            if old_path.exists():
                old_path.rename(new_path)
        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    @app.delete("/api/projects/{slug}/characters/{char_slug}")
    async def delete_project_character(slug: str, char_slug: str) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        chars = _read_char_json(project_dir)
        before = len(chars)
        chars = [c for c in chars if _safe_char_name(c["name"]) != char_slug]
        if len(chars) == before:
            raise HTTPException(404, "character not found")
        _write_char_json(project_dir, chars)
        portrait = project_dir / "characters" / f"{char_slug}.jpg"
        if portrait.exists():
            portrait.unlink()
        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    @app.post("/api/projects/{slug}/characters/{char_slug}/portrait")
    async def regen_project_portrait(slug: str, char_slug: str) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        chars = _read_char_json(project_dir)
        match = next((c for c in chars if _safe_char_name(c["name"]) == char_slug), None)
        if not match:
            raise HTTPException(404, "character not found")
        cfg = _config()
        if not cfg.AI_AUTO_API_KEY:
            raise HTTPException(400, "ai-auto.io key not set")
        await _generate_project_portrait(project_dir, match, cfg)
        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    @app.post("/api/projects/{slug}/characters/{char_slug}/upload")
    async def upload_project_portrait(
        slug: str,
        char_slug: str,
        file: UploadFile = File(...),
    ) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        chars = _read_char_json(project_dir)
        match = next((c for c in chars if _safe_char_name(c["name"]) == char_slug), None)
        if not match:
            raise HTTPException(404, "character not found")
        out = _project_char_path(project_dir, match["name"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(await file.read())
        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    @app.post("/api/projects/{slug}/characters/import/{lib_slug}")
    async def import_library_character(slug: str, lib_slug: str) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            raise HTTPException(404, "project not found")
        lib_char = library.get_character(lib_slug)
        if not lib_char:
            raise HTTPException(404, "library character not found")
        chars = _read_char_json(project_dir)
        if not any(c["name"].strip().lower() == lib_char["name"].strip().lower() for c in chars):
            chars.append({
                "name": lib_char["name"],
                "description": lib_char.get("description", ""),
                "source": "imported",
            })
            _write_char_json(project_dir, chars)
        # Copy portrait into project.
        src = library.portrait_path(lib_slug)
        if src.exists():
            dest = _project_char_path(project_dir, lib_char["name"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    @app.post("/api/projects/{slug}/regenerate/moodboard/{scene_idx}")
    async def regenerate_moodboard(slug: str, scene_idx: int) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            raise HTTPException(404, "project not found")
        cfg = _config()
        if not cfg.AI_AUTO_API_KEY:
            raise HTTPException(400, "ai-auto.io key not set")
        sp = script_mod.load_screenplay(project_dir)
        try:
            async with AIAutoClient(
                api_key=cfg.AI_AUTO_API_KEY,
                video_concurrency=cfg.VIDEO_CONCURRENCY,
                image_concurrency=cfg.IMAGE_CONCURRENCY,
            ) as client:
                out = await mb_mod.regenerate_scene_moodboard(
                    client=client,
                    screenplay=sp,
                    project_dir=project_dir,
                    scene_idx=scene_idx,
                    moodboard_image_model=cfg.MOODBOARD_IMAGE_MODEL,
                    image_resolution=getattr(cfg, "DEFAULT_IMAGE_RESOLUTION", "4k"),
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc
        return JSONResponse({"ok": True, "filename": out.name})

    @app.post("/api/projects/{slug}/regenerate/keyframe/{shot_idx}")
    async def regenerate_keyframe(slug: str, shot_idx: int) -> JSONResponse:
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            raise HTTPException(404, "project not found")
        cfg = _config()
        if not cfg.AI_AUTO_API_KEY:
            raise HTTPException(400, "ai-auto.io key not set")
        sp = script_mod.load_screenplay(project_dir)
        manifest = _read_manifest(slug)
        aspect_ratio = manifest.get("aspect_ratio") or cfg.DEFAULT_ASPECT_RATIO
        try:
            async with AIAutoClient(
                api_key=cfg.AI_AUTO_API_KEY,
                video_concurrency=cfg.VIDEO_CONCURRENCY,
                image_concurrency=cfg.IMAGE_CONCURRENCY,
            ) as client:
                out = await mb_mod.regenerate_shot_keyframe(
                    client=client,
                    screenplay=sp,
                    project_dir=project_dir,
                    shot_index=shot_idx,
                    keyframe_image_model=cfg.KEYFRAME_IMAGE_MODEL,
                    aspect_ratio=aspect_ratio,
                    image_resolution=getattr(cfg, "DEFAULT_IMAGE_RESOLUTION", "4k"),
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc
        return JSONResponse({"ok": True, "filename": out.name})

    @app.post("/api/projects/{slug}/cast/update")
    async def cast_update(
        slug: str,
        old_name: str = Form(...),
        new_name: str = Form(""),
        lib_slug: str = Form(""),
    ) -> JSONResponse:
        """Rename a character in the screenplay + characters.json + portrait file.

        If `lib_slug` is provided, the character's name becomes the library
        character's name (overriding `new_name`) and the library sheet is copied
        into the project as that character's portrait.
        """
        project_dir = PROJECTS_ROOT / slug
        if not project_dir.exists():
            raise HTTPException(404, "project not found")
        old_name = old_name.strip()
        if not old_name:
            raise HTTPException(400, "old_name required")
        lib_char = None
        if lib_slug:
            lib_char = library.get_character(lib_slug)
            if not lib_char:
                raise HTTPException(404, "library character not found")
            new_name = lib_char["name"]
        new_name = (new_name or "").strip()
        if not new_name:
            raise HTTPException(400, "new_name required")

        # Apply rename across everything.
        _rename_in_screenplay(project_dir, old_name, new_name)
        _rename_in_characters_json(project_dir, old_name, new_name)
        _rename_portrait_file(project_dir, old_name, new_name)

        # If we replaced from library, also copy description + portrait sheet.
        if lib_char:
            chars = _read_char_json(project_dir)
            for c in chars:
                if (c.get("name") or "").strip().lower() == new_name.strip().lower():
                    if lib_char.get("description"):
                        c["description"] = lib_char["description"]
                    c["source"] = "imported"
            _write_char_json(project_dir, chars)
            src = library.portrait_path(lib_slug)
            if src.exists():
                dest = _project_char_path(project_dir, new_name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())

        return JSONResponse({"ok": True, "characters": _list_project_characters(slug)})

    # ---------------------------------------------------------------- library

    @app.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "library.html",
            {
                "characters": library.list_library(),
                "keys": _keys_status(),
                "config": _config(),
            },
        )

    @app.get("/api/library")
    async def api_library_list() -> JSONResponse:
        return JSONResponse({"characters": library.list_library()})

    @app.post("/api/library")
    async def api_library_add(
        name: str = Form(...),
        description: str = Form(""),
    ) -> JSONResponse:
        name = name.strip()
        if not name:
            raise HTTPException(400, "name required")
        slug = library.unique_slug(name)
        library.save_character(slug, name, description.strip(), source="manual")
        return JSONResponse({"ok": True, "slug": slug, "characters": library.list_library()})

    @app.post("/api/library/{lib_slug}/update")
    async def api_library_update(
        lib_slug: str,
        name: str = Form(""),
        description: str = Form(""),
    ) -> JSONResponse:
        try:
            library.update_character(
                lib_slug,
                name=name.strip() or None,
                description=description.strip(),
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, "not found") from exc
        return JSONResponse({"ok": True, "characters": library.list_library()})

    @app.delete("/api/library/{lib_slug}")
    async def api_library_delete(lib_slug: str) -> JSONResponse:
        library.delete_character(lib_slug)
        return JSONResponse({"ok": True, "characters": library.list_library()})

    @app.post("/api/library/{lib_slug}/portrait")
    async def api_library_regen(lib_slug: str) -> JSONResponse:
        char = library.get_character(lib_slug)
        if not char:
            raise HTTPException(404, "not found")
        cfg = _config()
        if not cfg.AI_AUTO_API_KEY:
            raise HTTPException(400, "ai-auto.io key not set")
        out = library.portrait_path(lib_slug)
        out.parent.mkdir(parents=True, exist_ok=True)
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            await char_mod.generate_portrait(
                client,
                name=char["name"],
                description=char.get("description", ""),
                image_model=cfg.CHARACTER_IMAGE_MODEL,
                out_path=out,
            )
        # Mark as generated source.
        library.update_character(lib_slug)  # touches meta only
        return JSONResponse({"ok": True, "characters": library.list_library()})

    @app.post("/api/library/{lib_slug}/upload")
    async def api_library_upload(
        lib_slug: str,
        file: UploadFile = File(...),
    ) -> JSONResponse:
        if not library.get_character(lib_slug):
            raise HTTPException(404, "not found")
        library.write_portrait_bytes(lib_slug, await file.read())
        return JSONResponse({"ok": True, "characters": library.list_library()})

    @app.get("/library-files/{lib_slug}/portrait")
    async def serve_library_portrait(lib_slug: str) -> FileResponse:
        p = library.portrait_path(lib_slug)
        if not p.exists():
            raise HTTPException(404, "no portrait")
        return FileResponse(str(p))

    @app.post("/api/library/from-url")
    async def api_library_from_url(
        name: str = Form(...),
        description: str = Form(""),
        url: str = Form(...),
        mode: str = Form("as-is"),
    ) -> JSONResponse:
        from cinemastudio.providers.ai_auto import bytes_to_data_url
        from cinemastudio.web.url_image import fetch_image_from_url

        name = name.strip()
        if not name:
            raise HTTPException(400, "name required")
        try:
            data, mime = await fetch_image_from_url(url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Could not fetch image: {exc}") from exc

        slug = library.unique_slug(name)
        library.save_character(slug, name, description.strip(), source="imported")

        if mode == "generate":
            cfg = _config()
            if not cfg.AI_AUTO_API_KEY:
                # Roll back the meta entry so we don't orphan it.
                library.delete_character(slug)
                raise HTTPException(400, "ai-auto.io key not set")
            ref = bytes_to_data_url(data, mime=mime)
            out = library.portrait_path(slug)
            try:
                async with AIAutoClient(
                    api_key=cfg.AI_AUTO_API_KEY,
                    video_concurrency=cfg.VIDEO_CONCURRENCY,
                    image_concurrency=cfg.IMAGE_CONCURRENCY,
                ) as client:
                    from cinemastudio.pipeline.character import SHEET_PROMPT_TEMPLATE
                    prompt = SHEET_PROMPT_TEMPLATE.format(
                        name=name,
                        description=description or "match the reference image exactly",
                    )
                    gen = await client.generate_image(
                        prompt=prompt,
                        image_model=cfg.CHARACTER_IMAGE_MODEL,
                        aspect_ratio="16:9",
                        resolution="4k",
                        reference_images=[ref],
                    )
                    out.parent.mkdir(parents=True, exist_ok=True)
                    await client.download_image(gen.id, out)
            except Exception:
                library.delete_character(slug)
                raise
        else:
            # Use the fetched image as-is.
            library.write_portrait_bytes(slug, data)

        return JSONResponse({"ok": True, "slug": slug, "characters": library.list_library()})

    # ---------------------------------------------------------------- runners

    async def _start(slug: str, kind: str) -> bool:
        manifest = _read_manifest(slug)

        async def fn(ctx: JobContext) -> None:
            cfg = _config()
            provider, provider_key = settings.script_provider_key(cfg)
            if kind in {"script", "full"} and not provider_key:
                label = "Google AI Studio" if provider == "google" else "Anthropic"
                raise RuntimeError(f"{label} API key missing. Visit /setup.")
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
                    ctx.log("info", f"Generating screenplay via {provider} for: {logline!r}")
                    sp = await asyncio.to_thread(
                        script_mod.generate_screenplay,
                        provider=provider,
                        api_key=provider_key,
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
