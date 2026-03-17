"""
hosters/mega.py
Download a Mega.nz public link to a local directory.

Dependencies
------------
    pip install mega.py

The ``mega.py`` library is synchronous (uses ``requests`` internally), so
every download runs inside a thread-pool executor to avoid blocking the
asyncio event loop.

Progress is approximated by polling the partially-written file size from a
background coroutine while the download thread is running.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable

# Dedicated executor so Mega downloads don't compete with aria2 I/O threads
_mega_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mega_dl")

# ── Patterns ──────────────────────────────────────────────────────────────────
_MEGA_PATTERNS: list[str] = [
    r"https?://(?:www\.)?mega\.nz/file/([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.nz/#!([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.co\.nz/#!([a-zA-Z0-9_\-]+)",
    r"https?://(?:www\.)?mega\.io/file/([a-zA-Z0-9_\-]+)",
    # Folder links (will try to download first file found)
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
    Download a public Mega.nz link to *download_dir*.

    Parameters
    ----------
    url : str
        Public Mega.nz file or folder URL.
    download_dir : str
        Directory where the file should be saved.
    task : DownloadTask | None
        If provided, ``.dl`` dict is updated with live progress so the
        dashboard can display speed / ETA while the download is running.
    push_update_fn : async callable | None
        Coroutine function ``(user_id: int) -> None`` that pushes a
        dashboard refresh.  Must be provided when *task* is provided.

    Returns
    -------
    tuple[str, str]
        ``(absolute_file_path, filename)``

    Raises
    ------
    ImportError
        If the ``mega.py`` package is not installed.
    RuntimeError
        If the download fails or the output file cannot be located.
    """
    try:
        from mega import Mega  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "mega.py library is not installed.\n"
            "Install it with:  pip install mega.py"
        ) from exc

    os.makedirs(download_dir, exist_ok=True)

    result_holder: list[str | None]    = [None]
    error_holder:  list[Exception | None] = [None]
    thread_done = threading.Event()

    def _worker() -> None:
        try:
            m      = Mega()
            client = m.login()   # anonymous — no credentials needed for public links
            # mega.py returns a pathlib.Path or str pointing to the saved file
            path   = client.download_url(url, dest_path=download_dir)
            result_holder[0] = str(path)
        except Exception as exc:  # noqa: BLE001
            error_holder[0] = exc
        finally:
            thread_done.set()

    # ── Launch download in background thread ──────────────────────────────
    _mega_executor.submit(_worker)

    # ── Poll file size for live progress ──────────────────────────────────
    start_time   = time.time()
    last_size    = 0
    last_poll_t  = start_time

    while not thread_done.is_set():
        await asyncio.sleep(3)

        if task is None or push_update_fn is None:
            continue   # nothing to update

        try:
            # Find the file being written (largest / most-recently-modified)
            candidates = [
                os.path.join(download_dir, f)
                for f in os.listdir(download_dir)
                if os.path.isfile(os.path.join(download_dir, f))
            ]
            if not candidates:
                continue

            partial_path = max(candidates, key=os.path.getmtime)
            current_size = os.path.getsize(partial_path)
            now          = time.time()
            elapsed      = now - start_time
            dt           = now - last_poll_t or 1
            speed        = max(current_size - last_size, 0) / dt
            total        = task.dl.get("total", 0)
            progress     = (current_size / total * 100) if total > 0 else 0.0
            eta          = (total - current_size) / speed if (speed > 0 and total > 0) else 0

            task.dl.update({
                "filename":   os.path.basename(partial_path),
                "progress":   min(progress, 99.0),   # 100% only after confirmed complete
                "speed":      speed,
                "downloaded": current_size,
                "elapsed":    elapsed,
                "eta":        eta,
                "peer_line":  "├ **Engine** → MEGA\n",
            })
            task.filename = task.dl["filename"]

            await push_update_fn(task.user_id)

            last_size   = current_size
            last_poll_t = now

        except Exception:  # noqa: BLE001
            pass   # never crash the progress loop

    # ── Check for errors from the worker thread ───────────────────────────
    if error_holder[0] is not None:
        raise RuntimeError(
            f"Mega download failed: {error_holder[0]}"
        ) from error_holder[0]

    file_path = result_holder[0]

    # ── Locate the downloaded file ────────────────────────────────────────
    if file_path and os.path.exists(file_path):
        filename = os.path.basename(file_path)
        return (file_path, filename)

    # mega.py occasionally returns a directory path for folder downloads;
    # find the most-recently-modified file inside it.
    if file_path and os.path.isdir(file_path):
        all_files = [
            os.path.join(r, f)
            for r, _, files in os.walk(file_path)
            for f in files
        ]
        if all_files:
            newest = max(all_files, key=os.path.getmtime)
            return (newest, os.path.basename(newest))

    # Last resort: scan the download dir for whatever was just written
    candidates = [
        os.path.join(download_dir, f)
        for f in os.listdir(download_dir)
        if os.path.isfile(os.path.join(download_dir, f))
    ]
    if candidates:
        newest = max(candidates, key=os.path.getmtime)
        return (newest, os.path.basename(newest))

    raise RuntimeError(
        f"Mega download appeared to succeed but no output file was found "
        f"in {download_dir!r}. raw result={file_path!r}"
    )
