"""Moodboards: generate keyframe images for each shot.

Strategy: each shot gets its own photorealistic keyframe via the keyframe image
model. For multi-shot scenes we ALSO generate a wide "moodboard" reference using
the moodboard image model, then use sliced panels of it as a style/character
reference when generating the per-shot keyframes. This is the cheap continuity
trick: one image generation locks lighting/character/style across N panels.

Output layout:
  projects/<slug>/moodboards/scene_<N>_moodboard.jpg
  projects/<slug>/moodboards/scene_<N>_panel_<K>.jpg
  projects/<slug>/keyframes/shot_<NNN>.jpg
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import re

from PIL import Image
from rich.console import Console

from cinemastudio.models import Screenplay, Shot
from cinemastudio.providers.ai_auto import AIAutoClient, file_to_data_url

console = Console()


def _safe_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:48].rstrip("-") or "character"


def _project_character_path(project_dir: Path, name: str) -> Path:
    """Resolve the per-project portrait path for a character name."""
    return project_dir / "characters" / f"{_safe_name(name)}.jpg"


def _references_for_shot(
    project_dir: Path,
    shot: Shot,
    screenplay: Screenplay | None = None,
) -> tuple[list[str], list[str]]:
    """Return (data URLs, names) for up to 2 character sheets matching the shot.

    Tries `shot.character_names` first; if that's empty or yields no portraits,
    scans the shot's keyframe_prompt + description for any known character name
    from the screenplay. This is the fallback for screenplays where Gemini
    didn't populate character_names reliably.
    """
    seen: set[str] = set()
    refs: list[str] = []
    matched: list[str] = []

    def _add(name: str) -> bool:
        key = name.strip().lower()
        if not key or key in seen:
            return False
        path = _project_character_path(project_dir, name)
        if not path.exists():
            return False
        refs.append(file_to_data_url(path))
        matched.append(name)
        seen.add(key)
        return len(refs) >= 2

    # Primary: explicit character_names on the shot.
    for name in shot.character_names or []:
        if _add(name):
            return refs, matched

    # Fallback: scan keyframe_prompt + description for any known character name.
    if screenplay is not None and len(refs) < 2:
        haystack = " ".join(filter(None, [shot.keyframe_prompt, shot.description]))
        # Sort longest first so "Captain Hawk" matches before "Hawk".
        candidates = sorted(
            (c.name for c in screenplay.characters),
            key=lambda n: -len(n),
        )
        for cand in candidates:
            if cand.strip().lower() in seen:
                continue
            if re.search(rf"\b{re.escape(cand)}\b", haystack, flags=re.IGNORECASE):
                if _add(cand):
                    return refs, matched

    return refs, matched


def _moodboard_prompt(screenplay: Screenplay, scene_idx: int, shots: list[Shot]) -> str:
    """Build a single prompt that asks the image model for a multi-panel sheet."""
    scene = next(s for s in screenplay.scenes if s.index == scene_idx)
    panels = []
    for k, shot in enumerate(shots, 1):
        panels.append(f"Panel {k}: {shot.keyframe_prompt}")
    panel_count = len(shots)
    layout = (
        f"A single horizontal contact sheet divided into {panel_count} equal vertical panels, "
        "side by side, no gaps, no borders, no text overlays. "
        "All panels share the same lighting style, color palette, and any recurring characters."
    )
    style = screenplay.style
    body = "\n".join(panels)
    return (
        f"{layout}\n\nGlobal visual style: {style}\n\n"
        f"Scene context: {scene.title}, {scene.location}, {scene.time_of_day}, mood: {scene.mood}.\n\n"
        f"{body}"
    )


def _slice_moodboard(image_path: Path, panel_count: int, out_dir: Path, scene_idx: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    panel_w = w // panel_count
    paths: list[Path] = []
    for k in range(panel_count):
        left = k * panel_w
        right = (k + 1) * panel_w if k < panel_count - 1 else w
        panel = img.crop((left, 0, right, h))
        out = out_dir / f"scene_{scene_idx:02d}_panel_{k + 1}.jpg"
        panel.save(out, "JPEG", quality=92)
        paths.append(out)
    return paths


async def _generate_keyframe(
    client: AIAutoClient,
    *,
    shot: Shot,
    screenplay: Screenplay,
    image_model: str,
    aspect_ratio: str,
    resolution: str,
    out_path: Path,
    references: list[str] | None = None,
) -> Path:
    style_suffix = f" Visual style: {screenplay.style}."
    prompt = shot.keyframe_prompt + style_suffix
    gen = await client.generate_image(
        prompt=prompt,
        image_model=image_model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_images=references,
    )
    await client.download_image(gen.id, out_path)
    return out_path


async def regenerate_scene_moodboard(
    *,
    client: AIAutoClient,
    screenplay: Screenplay,
    project_dir: Path,
    scene_idx: int,
    moodboard_image_model: str,
    image_resolution: str = "4k",
) -> Path:
    """Regenerate just the moodboard sheet for a single scene."""
    moodboards_dir = project_dir / "moodboards"
    moodboards_dir.mkdir(parents=True, exist_ok=True)
    shots = [s for s in screenplay.shots if s.scene == scene_idx]
    if not shots:
        raise ValueError(f"No shots found in scene {scene_idx}")
    prompt = _moodboard_prompt(screenplay, scene_idx, shots)
    out = moodboards_dir / f"scene_{scene_idx:02d}_moodboard.jpg"
    console.log(f"[cyan]Regenerating moodboard[/cyan] scene {scene_idx} ({len(shots)} panels)")
    gen = await client.generate_image(
        prompt=prompt,
        image_model=moodboard_image_model,
        aspect_ratio="16:9",
        resolution=image_resolution,
    )
    await client.download_image(gen.id, out)
    if len(shots) >= 2:
        _slice_moodboard(out, len(shots), moodboards_dir, scene_idx)
    return out


async def regenerate_shot_keyframe(
    *,
    client: AIAutoClient,
    screenplay: Screenplay,
    project_dir: Path,
    shot_index: int,
    keyframe_image_model: str,
    aspect_ratio: str,
    image_resolution: str = "4k",
) -> Path:
    """Regenerate just the keyframe for a single shot."""
    shot = next((s for s in screenplay.shots if s.index == shot_index), None)
    if shot is None:
        raise ValueError(f"No shot with index {shot_index}")
    keyframes_dir = project_dir / "keyframes"
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    out = keyframes_dir / f"shot_{shot.index:03d}.jpg"
    refs, matched = _references_for_shot(project_dir, shot, screenplay)
    if refs:
        console.log(
            f"[green]Regenerating keyframe[/green] shot {shot.index} (refs: {', '.join(matched)})"
        )
    else:
        console.log(
            f"[green]Regenerating keyframe[/green] shot {shot.index} (no character refs)"
        )
    return await _generate_keyframe(
        client,
        shot=shot,
        screenplay=screenplay,
        image_model=keyframe_image_model,
        aspect_ratio=aspect_ratio,
        resolution=image_resolution,
        out_path=out,
        references=refs,
    )


async def build_moodboards(
    *,
    client: AIAutoClient,
    screenplay: Screenplay,
    project_dir: Path,
    moodboard_image_model: str,
    keyframe_image_model: str,
    aspect_ratio: str,
    image_resolution: str = "4k",
) -> dict[int, Path]:
    """Generate per-scene moodboards (for review) and per-shot keyframes (used by Seedance).

    Returns a mapping shot_index -> keyframe path.
    """
    moodboards_dir = project_dir / "moodboards"
    keyframes_dir = project_dir / "keyframes"
    moodboards_dir.mkdir(parents=True, exist_ok=True)
    keyframes_dir.mkdir(parents=True, exist_ok=True)

    # Group shots by scene so we render one moodboard per scene.
    by_scene: dict[int, list[Shot]] = {}
    for shot in screenplay.shots:
        by_scene.setdefault(shot.scene, []).append(shot)

    # Moodboards: only worth doing when a scene has 2+ shots (continuity payoff).
    moodboard_tasks = []
    for scene_idx, shots in by_scene.items():
        if len(shots) < 2:
            continue
        prompt = _moodboard_prompt(screenplay, scene_idx, shots)
        out = moodboards_dir / f"scene_{scene_idx:02d}_moodboard.jpg"

        async def _do(scene_idx=scene_idx, prompt=prompt, out=out, shots=shots):
            console.log(f"[cyan]Moodboard[/cyan] scene {scene_idx} ({len(shots)} panels)")
            gen = await client.generate_image(
                prompt=prompt,
                image_model=moodboard_image_model,
                aspect_ratio="16:9",
                resolution=image_resolution,
            )
            await client.download_image(gen.id, out)
            _slice_moodboard(out, len(shots), moodboards_dir, scene_idx)
            return out

        moodboard_tasks.append(_do())

    if moodboard_tasks:
        # return_exceptions=True so one failed scene moodboard doesn't take
        # down the keyframes (or the other moodboards).
        results = await asyncio.gather(*moodboard_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                console.log(
                    f"[yellow]Moodboard skipped[/yellow] ({type(r).__name__}: {r})"
                )

    # Per-shot keyframes (these are what Seedance actually animates from).
    keyframe_paths: dict[int, Path] = {}

    async def _kf(shot: Shot) -> tuple[int, Path]:
        out = keyframes_dir / f"shot_{shot.index:03d}.jpg"
        refs, matched = _references_for_shot(project_dir, shot, screenplay)
        if refs:
            console.log(
                f"[green]Keyframe[/green] shot {shot.index} "
                f"(refs: {', '.join(matched)})"
            )
        else:
            console.log(
                f"[green]Keyframe[/green] shot {shot.index} "
                f"[yellow](no character refs — text only)[/yellow]"
            )
        path = await _generate_keyframe(
            client,
            shot=shot,
            screenplay=screenplay,
            image_model=keyframe_image_model,
            aspect_ratio=aspect_ratio,
            resolution=image_resolution,
            out_path=out,
            references=refs,
        )
        return shot.index, path

    results = await asyncio.gather(
        *[_kf(s) for s in screenplay.shots], return_exceptions=True
    )
    failures: list[str] = []
    for shot, r in zip(screenplay.shots, results):
        if isinstance(r, BaseException):
            failures.append(f"shot {shot.index} ({type(r).__name__}: {r})")
            continue
        idx, path = r
        keyframe_paths[idx] = path
    if failures:
        console.log(
            "[yellow]Keyframes skipped:[/yellow] " + "; ".join(failures)
        )
    return keyframe_paths
