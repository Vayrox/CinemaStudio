"""Resolve a URL (Pinterest pin, direct image, etc.) to image bytes.

Pinterest pin URLs return HTML; we extract the og:image meta tag.
Anything that returns image/* is downloaded directly. Plain HTML pages
without og:image are rejected with a clear error.
"""
from __future__ import annotations

import re

import httpx

UA = "Mozilla/5.0 (compatible; CinemaStudio/0.1; +https://github.com/Vayrox/CinemaStudio)"
_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    flags=re.IGNORECASE,
)


async def fetch_image_from_url(url: str, *, timeout: float = 30.0) -> tuple[bytes, str]:
    """Return (bytes, mime). Raises ValueError if no image can be extracted."""
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    headers = {"User-Agent": UA, "Accept": "image/*,text/html;q=0.9,*/*;q=0.5"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            raise ValueError(f"Fetch failed: {r.status_code} {url}")
        ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ct.startswith("image/"):
            return r.content, ct
        if "html" in ct or ct == "":
            text = r.text
            m = _OG_RE.search(text)
            img_url = (m.group(1) or m.group(2)) if m else None
            if not img_url:
                raise ValueError(
                    "No og:image found on the page. Try pasting the direct image URL "
                    "(right-click the image → 'Copy image address')."
                )
            r2 = await client.get(img_url)
            if r2.status_code >= 400:
                raise ValueError(f"og:image fetch failed: {r2.status_code}")
            ct2 = r2.headers.get("content-type", "").split(";")[0].strip().lower()
            if not ct2.startswith("image/"):
                ct2 = "image/jpeg"
            return r2.content, ct2
        raise ValueError(f"Unexpected content-type: {ct}")
