"""
hosters/pixeldrain.py
Extract a direct download URL from a PixelDrain share link.

Both ``pixeldrain.com`` and ``pixeldrain.dev`` are supported.

PixelDrain exposes a simple public REST API:
  GET  /api/file/{id}/info  → JSON with success=true/false
  GET  /api/file/{id}?download → streams the file (no auth needed for
                                  public files)

No authentication is required for files that have been uploaded publicly.
"""

from __future__ import annotations

import re
import aiohttp


_PD_PATTERNS: list[str] = [
    # Standard share page: pixeldrain.com/u/AbCdEfGh
    r"https?://(?:www\.)?pixeldrain\.(?:com|dev)/u/([a-zA-Z0-9\-_]+)",
    # Direct API URL: pixeldrain.com/api/file/AbCdEfGh
    r"https?://(?:www\.)?pixeldrain\.(?:com|dev)/api/file/([a-zA-Z0-9\-_]+)",
    # List page: pixeldrain.com/l/AbCdEfGh  (first file only)
    r"https?://(?:www\.)?pixeldrain\.(?:com|dev)/l/([a-zA-Z0-9\-_]+)",
]

# Always resolve via the canonical .com API, regardless of source TLD
_INFO_TMPL   = "https://pixeldrain.com/api/file/{id}/info"
_DIRECT_TMPL = "https://pixeldrain.com/api/file/{id}?download"

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _extract_id(url: str) -> str | None:
    for pattern in _PD_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def extract_pixeldrain_direct(url: str) -> str | None:
    """
    Return the direct download URL for a public PixelDrain file, or None.

    Parameters
    ----------
    url : str
        Any ``pixeldrain.com`` or ``pixeldrain.dev`` share URL.

    Returns
    -------
    str | None
        A ``?download`` API URL that aria2 can fetch directly, or None.
    """
    file_id = _extract_id(url)
    if not file_id:
        print(f"[PixelDrain] Could not parse file ID from: {url}")
        return None

    info_url   = _INFO_TMPL.format(id=file_id)
    direct_url = _DIRECT_TMPL.format(id=file_id)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(info_url, timeout=_TIMEOUT) as resp:
                if resp.status == 404:
                    print(f"[PixelDrain] File not found (id={file_id!r})")
                    return None

                if resp.status != 200:
                    print(f"[PixelDrain] Info API returned HTTP {resp.status}")
                    return None

                data = await resp.json(content_type=None)

        if not data.get("success", False):
            reason = data.get("value", "unknown")
            print(f"[PixelDrain] File not accessible — reason={reason!r}")
            return None

        # File is public and accessible — return the download URL
        return direct_url

    except Exception as exc:
        print(f"[PixelDrain] Extraction error: {exc}")
        return None
