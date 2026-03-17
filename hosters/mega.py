"""
hosters/mega.py
Download a Mega.nz public link using megasdk (pure Python, no pycrypto).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any, Awaitable, Callable

try:
    from megasdk import Mega  # type: ignore[import]
except ImportError:
    Mega = None

# ── Patterns ──────────────────────────────────────────────────────────────────
_MEGA_PATTERNS: list[str] = [
    r"https?://(?:www\.)?mega\.nz/file/([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.nz/#!([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.co\.nz/#!([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.io/file/([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.nz/folder/([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.nz/#F!([a-zA-Z0-9_\-]+)",
]


def is_mega_link(url: str) -> bool:
    """Return True if *url* looks like a Mega.nz / Mega.io link."""
    return any(re.search(p, url, re.IGNORECASE) for p in _MEGA_PATTERNS)


# ── Downloader ────────────────────────────────────────────────────────────────

async def download_mega_link(
    url: str,
    download_dir: str,
    task: Any = None,
    push_update_fn: Callable[[int], Awaitable[None]] | None = None,
) -> tuple[str, str]:
    """
    Download a public Mega.nz link to *download_dir* using megasdk.
    """
    if Mega is None:
        raise ImportError(
            "megasdk library is not installed.\n"
            "Install it with: pip install megasdk"
        )

    os.makedirs(download_dir, exist_ok=True)

    # Initialize Mega client (anonymous)
    mega = Mega()
    
    # Get file info first for progress
    file_info = mega.get_public_file_info(url)
    total_size = file_info.get("size", 0) if file_info else 0
    
    if task:
        task.dl["total"] = total_size
        task.dl["filename"] = file_info.get("name", "mega_download") if file_info else "mega_download"
        task.dl["peer_line"] = "├ **Engine** → MEGA\n"

    # Download with progress callback
    def progress_callback(downloaded: int, total: int):
        if task is None or push_update_fn is None:
            return
            
        now = time.time()
        elapsed = now - start_time
        speed = (downloaded - last_size[0]) / (now - last_poll[0]) if last_poll[0] else 0
        progress = (downloaded / total * 100) if total > 0 else 0
        eta = (total - downloaded) / speed if speed > 0 else 0
        
        task.dl.update({
            "progress": min(progress, 99.0),
            "speed": speed,
            "downloaded": downloaded,
            "elapsed": elapsed,
            "eta": eta,
        })
        
        last_size[0] = downloaded
        last_poll[0] = now
        
        # Schedule dashboard update
        asyncio.create_task(push_update_fn(task.user_id))

    start_time = time.time()
    last_size = [0]
    last_poll = [start_time]

    # Run download in thread to not block
    loop = asyncio.get_running_loop()
    
    def _download():
        return mega.download_url(
            url, 
            dest_path=download_dir,
            progress_callback=progress_callback if task else None
        )
    
    try:
        file_path = await loop.run_in_executor(None, _download)
    except Exception as exc:
        raise RuntimeError(f"Mega download failed: {exc}") from exc

    if not file_path or not os.path.exists(file_path):
        raise RuntimeError(f"Mega download failed: no file created at {file_path}")

    filename = os.path.basename(file_path)
    
    # Final progress update
    if task:
        task.dl["progress"] = 100.0
        task.dl["downloaded"] = os.path.getsize(file_path)
        if push_update_fn:
            await push_update_fn(task.user_id)
    
    return (str(file_path), filename)
