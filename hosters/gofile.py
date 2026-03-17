"""
hosters/gofile.py
Extract a direct download URL from a GoFile share link.
"""

from __future__ import annotations

import re
from curl_cffi.requests import AsyncSession  # Changed from aiohttp

_GF_PATTERNS: list[str] = [
    r"https?://(?:www\.)?gofile\.io/d/([a-zA-Z0-9]+)",
    r"https?://gofile\.io/\?c=([a-zA-Z0-9]+)",
    r"https?://(?:www\.)?gofile\.io/d/([a-zA-Z0-9]+)\?",
]

_ACCOUNTS_URL = "https://api.gofile.io/accounts"
_CONTENTS_TMPL = "https://api.gofile.io/contents/{id}?wt=4fd6sg89d7s6&cache=true"

_TIMEOUT = 30


def _extract_id(url: str) -> str | None:
    for pattern in _GF_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def extract_gofile_direct(url: str) -> tuple[str, str, dict] | None:
    content_id = _extract_id(url)
    if not content_id:
        print(f"[GoFile] Could not parse content ID from: {url}")
        return None

    try:
        # Use curl_cffi with browser impersonation
        async with AsyncSession(impersonate="chrome110") as session:
            
            # Step 1: Create guest account
            resp = await session.post(_ACCOUNTS_URL, timeout=_TIMEOUT)
            if resp.status_code != 200:
                print(f"[GoFile] Account creation failed — HTTP {resp.status_code}")
                return None

            acc_data = resp.json()

            if acc_data.get("status") != "ok":
                print(f"[GoFile] Account API error — {acc_data}")
                return None

            token = acc_data["data"]["token"]

            # Step 2: Fetch content
            headers = {"Authorization": f"Bearer {token}"}
            content_url = _CONTENTS_TMPL.format(id=content_id)

            resp = await session.get(content_url, headers=headers, timeout=_TIMEOUT)
            if resp.status_code != 200:
                print(f"[GoFile] Contents fetch failed — HTTP {resp.status_code}")
                # Debug: print response body
                print(f"[GoFile] Response: {resp.text[:200]}")
                return None

            content_data = resp.json()

        if content_data.get("status") != "ok":
            reason = content_data.get("status", "unknown")
            print(f"[GoFile] Contents API error — status={reason!r}")
            return None

        data = content_data.get("data", {})
        children: dict = data.get("children") or data.get("contents") or {}

        if not children:
            print(f"[GoFile] No children/contents found for id={content_id!r}")
            return None

        for _, item in children.items():
            if item.get("type") == "file":
                direct_link = item.get("link") or item.get("directLink")
                filename = item.get("name", "gofile_download")

                if direct_link:
                    aria2_opts = {"header": f"Cookie: accountToken={token}"}
                    return (direct_link, filename, aria2_opts)

        print(f"[GoFile] No downloadable file entry found for id={content_id!r}")
        return None

    except Exception as exc:
        print(f"[GoFile] Extraction error: {exc}")
        return None
