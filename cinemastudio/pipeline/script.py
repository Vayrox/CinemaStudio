"""Logline -> screenplay + shot list, via Claude."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from anthropic import Anthropic

from cinemastudio.models import Screenplay

MODEL = "claude-sonnet-4-6"
PROMPT_DIR = Path(__file__).parent.parent / "prompts"


def _load_system_prompt() -> str:
    return (PROMPT_DIR / "script_system.txt").read_text()


def _strip_json_fences(text: str) -> str:
    # Models occasionally wrap JSON in ```json ... ``` despite instructions.
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if m:
        return m.group(1)
    return text.strip()


def generate_screenplay(
    *,
    api_key: str,
    logline: str,
    aspect_ratio: str,
    target_minutes: float,
    shot_seconds: int,
) -> Screenplay:
    client = Anthropic(api_key=api_key)
    expected_shots = max(1, math.ceil(target_minutes * 60 / shot_seconds))
    user = (
        f"Logline: {logline}\n"
        f"Aspect ratio: {aspect_ratio}\n"
        f"Target length: {target_minutes} minutes\n"
        f"Shot duration: {shot_seconds} seconds (every shot is exactly this long)\n"
        f"Expected shot count: {expected_shots}\n\n"
        "Return the JSON object only."
    )
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=_load_system_prompt(),
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    cleaned = _strip_json_fences(raw)
    data = json.loads(cleaned)
    return Screenplay.model_validate(data)


def save_screenplay(screenplay: Screenplay, project_dir: Path) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "screenplay.json"
    path.write_text(screenplay.model_dump_json(indent=2))
    return path


def load_screenplay(project_dir: Path) -> Screenplay:
    path = project_dir / "screenplay.json"
    return Screenplay.model_validate_json(path.read_text())
