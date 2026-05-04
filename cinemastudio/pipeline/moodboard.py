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

from PIL import Image
from rich.console import Console

from cinemastudio.models import Screenplay, Shot
from cinemastudio.providers.ai_auto import AIAutoClient, file_to_data_url

console = Console()


def _project_character_path(project_dir: Path, name: str) -> Path:
    """Resolve the per-project portrait path for a character name."""
    import re

    safe = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "character"
    return project_dir / "characters" / f"{safe}.jpg"


def _references_for_shot(project_dir: Path, shot: Shot) -> list[str]:
    """Return up to 2 character portrait data URLs for the given shot."""
    refs: list[str] = []
    for name in shot.character_names or []:
        p = _project_character_path(project_dir, name)
        if p.exists():
            refs.append(file_to_data_url(p))
        if len(refs) == 2:
            break
    return refs


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
        await asyncio.gather(*moodboard_tasks)

    # Per-shot keyframes (these are what Seedance actually animates from).
    keyframe_paths: dict[int, Path] = {}

    async def _kf(shot: Shot) -> tuple[int, Path]:
        out = keyframes_dir / f"shot_{shot.index:03d}.jpg"
        refs = _references_for_shot(project_dir, shot)
        if refs:
            console.log(
                f"[green]Keyframe[/green] shot {shot.index} (with "
                f"{len(refs)} character ref{'s' if len(refs) != 1 else ''})"
            )
        else:
            console.log(f"[green]Keyframe[/green] shot {shot.index}")
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

    results = await asyncio.gather(*[_kf(s) for s in screenplay.shots])
    for idx, path in results:
        keyframe_paths[idx] = path
    return keyframe_paths
