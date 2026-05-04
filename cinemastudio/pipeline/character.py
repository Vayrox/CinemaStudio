"""Locked-reference character sheet generation.

A "character sheet" here is a 3-panel turnaround on a pure white background:

  ┌─────────────┬─────────────┬─────────────┐
  │   FRONT     │    BACK     │  FACE CU    │
  │  full body  │  full body  │  close-up   │
  └─────────────┴─────────────┴─────────────┘

Side-by-side equal panels, no gaps, no text. We later pass this sheet to
the keyframe model as a reference image. Multiple views beat a single
portrait at locking identity, hair, wardrobe across every shot.
"""
from __future__ import annotations

from pathlib import Path

from cinemastudio.providers.ai_auto import AIAutoClient


SHEET_PROMPT_TEMPLATE = (
    "Character reference sheet of {name} on a pure white seamless studio "
    "background (#FFFFFF, no shadows on the floor, no horizon line). "
    "A single horizontal image divided into THREE equal vertical panels, "
    "side by side, no gaps, no borders, no text, no labels, no logos:\n"
    "  Panel 1 (left): FULL-BODY FRONT view, head to toe, neutral pose, "
    "arms slightly away from body, looking straight at camera.\n"
    "  Panel 2 (centre): FULL-BODY BACK view, same pose, head from behind, "
    "showing hair, back of wardrobe.\n"
    "  Panel 3 (right): HEAD-AND-SHOULDERS CLOSE-UP, frontal, neutral "
    "expression, sharp focus on facial features.\n"
    "All three panels show the EXACT SAME character with identical "
    "wardrobe, hair, accessories, build, age, and skin tone. Even soft "
    "front-lit studio lighting throughout. Photorealistic, sharp focus, "
    "natural skin texture, ultra-detailed.\n\n"
    "Character description: {description}"
)


async def generate_portrait(
    client: AIAutoClient,
    *,
    name: str,
    description: str,
    image_model: str,
    out_path: Path,
    aspect_ratio: str = "16:9",
    resolution: str = "4k",
) -> Path:
    """Generate one 3-panel character sheet and save it to `out_path`."""
    prompt = SHEET_PROMPT_TEMPLATE.format(name=name, description=description)
    gen = await client.generate_image(
        prompt=prompt,
        image_model=image_model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await client.download_image(gen.id, out_path)
    return out_path
