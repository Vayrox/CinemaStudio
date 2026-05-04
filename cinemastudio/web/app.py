"""FastAPI app for CinemaStudio."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from cinemastudio import settings as settings_mod
from cinemastudio.models import Screenplay
from cinemastudio.pipeline import generate as gen_mod
from cinemastudio.pipeline import moodboard as mb_mod
from cinemastudio.pipeline import script as script_mod
from cinemastudio.providers.ai_auto import AIAutoClient
from cinemastudio.web.jobs import Job, manager

ROOT = Path(__file__).parent.parent.parent
PROJECTS_ROOT = ROOT / "projects"
PROJECTS_ROOT.mkdir(exist_ok=True)
STATIC = Path(__file__).parent / "static"
TEMPLATES = Path(__file__).parent / "templates"

app = FastAPI(title="CinemaStudio")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/files", StaticFiles(directory=PROJECTS_ROOT), name="files")
templates = Jinja2Templates(directory=str(TEMPLATES))


def _project_dir(slug: str) -> Path:
    p = PROJECTS_ROOT / slug
    if ".." in slug or "/" in slug or "\\" in slug:
        raise HTTPException(400, "invalid slug")
    return p


def _load_screenplay(project_dir: Path) -> Screenplay | None:
    fp = project_dir / "screenplay.json"
    if not fp.exists():
        return None
    return Screenplay.model_validate_json(fp.read_text())


def _project_summary(project_dir: Path) -> dict:
    sp = _load_screenplay(project_dir)
    return {
        "slug": project_dir.name,
        "title": sp.title if sp else project_dir.name,
        "logline": sp.logline if sp else "(no screenplay yet)",
        "shot_count": len(sp.shots) if sp else 0,
        "keyframes": len(list((project_dir / "keyframes").glob("*.jpg"))),
        "clips": len(list((project_dir / "clips").glob("*.mp4"))),
    }


def _shot_files(project_dir: Path, shot_index: int) -> dict:
    kf = project_dir / "keyframes" / f"shot_{shot_index:03d}.jpg"
    clip = next(
        (project_dir / "clips").glob(f"shot_{shot_index:03d}_*.mp4"), None
    )
    return {
        "keyframe_url": f"/files/{project_dir.name}/keyframes/{kf.name}" if kf.exists() else None,
        "clip_url": f"/files/{project_dir.name}/clips/{clip.name}" if clip else None,
    }


# ---------- Pages ----------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = settings_mod.get()
    keys_set = bool(cfg.AI_AUTO_API_KEY) and bool(cfg.ANTHROPIC_API_KEY)
    projects = []
    if PROJECTS_ROOT.exists():
        for d in sorted(PROJECTS_ROOT.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                projects.append(_project_summary(d))
    return templates.TemplateResponse(
        request,
        "index.html",
        {"projects": projects, "keys_set": keys_set, "cfg": cfg},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = settings_mod.get()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "ai_auto_set": bool(cfg.AI_AUTO_API_KEY),
            "anthropic_set": bool(cfg.ANTHROPIC_API_KEY),
            "cfg": cfg,
        },
    )


@app.post("/settings")
async def save_settings(
    ai_auto_key: str = Form(""), anthropic_key: str = Form("")
):
    cfg = settings_mod.get()
    settings_mod.write_keys(
        ai_auto_key=ai_auto_key.strip() or cfg.AI_AUTO_API_KEY,
        anthropic_key=anthropic_key.strip() or cfg.ANTHROPIC_API_KEY,
    )
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/projects/new")
async def new_project(
    logline: str = Form(...),
    slug: str = Form(""),
    aspect_ratio: str = Form("9:16"),
    target_minutes: float = Form(1.0),
    shot_seconds: int = Form(15),
):
    cfg = settings_mod.get()
    if not cfg.AI_AUTO_API_KEY or not cfg.ANTHROPIC_API_KEY:
        raise HTTPException(400, "API keys not set. Visit /settings.")
    slug = slug.strip() or _slug(logline)
    project_dir = _project_dir(slug)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Persist the request so the project page can re-use settings.
    (project_dir / "settings.json").write_text(
        json.dumps(
            {
                "aspect_ratio": aspect_ratio,
                "target_minutes": target_minutes,
                "shot_seconds": shot_seconds,
            },
            indent=2,
        )
    )

    async def run(job: Job):
        job.emit(f"Generating screenplay for: {logline}")
        sp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: script_mod.generate_screenplay(
                api_key=cfg.ANTHROPIC_API_KEY,
                logline=logline,
                aspect_ratio=aspect_ratio,
                target_minutes=target_minutes,
                shot_seconds=shot_seconds,
            ),
        )
        script_mod.save_screenplay(sp, project_dir)
        job.emit(f"Screenplay ready: {len(sp.shots)} shots, {len(sp.scenes)} scenes")

    manager.start(project=slug, kind="script", coro_factory=run)
    return RedirectResponse(f"/projects/{slug}", status_code=303)


@app.get("/projects/{slug}", response_class=HTMLResponse)
async def project_page(request: Request, slug: str):
    project_dir = _project_dir(slug)
    if not project_dir.exists():
        raise HTTPException(404, "no such project")
    sp = _load_screenplay(project_dir)
    settings_json = project_dir / "settings.json"
    project_settings = (
        json.loads(settings_json.read_text())
        if settings_json.exists()
        else {"aspect_ratio": "9:16", "target_minutes": 1.0, "shot_seconds": 15}
    )

    shots_view = []
    if sp:
        for shot in sp.shots:
            files = _shot_files(project_dir, shot.index)
            shots_view.append({"shot": shot, **files})

    jobs = manager.list_for_project(slug)
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "slug": slug,
            "screenplay": sp,
            "shots_view": shots_view,
            "project_settings": project_settings,
            "jobs": jobs,
        },
    )


# ---------- Pipeline triggers ----------

@app.post("/projects/{slug}/moodboard")
async def trigger_moodboard(slug: str):
    project_dir = _project_dir(slug)
    sp = _load_screenplay(project_dir)
    if not sp:
        raise HTTPException(400, "no screenplay yet")
    cfg = settings_mod.get()
    settings_path = project_dir / "settings.json"
    project_settings = (
        json.loads(settings_path.read_text()) if settings_path.exists() else {}
    )
    aspect_ratio = project_settings.get("aspect_ratio", cfg.DEFAULT_ASPECT_RATIO)

    async def run(job: Job):
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            mb_mod.console.log = lambda *a, **k: job.emit(_render_console(a))
            await mb_mod.build_moodboards(
                client=client,
                screenplay=sp,
                project_dir=project_dir,
                moodboard_image_model=cfg.MOODBOARD_IMAGE_MODEL,
                keyframe_image_model=cfg.KEYFRAME_IMAGE_MODEL,
                aspect_ratio=aspect_ratio,
                image_resolution=getattr(cfg, "DEFAULT_IMAGE_RESOLUTION", "4k"),
            )
        job.emit("Moodboards + keyframes ready")

    job = manager.start(project=slug, kind="moodboard", coro_factory=run)
    return {"job_id": job.id}


@app.post("/projects/{slug}/generate")
async def trigger_generate(slug: str, only_shots: str = Form("")):
    project_dir = _project_dir(slug)
    sp = _load_screenplay(project_dir)
    if not sp:
        raise HTTPException(400, "no screenplay yet")
    cfg = settings_mod.get()
    settings_path = project_dir / "settings.json"
    project_settings = (
        json.loads(settings_path.read_text()) if settings_path.exists() else {}
    )
    aspect_ratio = project_settings.get("aspect_ratio", cfg.DEFAULT_ASPECT_RATIO)
    shot_seconds = project_settings.get("shot_seconds", cfg.DEFAULT_SHOT_SECONDS)

    keyframes_dir = project_dir / "keyframes"
    keyframe_paths = {
        s.index: keyframes_dir / f"shot_{s.index:03d}.jpg" for s in sp.shots
    }
    only = (
        [int(x) for x in only_shots.split(",") if x.strip().isdigit()]
        if only_shots
        else None
    )

    async def run(job: Job):
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            gen_mod.console.log = lambda *a, **k: job.emit(_render_console(a))
            await gen_mod.animate_all(
                client=client,
                screenplay=sp,
                keyframe_paths=keyframe_paths,
                project_dir=project_dir,
                aspect_ratio=aspect_ratio,
                resolution=cfg.DEFAULT_RESOLUTION,
                seconds=shot_seconds,
                video_model=cfg.VIDEO_MODEL,
                enable_audio=getattr(cfg, "ENABLE_NATIVE_AUDIO", True),
                overwrite=bool(only),
                only_shots=only,
            )
        gen_mod.write_edl(sp, project_dir, shot_seconds)
        job.emit("Clips ready, EDL written")

    job = manager.start(project=slug, kind="generate", coro_factory=run)
    return {"job_id": job.id}


@app.post("/projects/{slug}/shots/{shot_index}/regen-keyframe")
async def regen_keyframe(slug: str, shot_index: int):
    project_dir = _project_dir(slug)
    sp = _load_screenplay(project_dir)
    if not sp:
        raise HTTPException(400, "no screenplay")
    shot = next((s for s in sp.shots if s.index == shot_index), None)
    if not shot:
        raise HTTPException(404, "no such shot")
    cfg = settings_mod.get()
    settings_path = project_dir / "settings.json"
    project_settings = (
        json.loads(settings_path.read_text()) if settings_path.exists() else {}
    )
    aspect_ratio = project_settings.get("aspect_ratio", cfg.DEFAULT_ASPECT_RATIO)
    out = project_dir / "keyframes" / f"shot_{shot_index:03d}.jpg"

    async def run(job: Job):
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            job.emit(f"Regenerating keyframe for shot {shot_index}")
            await mb_mod._generate_keyframe(  # type: ignore[attr-defined]
                client,
                shot=shot,
                screenplay=sp,
                image_model=cfg.KEYFRAME_IMAGE_MODEL,
                aspect_ratio=aspect_ratio,
                resolution=getattr(cfg, "DEFAULT_IMAGE_RESOLUTION", "4k"),
                out_path=out,
            )
        job.emit(f"Shot {shot_index} keyframe ready")

    job = manager.start(project=slug, kind="regen-keyframe", coro_factory=run)
    return {"job_id": job.id}


@app.post("/projects/{slug}/shots/{shot_index}/regen-clip")
async def regen_clip(slug: str, shot_index: int):
    project_dir = _project_dir(slug)
    sp = _load_screenplay(project_dir)
    if not sp:
        raise HTTPException(400, "no screenplay")
    shot = next((s for s in sp.shots if s.index == shot_index), None)
    if not shot:
        raise HTTPException(404, "no such shot")
    cfg = settings_mod.get()
    settings_path = project_dir / "settings.json"
    project_settings = (
        json.loads(settings_path.read_text()) if settings_path.exists() else {}
    )
    aspect_ratio = project_settings.get("aspect_ratio", cfg.DEFAULT_ASPECT_RATIO)
    shot_seconds = project_settings.get("shot_seconds", cfg.DEFAULT_SHOT_SECONDS)

    keyframes_dir = project_dir / "keyframes"
    keyframe_path = keyframes_dir / f"shot_{shot_index:03d}.jpg"
    if not keyframe_path.exists():
        raise HTTPException(400, "keyframe missing — regenerate keyframe first")

    async def run(job: Job):
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            gen_mod.console.log = lambda *a, **k: job.emit(_render_console(a))
            await gen_mod._animate_one(  # type: ignore[attr-defined]
                client,
                shot=shot,
                screenplay=sp,
                keyframe_path=keyframe_path,
                project_dir=project_dir,
                aspect_ratio=aspect_ratio,
                resolution=cfg.DEFAULT_RESOLUTION,
                seconds=shot_seconds,
                video_model=cfg.VIDEO_MODEL,
                enable_audio=getattr(cfg, "ENABLE_NATIVE_AUDIO", True),
                overwrite=True,
            )
        job.emit(f"Shot {shot_index} clip ready")

    job = manager.start(project=slug, kind="regen-clip", coro_factory=run)
    return {"job_id": job.id}


# ---------- SSE log stream ----------

@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404)
    queue = await manager.subscribe(job)

    async def gen():
        while True:
            line = await queue.get()
            if line is None:
                yield {"event": "end", "data": job.status}
                return
            yield {
                "event": "log",
                "data": json.dumps(
                    {"ts": line.ts, "level": line.level, "message": line.message}
                ),
            }

    return EventSourceResponse(gen())


@app.get("/projects/{slug}/jobs")
async def list_jobs(slug: str):
    return [
        {"id": j.id, "kind": j.kind, "status": j.status, "created_at": j.created_at}
        for j in manager.list_for_project(slug)
    ]


# ---------- helpers ----------

def _slug(text: str, max_len: int = 40) -> str:
    import re

    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


def _render_console(args: tuple) -> str:
    """Best-effort rendering of rich console.log args -> plain text."""
    out = []
    for a in args:
        if hasattr(a, "plain"):
            out.append(a.plain)
        else:
            out.append(str(a))
    text = " ".join(out)
    # strip rich markup like [magenta]
    import re

    return re.sub(r"\[/?[^\]]+\]", "", text)
