"""Per-shot Seedance 2.0 video generation."""
from __future__ import annotations

import asyncio
import csv
import re
from pathlib import Path

from rich.console import Console

from cinemastudio.models import Screenplay, Shot
from cinemastudio.providers.ai_auto import AIAutoClient, file_to_data_url

console = Console()


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return s[:max_len].rstrip("_")


def _clip_path(project_dir: Path, shot: Shot) -> Path:
    desc = _slug(shot.description)
    return project_dir / "clips" / f"shot_{shot.index:03d}_scene{shot.scene:02d}_{desc}.mp4"


async def _animate_one(
    client: AIAutoClient,
    *,
    shot: Shot,
    screenplay: Screenplay,
    keyframe_path: Path,
    project_dir: Path,
    aspect_ratio: str,
    resolution: str,
    seconds: int,
    video_model: str,
) -> Path:
    out = _clip_path(project_dir, shot)
    if out.exists() and out.stat().st_size > 0:
        console.log(f"[dim]Skip[/dim] shot {shot.index} (already rendered)")
        return out

    style_suffix = f" Style: {screenplay.style}."
    prompt = shot.motion_prompt + style_suffix
    frame_data_url = file_to_data_url(keyframe_path)

    console.log(f"[magenta]Animate[/magenta] shot {shot.index} ({seconds}s, {aspect_ratio}, {resolution})")
    gen = await client.generate_video(
        prompt=prompt,
        model=video_model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        seconds=seconds,
        i2v_frame_start=frame_data_url,
    )
    await client.download_video(gen.id, out)
    console.log(f"[green]✓[/green] shot {shot.index} -> {out.name}")
    return out


async def animate_all(
    *,
    client: AIAutoClient,
    screenplay: Screenplay,
    keyframe_paths: dict[int, Path],
    project_dir: Path,
    aspect_ratio: str,
    resolution: str,
    seconds: int,
    video_model: str,
) -> list[Path]:
    tasks = []
    for shot in screenplay.shots:
        kf = keyframe_paths.get(shot.index)
        if kf is None:
            console.log(f"[yellow]Warn[/yellow] shot {shot.index}: no keyframe, skipping")
            continue
        tasks.append(
            _animate_one(
                client,
                shot=shot,
                screenplay=screenplay,
                keyframe_path=kf,
                project_dir=project_dir,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                seconds=seconds,
                video_model=video_model,
            )
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    paths: list[Path] = []
    failures: list[str] = []
    for shot, r in zip([s for s in screenplay.shots if s.index in keyframe_paths], results):
        if isinstance(r, BaseException):
            failures.append(f"shot {shot.index} ({type(r).__name__}: {r})")
            continue
        paths.append(r)
    if failures:
        console.log("[yellow]Clips skipped:[/yellow] " + "; ".join(failures))
    return paths


def write_edl(screenplay: Screenplay, project_dir: Path, seconds: int) -> Path:
    """Write a CSV editorial decision list importable into most NLEs."""
    out = project_dir / "edl.csv"
    cumulative = 0
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["index", "scene", "in_tc", "out_tc", "duration_s", "clip_file", "description", "motion_prompt"]
        )
        for shot in screenplay.shots:
            in_s = cumulative
            out_s = cumulative + seconds
            cumulative = out_s
            clip_name = _clip_path(project_dir, shot).name
            w.writerow(
                [
                    shot.index,
                    shot.scene,
                    _tc(in_s),
                    _tc(out_s),
                    seconds,
                    clip_name,
                    shot.description,
                    shot.motion_prompt,
                ]
            )
    return out


def _tc(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"
