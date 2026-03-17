"""
hosters/mediafire.py
Extract a direct download URL from a MediaFire share link.

Strategy
--------
1. Parse the file key from the URL.
2. Hit MediaFire's public file-info API (JSON response) — no login required
   for public files.
3. Return the ``normal_download`` link from the API response, or None if
   the file is private / not found.
"""

from __future__ import annotations

import re
import aiohttp


# ── Regex patterns for every known MediaFire URL shape ───────────────────────
_MF_PATTERNS: list[str] = [
    r"https?://(?:www\.)?mediafire\.com/file/([a-zA-Z0-9]+)",
    r"https?://(?:www\.)?mediafire\.com/file_premium/([a-zA-Z0-9]+)",
    r"https?://(?:www\.)?mediafire\.com/view/([a-zA-Z0-9]+)",
    r"https?://(?:www\.)?mediafire\.com/\?([a-zA-Z0-9]+)",
    # Short-link style: mediafire.com/download/XXXX/filename.ext
    r"https?://(?:www\.)?mediafire\.com/download/([a-zA-Z0-9]+)",
]

_API_TMPL = (
    "https://www.mediafire.com/api/1.4/file/get_info.php"
    "?quick_key={key}&response_format=json"
)

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _extract_key(url: str) -> str | None:
    for pattern in _MF_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def extract_mediafire_direct(url: str) -> str | None:
    """
    Return the direct download URL for a public MediaFire file, or None.

    Parameters
    ----------
    url : str
        Any valid MediaFire share / view / download URL.

    Returns
    -------
    str | None
        The direct ``normal_download`` URL, or None if extraction fails.
    """
    file_key = _extract_key(url)
    if not file_key:
        print(f"[MediaFire] Could not parse file key from: {url}")
        return None

    api_url = _API_TMPL.format(key=file_key)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    print(f"[MediaFire] API returned HTTP {resp.status}")
                    return None

                data = await resp.json(content_type=None)

        # ── Navigate the JSON response ────────────────────────────────────
        response_obj = data.get("response", {})
        action       = response_obj.get("action", "")
        result       = response_obj.get("result", "")

        if result != "Success":
            message = response_obj.get("message", "unknown error")
            print(f"[MediaFire] API error — result={result!r}, message={message!r}")
            return None

        file_info = response_obj.get("file_info", {})
        links     = file_info.get("links", {})

        # Prefer the uncompressed direct link; fall back to the normal web link
        direct = (
            links.get("normal_download")
            or links.get("direct_download")
        )

        if direct:
            return direct

        print(f"[MediaFire] No download link found in API response for key={file_key!r}")
        return None

    except Exception as exc:
        print(f"[MediaFire] Extraction error: {exc}")
        return None
