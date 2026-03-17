"""
hosters/__init__.py
Central registry for all supported file-hosting services.
Add new hosters here: define a pattern and export its handler.
"""

import re

from .mediafire  import extract_mediafire_direct
from .pixeldrain import extract_pixeldrain_direct
from .gofile     import extract_gofile_direct
from .mega       import download_mega_link, is_mega_link

# ── Pattern registry ──────────────────────────────────────────────────────────
# Order matters: first match wins.
_HOSTER_PATTERNS: dict[str, str] = {
    "mediafire":  r"https?://(?:www\.)?mediafire\.com",
    "pixeldrain": r"https?://(?:www\.)?pixeldrain\.(?:com|dev)",
    "gofile":     r"https?://(?:www\.)?gofile\.io",
    "mega":       r"https?://(?:www\.)?mega\.(?:nz|co\.nz|io)",
}


def is_special_hoster(url: str) -> str | None:
    """Return the hoster name if *url* belongs to a supported hoster, else None."""
    for name, pattern in _HOSTER_PATTERNS.items():
        if re.search(pattern, url, re.IGNORECASE):
            return name
    return None


def list_hosters() -> list[str]:
    """Return a sorted list of all registered hoster names."""
    return sorted(_HOSTER_PATTERNS.keys())


__all__ = [
    "is_special_hoster",
    "list_hosters",
    "extract_mediafire_direct",
    "extract_pixeldrain_direct",
    "extract_gofile_direct",
    "download_mega_link",
    "is_mega_link",
]
