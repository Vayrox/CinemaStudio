"""Logline -> screenplay + shot list, via the configured LLM provider.

Two providers are supported:

* `anthropic` — Claude (`claude-sonnet-4-6` by default).
* `google`    — Gemini via Google AI Studio (`gemini-2.5-pro` by default).

Both produce the same JSON schema for `Screenplay`. We ask for `application/json`
output where the SDK supports it, then validate with pydantic.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from cinemastudio.models import Screenplay

ANTHROPIC_MODEL = "claude-sonnet-4-6"
GOOGLE_MODEL = "gemini-2.5-pro"
PROMPT_DIR = Path(__file__).parent.parent / "prompts"


def _load_system_prompt() -> str:
    return (PROMPT_DIR / "script_system.txt").read_text()


def _strip_json_fences(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if m:
        return m.group(1)
    return text.strip()


def _user_prompt(logline: str, aspect_ratio: str, target_minutes: float, shot_seconds: int) -> str:
    expected_shots = max(1, math.ceil(target_minutes * 60 / shot_seconds))
    return (
        f"Logline: {logline}\n"
        f"Aspect ratio: {aspect_ratio}\n"
        f"Target length: {target_minutes} minutes\n"
        f"Shot duration: {shot_seconds} seconds (every shot is exactly this long)\n"
        f"Expected shot count: {expected_shots}\n\n"
        "Return the JSON object only."
    )


def _generate_anthropic(api_key: str, system: str, user: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _generate_google(api_key: str, system: str, user: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    # thinking_budget=-1 lets Gemini 2.5 Pro spend as long as it needs
    # reasoning before writing the screenplay; include_thoughts=False keeps
    # the chain-of-thought out of the returned JSON.
    resp = client.models.generate_content(
        model=GOOGLE_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            max_output_tokens=32000,
            temperature=0.9,
            thinking_config=types.ThinkingConfig(
                thinking_budget=-1,
                include_thoughts=False,
            ),
        ),
    )
    return resp.text or ""


def generate_screenplay(
    *,
    provider: str,
    api_key: str,
    logline: str,
    aspect_ratio: str,
    target_minutes: float,
    shot_seconds: int,
) -> Screenplay:
    system = _load_system_prompt()
    user = _user_prompt(logline, aspect_ratio, target_minutes, shot_seconds)

    provider = (provider or "google").lower()
    if provider == "anthropic":
        raw = _generate_anthropic(api_key, system, user)
    elif provider == "google":
        raw = _generate_google(api_key, system, user)
    else:
        raise ValueError(f"Unknown script provider: {provider!r}")

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
