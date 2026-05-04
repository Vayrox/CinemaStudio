"""Cross-project character library at ~/.cinemastudio/characters/.

Layout:
  ~/.cinemastudio/characters/<slug>/
    meta.json    {name, description, created_at, source: 'generated'|'uploaded'}
    portrait.jpg
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

LIBRARY_ROOT = Path.home() / ".cinemastudio" / "characters"


def safe_slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48].rstrip("-") or "character"


def ensure_root() -> Path:
    LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    return LIBRARY_ROOT


def _meta_path(slug: str) -> Path:
    return LIBRARY_ROOT / slug / "meta.json"


def portrait_path(slug: str) -> Path:
    return LIBRARY_ROOT / slug / "portrait.jpg"


def list_library() -> list[dict[str, Any]]:
    ensure_root()
    out: list[dict[str, Any]] = []
    for d in sorted(LIBRARY_ROOT.iterdir()):
        if not d.is_dir():
            continue
        meta = _meta_path(d.name)
        if not meta.exists():
            continue
        try:
            data = json.loads(meta.read_text())
        except Exception:
            continue
        data["slug"] = d.name
        data["has_portrait"] = portrait_path(d.name).exists()
        out.append(data)
    return sorted(out, key=lambda d: -d.get("created_at", 0))


def get_character(slug: str) -> dict[str, Any] | None:
    meta = _meta_path(slug)
    if not meta.exists():
        return None
    data = json.loads(meta.read_text())
    data["slug"] = slug
    data["has_portrait"] = portrait_path(slug).exists()
    return data


def unique_slug(name: str) -> str:
    base = safe_slug(name)
    candidate = base
    n = 2
    while (LIBRARY_ROOT / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def save_character(
    slug: str,
    name: str,
    description: str,
    *,
    source: str = "generated",
) -> Path:
    ensure_root()
    char_dir = LIBRARY_ROOT / slug
    char_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "description": description,
        "source": source,
        "created_at": time.time(),
    }
    _meta_path(slug).write_text(json.dumps(meta, indent=2))
    return char_dir


def update_character(slug: str, *, name: str | None = None, description: str | None = None) -> dict[str, Any]:
    data = get_character(slug)
    if not data:
        raise FileNotFoundError(slug)
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    _meta_path(slug).write_text(json.dumps(
        {k: v for k, v in data.items() if k not in {"slug", "has_portrait"}},
        indent=2,
    ))
    return data


def delete_character(slug: str) -> None:
    target = LIBRARY_ROOT / slug
    if target.exists():
        shutil.rmtree(target)


def write_portrait_bytes(slug: str, data: bytes) -> Path:
    ensure_root()
    char_dir = LIBRARY_ROOT / slug
    char_dir.mkdir(parents=True, exist_ok=True)
    p = portrait_path(slug)
    p.write_bytes(data)
    return p
