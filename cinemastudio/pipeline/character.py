"""Locked-reference character portrait generation.

A "portrait" here is a single neutral studio shot of a character that we
later pass to the keyframe model as a reference image. The goal is purely
identity locking — same face, hair, wardrobe across every shot.
"""
from __future__ import annotations

from pathlib import Path

from cinemastudio.providers.ai_auto import AIAutoClient


PORTRAIT_PROMPT_TEMPLATE = (
    "Studio reference portrait of {name}. {description}. "
    "Centered medium close-up, neutral pose, looking slightly off-camera, "
    "plain neutral grey backdrop, even soft front-lit lighting, no shadows, "
    "no props, no overlays, no text, no logos. "
    "Photorealistic, sharp focus, high detail on facial features, "
    "natural skin texture. Single subject only."
)


async def generate_portrait(
    client: AIAutoClient,
    *,
    name: str,
    description: str,
    image_model: str,
    out_path: Path,
    aspect_ratio: str = "1:1",
    resolution: str = "2k",
) -> Path:
    """Generate one reference portrait and save it to `out_path`."""
    prompt = PORTRAIT_PROMPT_TEMPLATE.format(name=name, description=description)
    gen = await client.generate_image(
        prompt=prompt,
        image_model=image_model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await client.download_image(gen.id, out_path)
    return out_path
