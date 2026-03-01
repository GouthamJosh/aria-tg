import os
import re
import time
import asyncio
import aria2p
import aiohttp
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, MessageNotModified
import py7zr
import zipfile
import shutil
from datetime import timedelta
import psutil
from concurrent.futures import ThreadPoolExecutor

# ─────────────────────────────────────────────────────────────────────────────
#  Speed Optimizations
# ─────────────────────────────────────────────────────────────────────────────
try:
    import uvloop
    uvloop.install()
    print("✅ uvloop installed - faster event loop")
except ImportError:
    print("⚠️ uvloop not installed. Install with: pip install uvloop")

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────
API_ID       = os.environ.get("API_ID", '')
API_HASH     = os.environ.get("API_HASH", '')
BOT_TOKEN    = os.environ.get("BOT_TOKEN", '')
OWNER_ID     = int(os.environ.get("OWNER_ID", "6108995220"))
DOWNLOAD_DIR = "/tmp/downloads"
ARIA2_HOST   = "http://localhost"
ARIA2_PORT   = 6800
ARIA2_SECRET = os.environ.get("ARIA2_SECRET", "gjxml")

OWNER_PREMIUM    = os.environ.get("OWNER_PREMIUM", "false").lower() == "true"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024 if OWNER_PREMIUM else 2 * 1024 * 1024 * 1024
MAX_UPLOAD_LABEL = "4GB" if OWNER_PREMIUM else "2GB"

PORT = int(os.environ.get("PORT", "8000"))

ENGINE_DL      = "ARIA2 v1.36.0"
ENGINE_UL      = "Pyro v2.2.18"
ENGINE_EXTRACT = "py7zr / zipfile"

app = Client(
    "leech_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=100,
    max_concurrent_transmissions=5
)
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret=ARIA2_SECRET))

active_downloads = {}
executor = ThreadPoolExecutor(max_workers=4)


# ─────────────────────────────────────────────────────────────────────────────
#  Task class  ── stores LIVE state for every phase so Refresh is always fresh
# ─────────────────────────────────────────────────────────────────────────────
class DownloadTask:
    def __init__(self, gid, user_id, message_id, extract=False):
        self.gid         = gid
        self.user_id     = user_id
        self.message_id  = message_id
        self.extract     = extract
        self.cancelled   = False
        self.start_time  = time.time()
        self.file_path   = None
        self.extract_dir = None
        self.filename    = ""
        self.file_size   = 0
        self._last_edit_time = 0
        self._edit_count     = 0

        # current phase tracker
        self.current_phase = "dl"   # "dl" | "ext" | "ul"

        # live download snapshot (updated every progress tick)
        self.dl = {
            "filename": "", "progress": 0.0, "speed": 0,
            "downloaded": 0, "total": 0, "elapsed": 0,
            "eta": 0, "peer_line": "",
        }

        # live extraction snapshot
        self.ext = {
            "filename": "", "pct": 0.0, "speed": 0,
            "extracted": 0, "total": 0, "elapsed": 0,
            "remaining": 0, "cur_file": "", "file_index": 0,
            "total_files": 0, "archive_size": 0,
        }

        # live upload snapshot
        self.ul = {
            "filename": "", "uploaded": 0, "total": 0,
            "speed": 0, "elapsed": 0, "eta": 0,
            "file_index": 1, "total_files": 1,
            "file_map": {},     # {index: (uploaded, total)}
            "file_list": [],    # [(index, path), ...]
        }

    def can_edit(self):
        now = time.time()
        if now - self._last_edit_time > 60:
            self._edit_count     = 0
            self._last_edit_time = now
            return True
        if self._edit_count < 15:
            self._edit_count += 1
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Filename cleaner
# ─────────────────────────────────────────────────────────────────────────────
def clean_filename(filename: str) -> str:
    cleaned = re.sub(r'^\[?(?:www\.|WWW\.)[^\]\s]+\]?\s*[-–—_]\s*', '', filename)
    if cleaned == filename:
        cleaned = re.sub(r'^(?:www\.|WWW\.)[a-zA-Z0-9.-]+?\.', '', filename)
    return cleaned.strip() if cleaned.strip() else filename


