"""
hosters/gofile.py
Extract a direct download URL from a GoFile share link.

GoFile API v2 requires a bearer token even for public files.
Steps:
  1. POST /accounts            → create a guest account → get token
  2. GET  /contents/{id}       → list files in the folder
  3. Return (direct_url, filename, aria2_opts) where aria2_opts carries
     the accountToken cookie that GoFile requires on the download request.
"""

from __future__ import annotations

import re
import aiohttp

_GF_PATTERNS: list[str] = [
    r"https?://(?:www\.)?gofile\.io/d/([a-zA-Z0-9]+)",
    r"https?://gofile\.io/\?c=([a-zA-Z0-9]+)",
    # Trailing query string
    r"https?://(?:www\.)?gofile\.io/d/([a-zA-Z0-9]+)\?",
]

_ACCOUNTS_URL = "https://api.gofile.io/accounts"
_CONTENTS_TMPL = "https://api.gofile.io/contents/{id}?wt=4fd6sg89d7s6&cache=true"

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _extract_id(url: str) -> str | None:
    for pattern in _GF_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def extract_gofile_direct(url: str) -> tuple[str, str, dict] | None:
    """
    Return ``(direct_url, filename, aria2_extra_opts)`` for a GoFile link.

    ``aria2_extra_opts`` contains the ``header`` key needed so aria2 can
    authenticate the download request with the guest token cookie.

    Parameters
    ----------
    url : str
        Any ``gofile.io/d/…`` or ``gofile.io/?c=…`` URL.

    Returns
    -------
    tuple[str, str, dict] | None
        ``(direct_url, filename, {"header": "Cookie: accountToken=<token>"})``
        or None on failure.
    """
    content_id = _extract_id(url)
    if not content_id:
        print(f"[GoFile] Could not parse content ID from: {url}")
        return None

    try:
        async with aiohttp.ClientSession() as session:

            # ── Step 1: obtain a guest bearer token ───────────────────────
            async with session.post(_ACCOUNTS_URL, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    print(f"[GoFile] Account creation failed — HTTP {resp.status}")
                    return None

                acc_data = await resp.json(content_type=None)

            if acc_data.get("status") != "ok":
                print(f"[GoFile] Account API error — {acc_data}")
                return None

            token = acc_data["data"]["token"]

            # ── Step 2: fetch content metadata ────────────────────────────
            headers     = {"Authorization": f"Bearer {token}"}
            content_url = _CONTENTS_TMPL.format(id=content_id)

            async with session.get(
                content_url, headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    print(f"[GoFile] Contents fetch failed — HTTP {resp.status}")
                    return None

                content_data = await resp.json(content_type=None)

        if content_data.get("status") != "ok":
            reason = content_data.get("status", "unknown")
            print(f"[GoFile] Contents API error — status={reason!r}")
            return None

        data = content_data.get("data", {})

        # API v2 uses "children"; legacy responses used "contents"
        children: dict = data.get("children") or data.get("contents") or {}

        if not children:
            print(f"[GoFile] No children/contents found for id={content_id!r}")
            return None

        # Return the first file entry found (most GoFile links are single-file)
        for _, item in children.items():
            if item.get("type") == "file":
                direct_link: str | None = (
                    item.get("link")
                    or item.get("directLink")
                )
                filename: str = item.get("name", "gofile_download")

                if direct_link:
                    # aria2 must send the accountToken cookie so GoFile
                    # authorises the download without a login page redirect.
                    aria2_opts = {"header": f"Cookie: accountToken={token}"}
                    return (direct_link, filename, aria2_opts)

        print(f"[GoFile] No downloadable file entry found for id={content_id!r}")
        return None

    except Exception as exc:
        print(f"[GoFile] Extraction error: {exc}")
        return None
