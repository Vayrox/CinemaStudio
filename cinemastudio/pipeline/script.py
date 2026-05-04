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

    # 5-minute hard ceiling so a stuck call surfaces as an error instead of
    # quietly waiting for an hour. Genai SDK takes the timeout in ms.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=300_000),
    )
    # thinking_budget=-1 (dynamic) lets the model think indefinitely on hard
    # prompts; we've seen it spiral past 30 minutes. 8192 thinking tokens is
    # plenty for outlining + verifying a screenplay (typical run uses < 4k)
    # while still giving Pro real space to reason. include_thoughts=False
    # keeps the chain-of-thought out of the returned JSON.
    resp = client.models.generate_content(
        model=GOOGLE_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            max_output_tokens=32000,
            temperature=0.9,
            thinking_config=types.ThinkingConfig(
                thinking_budget=8192,
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


LOGLINE_SYSTEM = (
    "You are a senior screenwriter helping a director pitch their next short "
    "film. The user will give you a seed idea — anything from a vague theme "
    "to a detailed brief with characters, plot beats, settings, and tone.\n\n"
    "Your job: return a JSON array of polished one-sentence loglines, each "
    "honouring the seed's intent.\n\n"
    "RULES (in order of priority):\n"
    "1. Preserve EVERY concrete element from the seed: named characters, "
    "specific objects, locations, plot beats, time periods, technology, "
    "stated tone, requested genre. NEVER drop or replace specifics. If the "
    "seed says 'shapeshifting gauntlet', every logline must include the "
    "shapeshifting gauntlet.\n"
    "2. What you ARE allowed to vary between loglines: the angle of approach "
    "(POV, central conflict beat, antagonist framing, opening situation), "
    "the protagonist's secret weakness, the stakes, the visual atmosphere — "
    "but ALL the seed's named elements must still appear in every option.\n"
    "3. Each logline is one cinematic sentence: a protagonist with a telling "
    "detail, a concrete conflict, and an evocative setting. Length is "
    "whatever it takes to honour rule 1 — usually 25–50 words.\n"
    "4. No filler ('explore', 'embark on a journey', 'must learn'). Use "
    "active verbs and concrete imagery.\n"
    "5. Return ONLY a JSON array of strings. No prose, no numbering, no "
    "markdown fences."
)


def generate_loglines(
    *,
    provider: str,
    api_key: str,
    seed: str,
    count: int = 10,
) -> list[str]:
    """Expand a seed idea into `count` polished loglines."""
    user = (
        f"Seed:\n{seed}\n\n"
        f"Generate exactly {count} distinct loglines. Each MUST include every "
        f"named character, plot element, object, setting, time period, and "
        f"tone keyword from the seed above — vary only the dramatic angle. "
        f"Return a JSON array of {count} strings."
    )
    provider = (provider or "google").lower()
    if provider == "anthropic":
        raw = _generate_anthropic(api_key, LOGLINE_SYSTEM, user)
    elif provider == "google":
        raw = _generate_google(api_key, LOGLINE_SYSTEM, user)
    else:
        raise ValueError(f"Unknown script provider: {provider!r}")

    cleaned = _strip_json_fences(raw)
    # Tolerate either a bare array or {"loglines": [...]}.
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last-resort: pick the first JSON array we can find.
        m = re.search(r"\[(?:.|\s)*\]", cleaned)
        if not m:
            raise
        data = json.loads(m.group(0))
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got: {type(data).__name__}")
    return [str(x).strip() for x in data if str(x).strip()][:count]


def save_screenplay(screenplay: Screenplay, project_dir: Path) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "screenplay.json"
    path.write_text(screenplay.model_dump_json(indent=2))
    return path


def load_screenplay(project_dir: Path) -> Screenplay:
    path = project_dir / "screenplay.json"
    return Screenplay.model_validate_json(path.read_text())