# ─────────────────────────────────────────────────────────────────────────────
#  Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────
def create_progress_bar(percentage: float) -> str:
    if percentage >= 100:
        return "[●●●●●●●●●●] 100%"
    filled = int(percentage / 10)
    return f"[{'●' * filled}{'○' * (10 - filled)}] {percentage:.1f}%"

def format_speed(speed: float) -> str:
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.2f}MB/s"
    elif speed >= 1024:
        return f"{speed / 1024:.2f}KB/s"
    return "0 B/s"

def format_size(size_bytes: int) -> str:
    gb = size_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f}GB"
    return f"{size_bytes / (1024 ** 2):.2f}MB"

def format_time(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h{m}m{s}s"
    elif m > 0:
        return f"{m}m{s}s"
    return f"{s}s"

def get_system_stats() -> dict:
    cpu  = psutil.cpu_percent(interval=0.1)
    ram  = psutil.virtual_memory()
    up   = time.time() - psutil.boot_time()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    disk = psutil.disk_usage(DOWNLOAD_DIR)
    return {
        'cpu': cpu, 'ram_percent': ram.percent,
        'uptime': format_time(up),
        'disk_free': disk.free / (1024 ** 3),
        'disk_free_pct': 100.0 - disk.percent,
    }

def bot_stats_block(stats: dict) -> str:
    return (
        f"© **Bot Stats**\n"
        f"├ **CPU** → {stats['cpu']:.1f}% | **F** → {stats['disk_free']:.2f}GB [{stats['disk_free_pct']:.1f}%]\n"
        f"└ **RAM** → {stats['ram_percent']:.1f}% | **UP** → {stats['uptime']}"
    )

def get_user_label(message, task) -> str:
    try:
        if message.chat.username:
            return f"@{message.chat.username} ( #ID{task.user_id} )"
    except Exception:
        pass
    return f"#ID{task.user_id}"

def cleanup_files(task):
    try:
        if task.file_path and os.path.exists(task.file_path):
            if os.path.isfile(task.file_path):
                os.remove(task.file_path)
            else:
                shutil.rmtree(task.file_path, ignore_errors=True)
        if task.extract_dir and os.path.exists(task.extract_dir):
            shutil.rmtree(task.extract_dir, ignore_errors=True)
    except Exception as e:
        print(f"⚠ Cleanup error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Status text builders  ── called by both auto-update AND Refresh button
# ─────────────────────────────────────────────────────────────────────────────
def build_dl_text(task: DownloadTask, user_label: str) -> str:
    d         = task.dl
    gid_short = task.gid[:8]
    stats     = get_system_stats()
    size_text = (
        f"{format_size(d['downloaded'])} of {format_size(d['total'])}"
        if d['total'] > 0 else "Fetching file info…"
    )
    total_est = d['elapsed'] + d['eta']
    time_line = (
        f"{format_time(d['elapsed'])} of {format_time(total_est)} "
        f"( {format_time(d['eta'])} )"
    )
    return (
        f"**{d['filename'] or 'Connecting…'}**\n\n"
        f"**Task By** {user_label} [Link]\n"
        f"├ {create_progress_bar(d['progress'])}\n"
        f"├ **Processed** → {size_text}\n"
        f"├ **Status** → Download\n"
        f"├ **Speed** → {format_speed(d['speed'])}\n"
        f"├ **Time** → {time_line}\n"
        f"{d['peer_line']}"
        f"├ **Engine** → {ENGINE_DL}\n"
        f"├ **In Mode** → #ARIA2\n"
        f"├ **Out Mode** → #Leech\n"
        f"└ **Stop** → /stop_{gid_short}\n\n"
        f"{bot_stats_block(stats)}"
    )


def build_ext_text(task: DownloadTask, user_label: str) -> str:
    e     = task.ext
    stats = get_system_stats()
    return (
        f"**{e['filename'] or 'Extracting…'}**\n\n"
        f"**Task By** {user_label} [Link]\n"
        f"├ {create_progress_bar(e['pct'])}\n"
        f"├ **Processed** → {format_size(e['extracted'])} of {format_size(e['total'])}\n"
        f"├ **Status** → Extracting\n"
        f"├ **Speed** → {format_speed(e['speed'])}\n"
        f"├ **Time** → {format_time(e['elapsed'])} ( {format_time(e['remaining'])} )\n"
        f"├ **File** → `{e['cur_file']}` [{e['file_index']}/{e['total_files']}]\n"
        f"├ **Engine** → {ENGINE_EXTRACT}\n"
        f"├ **In Mode** → #Extract\n"
        f"├ **Out Mode** → #Leech\n"
        f"└ **Archive Size** → {format_size(e['archive_size'])}\n\n"
        f"{bot_stats_block(stats)}"
    )


def build_ul_text(task: DownloadTask, user_label: str) -> str:
    u         = task.ul
    stats     = get_system_stats()
    gid_short = task.gid[:8]

    # single file
    if u['total_files'] <= 1:
        pct       = min((u['uploaded'] / u['total']) * 100, 100) if u['total'] > 0 else 0
        time_line = f"of {format_time(u['elapsed'] + u['eta'])} ( {format_time(u['elapsed'])} )"
        return (
            f"**{clean_filename(u['filename'])}**\n\n"
            f"**Task By** {user_label} [Link]\n"
            f"├ {create_progress_bar(pct)}\n"
            f"├ **Processed** → {format_size(u['uploaded'])} of {format_size(u['total'])}\n"
            f"├ **Status** → Upload\n"
            f"├ **Speed** → {format_speed(u['speed'])}\n"
            f"├ **Time** → {time_line}\n"
            f"├ **Engine** → {ENGINE_UL}\n"
            f"├ **In Mode** → #Aria2\n"
            f"├ **Out Mode** → #Leech\n"
            f"└ **Stop** → /stop_{gid_short}\n\n"
            f"{bot_stats_block(stats)}"
        )

    # multi-file
    file_map    = u['file_map']
    file_list   = u['file_list']
    total_files = u['total_files']
    total_up    = sum(v[0] for v in file_map.values())
    total_tot   = sum(v[1] for v in file_map.values())
    overall_pct = min((total_up / total_tot) * 100, 100) if total_tot > 0 else 0

    lines = [
        f"**Task By** {user_label} [Link]\n",
        f"├ **Overall** {create_progress_bar(overall_pct)}\n",
        f"├ **Processed** → {format_size(total_up)} of {format_size(total_tot)}\n",
        f"├ **Status** → Upload ({total_files} files)\n",
        f"├ **Time** → {format_time(u['elapsed'])}\n",
        f"├ **Engine** → {ENGINE_UL}\n",
        f"├ **In Mode** → #Aria2\n",
        f"├ **Out Mode** → #Leech\n",
    ]
    for idx, fp in file_list[:3]:
        fname   = clean_filename(os.path.basename(fp))
        up, tot = file_map.get(idx, (0, os.path.getsize(fp)))
        pct     = min((up / tot) * 100, 100) if tot > 0 else 0
        lines.append(f"├ `{fname}` {format_size(up)}/{format_size(tot)} {create_progress_bar(pct)}\n")
    if total_files > 3:
        lines.append(f"├ ... and {total_files - 3} more files\n")
    lines.append(f"└ **Stop** → /stop_{gid_short}\n\n{bot_stats_block(stats)}")
    return "".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Inline keyboard helpers
# ─────────────────────────────────────────────────────────────────────────────
def stop_keyboard(gid: str, phase: str) -> InlineKeyboardMarkup:
    """Refresh + Stop buttons."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{phase}:{gid}"),
        InlineKeyboardButton("🛑 Stop",    callback_data=f"stop:{gid}"),
    ]])

def refresh_only_keyboard(gid: str, phase: str) -> InlineKeyboardMarkup:
    """Only a Refresh button (extraction — no stop mid-extract)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{phase}:{gid}"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
#  Safe message edit
# ─────────────────────────────────────────────────────────────────────────────
async def safe_edit_message(message, text, task=None, reply_markup=None):
    try:
        if task and not task.can_edit():
            return False
        kwargs = {"text": text}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await message.edit_text(**kwargs)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        try:
            kwargs = {"text": text}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            await message.edit_text(**kwargs)
            return True
        except Exception:
            return False
    except MessageNotModified:
        return True
    except Exception as e:
        print(f"⚠ Edit error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  🔄 Refresh callback  ── rebuilds text from LIVE task state every single tap
# ─────────────────────────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^refresh:"))
async def refresh_callback(client, callback_query: CallbackQuery):
    try:
        _, phase, gid = callback_query.data.split(":", 2)
    except ValueError:
        await callback_query.answer("❌ Invalid data.", show_alert=True)
        return

    task = active_downloads.get(gid)
    if not task:
        await callback_query.answer("⚠️ Task finished or not found.", show_alert=True)
        return

    user_label = f"#ID{task.user_id}"

    try:
        if phase == "dl":
            # Pull FRESH data directly from aria2 right now
            try:
                download = aria2.get_download(gid)
                task.dl["progress"]   = download.progress or 0.0
                task.dl["speed"]      = download.download_speed or 0
                task.dl["total"]      = download.total_length or 0
                task.dl["downloaded"] = download.completed_length or 0
                _eta = download.eta
                task.dl["eta"]     = _eta.total_seconds() if _eta and _eta.total_seconds() > 0 else 0
                task.dl["elapsed"] = time.time() - task.start_time
                raw = download.name if download.name else "Connecting…"
                task.dl["filename"] = clean_filename(raw)
                try:
                    seeders = getattr(download, 'num_seeders', None)
                    if seeders and seeders > 0:
                        task.dl["peer_line"] = (
                            f"├ **Seeders** → {seeders} | **Leechers** → {download.connections or 0}\n"
                        )
                    else:
                        task.dl["peer_line"] = f"├ **Connections** → {download.connections or 0}\n"
                except Exception:
                    task.dl["peer_line"] = ""
            except Exception as aria_err:
                await callback_query.answer(f"❌ aria2 error: {aria_err}", show_alert=True)
                return

            text = build_dl_text(task, user_label)
            kb   = stop_keyboard(gid, "dl")

        elif phase == "ext":
            # Recalculate elapsed/remaining from current wall-clock time
            now = time.time()
            task.ext["elapsed"] = now - task.start_time
            if task.ext["speed"] > 0 and task.ext["total"] > 0:
                task.ext["remaining"] = max(
                    (task.ext["total"] - task.ext["extracted"]) / task.ext["speed"], 0
                )
            text = build_ext_text(task, user_label)
            kb   = refresh_only_keyboard(gid, "ext")

        elif phase == "ul":
            # Recalculate elapsed and ETA from current time
            now = time.time()
            task.ul["elapsed"] = now - task.start_time
            if task.ul["total_files"] <= 1 and task.ul["speed"] > 0:
                remaining = task.ul["total"] - task.ul["uploaded"]
                task.ul["eta"] = max(remaining / task.ul["speed"], 0) if remaining > 0 else 0
            text = build_ul_text(task, user_label)
            kb   = stop_keyboard(gid, "ul")

        else:
            await callback_query.answer("❌ Unknown phase.", show_alert=True)
            return

        await callback_query.edit_message_text(text, reply_markup=kb)
        await callback_query.answer("✅ Refreshed!")

    except MessageNotModified:
        await callback_query.answer("ℹ️ Already up to date.")
    except Exception as e:
        await callback_query.answer(f"❌ Error: {e}", show_alert=True)


# ─────────────────────────────────────────────────────────────────────────────
#  🛑 Stop via inline button
# ─────────────────────────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^stop:"))
async def stop_callback(client, callback_query: CallbackQuery):
    try:
        _, gid = callback_query.data.split(":", 1)
    except ValueError:
        await callback_query.answer("❌ Invalid data.", show_alert=True)
        return

    task = active_downloads.get(gid)
    if not task:
        await callback_query.answer("⚠️ Already finished.", show_alert=True)
        return

    task.cancelled = True
    try:
        download = aria2.get_download(gid)
        aria2.remove([download], force=True, files=True)
        cleanup_files(task)
    except Exception as e:
        print(f"Stop callback error: {e}")

    active_downloads.pop(gid, None)
    await callback_query.answer("🛑 Task cancelled!")
    await callback_query.edit_message_text("❌ **Download cancelled**\n✅ **Files cleaned up!**")


# ─────────────────────────────────────────────────────────────────────────────
#  Download progress loop  ── writes into task.dl every tick
# ─────────────────────────────────────────────────────────────────────────────
async def update_progress(task: DownloadTask, message):
    try:
        await asyncio.sleep(2)
        update_count  = 0
        last_progress = -1
        task.current_phase = "dl"

        while not task.cancelled:
            try:
                download = aria2.get_download(task.gid)
                if download.is_complete:
                    break

                progress   = download.progress or 0.0
                speed      = download.download_speed or 0
                total_size = download.total_length or 0
                downloaded = download.completed_length or 0
                _eta       = download.eta
                eta        = _eta.total_seconds() if _eta and _eta.total_seconds() > 0 else 0
                elapsed    = time.time() - task.start_time
                filename   = clean_filename(download.name if download.name else "Connecting…")

                # write live snapshot — Refresh reads this directly
                task.filename  = filename
                task.file_size = total_size
                task.dl.update({
                    "filename": filename, "progress": progress,
                    "speed": speed, "downloaded": downloaded, "total": total_size,
                    "elapsed": elapsed, "eta": eta,
                })
                try:
                    seeders = getattr(download, 'num_seeders', None)
                    if seeders and seeders > 0:
                        task.dl["peer_line"] = (
                            f"├ **Seeders** → {seeders} | **Leechers** → {download.connections or 0}\n"
                        )
                    else:
                        task.dl["peer_line"] = f"├ **Connections** → {download.connections or 0}\n"
                except Exception:
                    task.dl["peer_line"] = ""

                if abs(progress - last_progress) < 0.5 and progress < 100:
                    await asyncio.sleep(2)
                    continue
                last_progress = progress

                if update_count % 3 == 0 or progress >= 100:
                    user_label = get_user_label(message, task)
                    text = build_dl_text(task, user_label)
                    await safe_edit_message(
                        message, text, task,
                        reply_markup=stop_keyboard(task.gid, "dl")
                    )

            except Exception as iter_err:
                print(f"Progress iteration error: {iter_err}")

            update_count += 1
            await asyncio.sleep(3)

    except Exception as e:
        print(f"Progress update error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Extraction  ── writes into task.ext every tick
# ─────────────────────────────────────────────────────────────────────────────
async def extract_archive(file_path, extract_to, status_msg=None, task=None):
    try:
        raw_name   = os.path.basename(file_path)
        filename   = clean_filename(raw_name)
        total_size = os.path.getsize(file_path)
        start_time = time.time()
        last_render = [0.0]

        if task:
            task.current_phase = "ext"
            task.ext.update({
                "filename": filename, "archive_size": total_size, "total": total_size
            })

        async def _render(extracted_bytes, total_bytes, current_file, file_index, total_files):
            now = time.time()
            if now - last_render[0] < 3 and extracted_bytes < total_bytes:
                return
            last_render[0] = now

            elapsed   = time.time() - start_time
            speed     = extracted_bytes / elapsed if elapsed > 0 else 0
            remaining = (total_bytes - extracted_bytes) / speed if speed > 0 else 0
            pct       = min((extracted_bytes / total_bytes) * 100, 100) if total_bytes > 0 else 0
            cur_clean = clean_filename(os.path.basename(current_file))

            # write live snapshot
            if task:
                task.ext.update({
                    "pct": pct, "speed": speed,
                    "extracted": extracted_bytes, "total": total_bytes,
                    "elapsed": elapsed, "remaining": remaining,
                    "cur_file": cur_clean,
                    "file_index": file_index, "total_files": total_files,
                })

            if status_msg:
                user_label = get_user_label(status_msg, task) if task else "Unknown"
                text = build_ext_text(task, user_label)
                gid  = task.gid if task else "unknown"
                await safe_edit_message(
                    status_msg, text, task,
                    reply_markup=refresh_only_keyboard(gid, "ext")
                )

        # ── ZIP ───────────────────────────────────────────────────────────────
        if file_path.endswith('.zip'):
            loop = asyncio.get_event_loop()

            def extract_zip():
                with zipfile.ZipFile(file_path, 'r') as zf:
                    members   = zf.infolist()
                    n_files   = len(members)
                    unc_total = sum(m.file_size for m in members)
                    extracted = 0
                    for idx, member in enumerate(members, start=1):
                        zf.extract(member, extract_to)
                        extracted += member.file_size
                        if idx % 5 == 0 or idx == n_files:
                            asyncio.run_coroutine_threadsafe(
                                _render(extracted, unc_total, member.filename, idx, n_files), loop
                            )
                return True

            return await loop.run_in_executor(executor, extract_zip)

        # ── 7z ───────────────────────────────────────────────────────────────
        elif file_path.endswith('.7z'):
            with py7zr.SevenZipFile(file_path, mode='r') as archive:
                members   = archive.list()
                n_files   = len(members)
                total_unc = sum(getattr(m, 'uncompressed', 0) or 0 for m in members)
                extracted_bytes = 0
                update_tick     = 0

                class _CB(py7zr.callbacks.ExtractCallback):
                    def __init__(s):
                        s.file_index = 0
                        s.loop = asyncio.get_event_loop()
                    def report_start_preparation(s): pass
                    def report_start(s, p, b): s.file_index += 1
                    def report_update(s, b): pass
                    def report_end(s, p, wrote_bytes):
                        nonlocal extracted_bytes, update_tick
                        extracted_bytes += wrote_bytes
                        update_tick     += 1
                        if update_tick % 5 == 0:
                            asyncio.run_coroutine_threadsafe(
                                _render(extracted_bytes, total_unc or total_size,
                                        filename, s.file_index, n_files),
                                s.loop
                            )
                    def report_postprocess(s): pass
                    def report_warning(s, m): pass

                try:
                    archive.extractall(path=extract_to, callback=_CB())
                    await _render(total_unc or total_size, total_unc or total_size,
                                  filename, n_files, n_files)
                except TypeError:
                    archive.extractall(path=extract_to)
                    await _render(total_size, total_size, filename, 1, 1)
                return True

        # ── TAR ───────────────────────────────────────────────────────────────
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            import tarfile
            loop = asyncio.get_event_loop()

            def extract_tar():
                with tarfile.open(file_path, 'r:*') as tf:
                    members   = tf.getmembers()
                    n_files   = len(members)
                    total_unc = sum(m.size for m in members)
                    extracted = 0
                    for idx, member in enumerate(members, start=1):
                        tf.extract(member, extract_to)
                        extracted += member.size
                        if idx % 5 == 0 or idx == n_files:
                            asyncio.run_coroutine_threadsafe(
                                _render(extracted, total_unc, member.name, idx, n_files), loop
                            )
                return True

            return await loop.run_in_executor(executor, extract_tar)
        else:
            return False

    except Exception as e:
        print(f"Extraction error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Upload  ── writes into task.ul every tick
# ─────────────────────────────────────────────────────────────────────────────
async def upload_to_telegram(file_path, message, caption="", status_msg=None, task=None):

    if task:
        task.current_phase = "ul"

    try:
        gid_full  = task.gid     if task else "unknown"
        gid_short = task.gid[:8] if task else "unknown"

        # ── Single file ────────────────────────────────────────────────────────
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > MAX_UPLOAD_BYTES:
                await message.reply_text(f"❌ File too large for Telegram (>{MAX_UPLOAD_LABEL})")
                return False

            raw_name   = os.path.basename(file_path)
            start_time = time.time()
            last_render_time = [0.0]
            last_uploaded    = [0]
            last_tick_time   = [start_time]

            if task:
                task.ul.update({
                    "filename": raw_name, "uploaded": 0, "total": file_size,
                    "speed": 0, "elapsed": 0, "eta": 0,
                    "file_index": 1, "total_files": 1,
                })

            user_label = get_user_label(message, task) if task else "Unknown"

            async def _progress(current, total):
                now     = time.time()
                elapsed = now - start_time

                if now - last_render_time[0] < 5:
                    return

                dt    = now - last_tick_time[0]
                speed = (current - last_uploaded[0]) / dt if dt > 0 else 0
                eta   = (total - current) / speed if speed > 0 else 0

                last_tick_time[0]   = now
                last_uploaded[0]    = current
                last_render_time[0] = now

                # write live snapshot — Refresh reads this directly
                if task:
                    task.ul.update({
                        "filename": raw_name,
                        "uploaded": current, "total": total,
                        "speed": speed, "elapsed": elapsed, "eta": eta,
                        "file_index": 1, "total_files": 1,
                    })

                text = build_ul_text(task, user_label)
                await safe_edit_message(
                    status_msg, text, task,
                    reply_markup=stop_keyboard(gid_full, "ul")
                )

            # Initial 0% render
            text = build_ul_text(task, user_label)
            await safe_edit_message(
                status_msg, text, task,
                reply_markup=stop_keyboard(gid_full, "ul")
            )

            await message.reply_document(
                document=file_path,
                caption=caption or clean_filename(raw_name),
                progress=_progress,
                disable_notification=True
            )

            elapsed = time.time() - start_time
            if task:
                task.ul.update({
                    "uploaded": file_size, "speed": 0, "elapsed": elapsed, "eta": 0
                })
            return True

        # ── Directory (multi-file) ─────────────────────────────────────────────
        elif os.path.isdir(file_path):
            files = [
                os.path.join(root, fname)
                for root, _, filenames in os.walk(file_path)
                for fname in filenames
                if os.path.getsize(os.path.join(root, fname)) <= MAX_UPLOAD_BYTES
            ]
            total_files = len(files)
            if total_files == 0:
                await message.reply_text("❌ No uploadable files found.")
                return False

            file_map  = {i: (0, os.path.getsize(fp)) for i, fp in enumerate(files, start=1)}
            file_list = list(enumerate(files, start=1))
            start_time = time.time()
            last_render = [0.0]

            if task:
                task.ul.update({
                    "total_files": total_files,
                    "file_map":    file_map,
                    "file_list":   file_list,
                    "elapsed":     0,
                })

            user_label = get_user_label(message, task) if task else "Unknown"

            async def _render_multi():
                now = time.time()
                if now - last_render[0] < 10:
                    return
                last_render[0] = now
                if task:
                    task.ul["elapsed"]  = now - start_time
                    task.ul["file_map"] = file_map
                text = build_ul_text(task, user_label)
                await safe_edit_message(
                    status_msg, text, task,
                    reply_markup=stop_keyboard(gid_full, "ul")
                )

            async def _upload_one(file_index, fpath):
                fsize     = os.path.getsize(fpath)
                raw_name  = os.path.basename(fpath)
                clean_cap = clean_filename(raw_name)

                async def _progress(current, total):
                    file_map[file_index] = (current, total)
                    if task:
                        task.ul["file_map"] = file_map
                    if time.time() - last_render[0] >= 10:
                        await _render_multi()

                file_map[file_index] = (0, fsize)
                await message.reply_document(
                    document=fpath,
                    caption=f"📄 {clean_cap}  [{file_index}/{total_files}]" + (f"\n{caption}" if caption else ""),
                    progress=_progress,
                    disable_notification=True
                )
                file_map[file_index] = (fsize, fsize)

            await _render_multi()

            semaphore = asyncio.Semaphore(3)

            async def _upload_with_limit(idx, fp):
                async with semaphore:
                    return await _upload_one(idx, fp)

            await asyncio.gather(*[_upload_with_limit(i, fp) for i, fp in file_list])

            last_render[0] = 0
            await _render_multi()
            return True

    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Bot commands
# ─────────────────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    help_text = (
        "**🤖 Leech Bot - Help**\n\n"
        "**📥 Download Commands:**\n"
        "• `/leech <link>` - Download direct link\n"
        "• `/l <link>` - Short for /leech\n"
        "• `/leech <link> -e` - Download & extract archive\n\n"
        f"**✨ Features:**\n"
        f"✓ Direct links (HTTP/HTTPS/FTP)\n"
        f"✓ Auto extraction (.zip, .7z, .tar.gz)\n"
        f"✓ Live progress — Download / Extract / Upload\n"
        f"✓ 🔄 **Refresh** — tap to get live stats instantly\n"
        f"✓ 🛑 **Stop** — cancel task from inline button\n"
        f"✓ Concurrent multi-file uploads\n"
        f"✓ CPU / RAM / Disk monitoring\n"
        f"✓ Auto cleanup after upload\n"
        f"✓ Site-name prefix auto-removed from filenames\n"
        f"✓ Max upload: **{MAX_UPLOAD_LABEL}** ({'Premium ⭐' if OWNER_PREMIUM else 'Standard'})\n\n"
        "**📖 Examples:**\n"
        "`/leech https://example.com/file.zip`\n"
        "`/l https://example.com/archive.7z -e`\n"
    )
    await message.reply_text(help_text)


@app.on_message(filters.command(["leech", "l"]))
async def leech_command(client, message: Message):
    gid  = None
    task = None
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/leech <link>` or `/leech <link> -e`")
            return

        url     = args[1].split()[0]
        extract = "-e" in message.text.lower()

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        status_msg = await message.reply_text("🔄 **Starting download...**")

        try:
            download = aria2.add_uris([url], options={"dir": DOWNLOAD_DIR})
            gid      = download.gid
            task     = DownloadTask(gid, message.from_user.id, status_msg.id, extract)
            active_downloads[gid] = task

            asyncio.create_task(update_progress(task, status_msg))

            while not download.is_complete and not task.cancelled:
                await asyncio.sleep(1)
                download.update()

            if task.cancelled:
                await safe_edit_message(status_msg, "❌ **Download cancelled**\n🧹 **Cleaning up...**", task)
                try:
                    aria2.remove([download], force=True, files=True)
                except Exception:
                    pass
                cleanup_files(task)
                active_downloads.pop(gid, None)
                await safe_edit_message(status_msg, "❌ **Download cancelled**\n✅ **Files cleaned up!**", task)
                return

            await safe_edit_message(
                status_msg, "✅ **Download completed!**\n📤 **Starting upload...**", task
            )
            download.update()
            file_path      = os.path.join(DOWNLOAD_DIR, download.name)
            task.file_path = file_path

            if extract and file_path.endswith(('.zip', '.7z', '.tar.gz', '.tgz', '.tar')):
                extract_dir      = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir
                await safe_edit_message(status_msg, "📦 **Starting extraction...**", task)
                if await extract_archive(file_path, extract_dir, status_msg=status_msg, task=task):
                    await safe_edit_message(
                        status_msg, "✅ **Extraction done!**\n📤 **Uploading to Telegram...**", task
                    )
                    await upload_to_telegram(
                        extract_dir, message,
                        caption="📁 Extracted files",
                        status_msg=status_msg, task=task
                    )
                else:
                    await safe_edit_message(
                        status_msg, "❌ **Extraction failed!** Uploading original...", task
                    )
                    await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)
            else:
                await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)

            await safe_edit_message(
                status_msg, "✅ **Upload completed!**\n🧹 **Cleaning up files...**", task
            )
            cleanup_files(task)
            await safe_edit_message(status_msg, "✅ **Task completed successfully!**", task)
            active_downloads.pop(gid, None)

        except Exception as e:
            await safe_edit_message(
                status_msg, f"❌ **Error:** `{str(e)}`\n🧹 **Cleaning up...**", task
            )
            if task:
                cleanup_files(task)
            active_downloads.pop(gid, None)

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")
        if task:
            cleanup_files(task)
        active_downloads.pop(gid, None)


@app.on_message(filters.command(["stop"]) | filters.regex(r"^/stop_\w+"))
async def stop_command(client, message: Message):
    try:
        text = message.text or ""
        if text.startswith("/stop_"):
            gid_short = text.split("_", 1)[1].strip()
        else:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.reply_text("❌ **Usage:** `/stop <task_id>`")
                return
            gid_short = parts[1].strip()

        found_task = found_gid = None
        for gid, task_item in active_downloads.items():
            if gid.startswith(gid_short) or gid[:8] == gid_short:
                found_task = task_item
                found_gid  = gid
                break

        if not found_task:
            await message.reply_text(f"❌ **Task `{gid_short}` not found or already completed!**")
            return

        found_task.cancelled = True
        try:
            download = aria2.get_download(found_gid)
            aria2.remove([download], force=True, files=True)
            cleanup_files(found_task)
        except Exception as e:
            print(f"Stop error: {e}")

        active_downloads.pop(found_gid, None)
        await message.reply_text(f"✅ **Task `{gid_short}` cancelled & files cleaned!**")

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")


# ─────────────────────────────────────────────────────────────────────────────
#  Koyeb keep-alive
# ─────────────────────────────────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(
        text=(
            "✅ Leech Bot is alive\n"
            f"Active downloads: {len(active_downloads)}\n"
            f"Upload limit: {MAX_UPLOAD_LABEL} ({'Premium' if OWNER_PREMIUM else 'Standard'})"
        ),
        content_type="text/plain",
    )

async def start_web_server():
    web_app = web.Application()
    web_app.router.add_get("/",       health_handler)
    web_app.router.add_get("/health", health_handler)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Keep-alive server on port {PORT}")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────
async def main():
    print("🚀 Starting Leech Bot...")
    print(f"📦 Max upload: {MAX_UPLOAD_LABEL} ({'Premium' if OWNER_PREMIUM else 'Standard'})")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await app.start()
    await start_web_server()
    print("🤖 Bot ready — listening for commands...")
    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
