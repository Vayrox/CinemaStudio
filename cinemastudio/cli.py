"""CinemaStudio CLI."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from cinemastudio import settings
from cinemastudio.pipeline import generate as gen_mod
from cinemastudio.pipeline import moodboard as mb_mod
from cinemastudio.pipeline import script as script_mod
from cinemastudio.providers.ai_auto import AIAutoClient

console = Console()
PROJECTS_ROOT = Path(__file__).parent.parent / "projects"


def _slug_from_logline(logline: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", logline.lower()).strip("-")
    return s[:40].rstrip("-") or "untitled"


def _project_dir(slug: str) -> Path:
    p = PROJECTS_ROOT / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


@click.group()
def cli() -> None:
    """CinemaStudio — AI short-film pipeline using Seedance 2.0 via ai-auto.io."""


@cli.command()
def setup() -> None:
    """Prompt for and save your API keys (gitignored)."""
    settings.ensure_keys(require_anthropic=True)
    console.print(Panel.fit("Setup complete.", style="green"))


@cli.command()
@click.argument("logline")
@click.option("--slug", default=None, help="Project folder name (default: derived from logline).")
@click.option("--ratio", "aspect_ratio", default=None, help="9:16 or 16:9.")
@click.option("--minutes", "target_minutes", type=float, default=1.0, help="Target film length.")
@click.option("--seconds", "shot_seconds", type=int, default=None, help="Per-shot duration (5/10/15).")
def script(
    logline: str,
    slug: str | None,
    aspect_ratio: str | None,
    target_minutes: float,
    shot_seconds: int | None,
) -> None:
    """Generate screenplay + shot list from a logline."""
    settings.ensure_keys(require_anthropic=True)
    cfg = settings.get()
    aspect_ratio = aspect_ratio or cfg.DEFAULT_ASPECT_RATIO
    shot_seconds = shot_seconds or cfg.DEFAULT_SHOT_SECONDS
    slug = slug or _slug_from_logline(logline)
    project_dir = _project_dir(slug)

    console.print(f"[bold]Generating screenplay[/bold] -> projects/{slug}/screenplay.json")
    sp = script_mod.generate_screenplay(
        api_key=cfg.ANTHROPIC_API_KEY,
        logline=logline,
        aspect_ratio=aspect_ratio,
        target_minutes=target_minutes,
        shot_seconds=shot_seconds,
    )
    path = script_mod.save_screenplay(sp, project_dir)
    console.print(f"[green]✓[/green] {len(sp.shots)} shots, {len(sp.scenes)} scenes")
    console.print(f"  saved to {path}")


@cli.command()
@click.option("--slug", required=True)
@click.option("--ratio", "aspect_ratio", default=None)
def moodboard(slug: str, aspect_ratio: str | None) -> None:
    """Generate moodboards + per-shot keyframes from an existing screenplay."""
    settings.ensure_keys(require_anthropic=False)
    cfg = settings.get()
    aspect_ratio = aspect_ratio or cfg.DEFAULT_ASPECT_RATIO
    project_dir = _project_dir(slug)
    sp = script_mod.load_screenplay(project_dir)
    asyncio.run(_run_moodboard(sp, project_dir, aspect_ratio, cfg))


async def _run_moodboard(sp, project_dir, aspect_ratio, cfg) -> None:
    image_resolution = getattr(cfg, "DEFAULT_IMAGE_RESOLUTION", "4k")
    async with AIAutoClient(
        api_key=cfg.AI_AUTO_API_KEY,
        video_concurrency=cfg.VIDEO_CONCURRENCY,
        image_concurrency=cfg.IMAGE_CONCURRENCY,
    ) as client:
        await mb_mod.build_moodboards(
            client=client,
            screenplay=sp,
            project_dir=project_dir,
            moodboard_image_model=cfg.MOODBOARD_IMAGE_MODEL,
            keyframe_image_model=cfg.KEYFRAME_IMAGE_MODEL,
            aspect_ratio=aspect_ratio,
            image_resolution=image_resolution,
        )
    console.print("[green]✓ Moodboards + keyframes ready.[/green]")


@cli.command()
@click.option("--slug", required=True)
@click.option("--ratio", "aspect_ratio", default=None)
@click.option("--resolution", default=None, help="720p or 4k.")
@click.option("--seconds", "shot_seconds", type=int, default=None, help="5, 10, or 15.")
def generate(
    slug: str,
    aspect_ratio: str | None,
    resolution: str | None,
    shot_seconds: int | None,
) -> None:
    """Animate keyframes into Seedance 2.0 clips."""
    settings.ensure_keys(require_anthropic=False)
    cfg = settings.get()
    aspect_ratio = aspect_ratio or cfg.DEFAULT_ASPECT_RATIO
    resolution = resolution or cfg.DEFAULT_RESOLUTION
    shot_seconds = shot_seconds or cfg.DEFAULT_SHOT_SECONDS
    project_dir = _project_dir(slug)
    sp = script_mod.load_screenplay(project_dir)

    keyframes_dir = project_dir / "keyframes"
    keyframe_paths = {
        shot.index: keyframes_dir / f"shot_{shot.index:03d}.jpg" for shot in sp.shots
    }
    missing = [i for i, p in keyframe_paths.items() if not p.exists()]
    if missing:
        console.print(
            f"[red]Missing keyframes for shots {missing}. Run `cinema moodboard --slug {slug}` first.[/red]"
        )
        raise SystemExit(2)

    asyncio.run(
        _run_generate(sp, keyframe_paths, project_dir, aspect_ratio, resolution, shot_seconds, cfg)
    )


async def _run_generate(
    sp, keyframe_paths, project_dir, aspect_ratio, resolution, shot_seconds, cfg
) -> None:
    async with AIAutoClient(
        api_key=cfg.AI_AUTO_API_KEY,
        video_concurrency=cfg.VIDEO_CONCURRENCY,
        image_concurrency=cfg.IMAGE_CONCURRENCY,
    ) as client:
        await gen_mod.animate_all(
            client=client,
            screenplay=sp,
            keyframe_paths=keyframe_paths,
            project_dir=project_dir,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            seconds=shot_seconds,
            video_model=cfg.VIDEO_MODEL,
        )
    edl = gen_mod.write_edl(sp, project_dir, shot_seconds)
    console.print(f"[green]✓ Clips ready in projects/{project_dir.name}/clips/[/green]")
    console.print(f"  EDL: {edl}")


@cli.command()
@click.argument("logline")
@click.option("--slug", default=None)
@click.option("--ratio", "aspect_ratio", default=None)
@click.option("--minutes", "target_minutes", type=float, default=1.0)
@click.option("--resolution", default=None)
@click.option("--seconds", "shot_seconds", type=int, default=None)
def make(
    logline: str,
    slug: str | None,
    aspect_ratio: str | None,
    target_minutes: float,
    resolution: str | None,
    shot_seconds: int | None,
) -> None:
    """Run the full pipeline: script -> moodboards -> animate."""
    settings.ensure_keys(require_anthropic=True)
    cfg = settings.get()
    aspect_ratio = aspect_ratio or cfg.DEFAULT_ASPECT_RATIO
    resolution = resolution or cfg.DEFAULT_RESOLUTION
    shot_seconds = shot_seconds or cfg.DEFAULT_SHOT_SECONDS
    slug = slug or _slug_from_logline(logline)
    project_dir = _project_dir(slug)

    console.print(Panel.fit(f"Project: [bold]{slug}[/bold]\nLogline: {logline}"))
    sp = script_mod.generate_screenplay(
        api_key=cfg.ANTHROPIC_API_KEY,
        logline=logline,
        aspect_ratio=aspect_ratio,
        target_minutes=target_minutes,
        shot_seconds=shot_seconds,
    )
    script_mod.save_screenplay(sp, project_dir)
    console.print(f"[green]✓[/green] Screenplay: {len(sp.shots)} shots, {len(sp.scenes)} scenes")

    asyncio.run(_run_moodboard(sp, project_dir, aspect_ratio, cfg))

    keyframes_dir = project_dir / "keyframes"
    keyframe_paths = {
        shot.index: keyframes_dir / f"shot_{shot.index:03d}.jpg" for shot in sp.shots
    }
    asyncio.run(
        _run_generate(sp, keyframe_paths, project_dir, aspect_ratio, resolution, shot_seconds, cfg)
    )


@cli.command()
def quota() -> None:
    """Show your ai-auto.io quota and plan."""
    settings.ensure_keys(require_anthropic=False)
    cfg = settings.get()

    async def _go():
        async with AIAutoClient(
            api_key=cfg.AI_AUTO_API_KEY,
            video_concurrency=cfg.VIDEO_CONCURRENCY,
            image_concurrency=cfg.IMAGE_CONCURRENCY,
        ) as client:
            return await client.me()

    info = asyncio.run(_go())
    console.print(info)


if __name__ == "__main__":
    cli()
