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

try:
    import tgcrypto
    print("✅ TgCrypto installed - UPLOAD SPEED BOOST ACTIVE")
except ImportError:
    print("⚠️ TgCrypto not installed! Uploads will be VERY SLOW. Install with: pip install tgcrypto")

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

# ─────────────────────────────────────────────────────────────────────────────
#  🚀 SUPERCHARGED Trackers — Maximum Peer Discovery
# ─────────────────────────────────────────────────────────────────────────────
TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce,"
    "udp://tracker.openbittorrent.com:6969/announce,"
    "http://tracker.openbittorrent.com:80/announce,"
    "udp://tracker.torrent.eu.org:451/announce,"
    "udp://exodus.desync.com:6969/announce,"
    "udp://tracker.cyberia.is:6969/announce,"
    "udp://open.demonii.com:1337/announce,"
    "udp://9.rarbg.com:2810/announce,"
    "udp://tracker.moeking.me:6969/announce,"
    "udp://tracker.lelux.fi:6969/announce,"
    "udp://retracker.lanta-net.ru:2710/announce,"
    "udp://opentor.net:2710/announce,"
    "udp://tracker.dler.org:6969/announce,"
    "udp://tracker.tiny-vps.com:6969/announce,"
    "https://tracker.tamersunion.org:443/announce,"
    "https://tracker.loligirl.cn:443/announce,"
    "udp://tracker.theoks.net:6969/announce,"
    "udp://tracker1.bt.moack.co.kr:80/announce,"
    "udp://open.stealth.si:80/announce,"
    "udp://tracker.zemoj.com:6969/announce"
)

# ─────────────────────────────────────────────────────────────────────────────
#  🚀 SUPERCHARGED Aria2 Options — Maximum Torrent/Magnet Speed
# ─────────────────────────────────────────────────────────────────────────────
BT_OPTIONS = {
    "dir": DOWNLOAD_DIR,
    "seed-time": "0",

    # Disk I/O — eliminate all pre-allocation delay
    "disk-cache": "64M",
    "file-allocation": "none",

    # Peer connections — maximum aggression
    "bt-max-peers": "200",
    "bt-request-peer-speed-limit": "50M",
    "max-connection-per-server": "16",
    "split": "16",
    "min-split-size": "1M",

    # DHT / peer exchange — maximum discovery
    "enable-dht": "true",
    "enable-dht6": "true",
    "enable-peer-exchange": "true",
    "bt-enable-lpd": "true",            # Local Peer Discovery

    # Piece selection — pick rarest pieces first for speed
    "bt-prioritize-piece": "head=2M,tail=2M",

    # Remove slow peers faster, connect faster
    "bt-remove-unselected-file": "true",
    "peer-agent": "aria2/1.36.0",

    # Network tuning
    "max-overall-download-limit": "0",  # No speed cap
    "max-overall-upload-limit": "1K",   # Minimal upload to free up bandwidth

    "bt-tracker": TRACKERS,
}

# Direct link options (HTTP/FTP) — also supercharged
DIRECT_OPTIONS = {
    "dir": DOWNLOAD_DIR,
    "disk-cache": "64M",
    "file-allocation": "none",
    "max-connection-per-server": "16",
    "split": "16",
    "min-split-size": "1M",
    "max-overall-download-limit": "0",
}

app = Client(
    "leech_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=200,
    max_concurrent_transmissions=10
)
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret=ARIA2_SECRET))

active_downloads = {}
user_settings    = {}  # {user_id: {"as_video": True}}
executor         = ThreadPoolExecutor(max_workers=4)


# ─────────────────────────────────────────────────────────────────────────────
#  Task class
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
        self.current_phase   = "dl"

        self.dl = {
            "filename": "", "progress": 0.0, "speed": 0,
            "downloaded": 0, "total": 0, "elapsed": 0,
            "eta": 0, "peer_line": "",
        }

        self.ext = {
            "filename": "", "pct": 0.0, "speed": 0,
            "extracted": 0, "total": 0, "elapsed": 0,
            "remaining": 0, "cur_file": "", "file_index": 0,
            "total_files": 0, "archive_size": 0,
        }

        self.ul = {
            "filename": "", "uploaded": 0, "total": 0,
            "speed": 0, "elapsed": 0, "eta": 0,
            "file_index": 1, "total_files": 1,
            "file_map": {},
            "file_list": [],
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
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def clean_filename(filename: str) -> str:
    cleaned = re.sub(r'^\[.*?\]\s*|^\(.*?\)\s*', '', filename)
    cleaned = re.sub(r'^@\w+\s*', '', cleaned)
    cleaned = re.sub(
        r'^(?:(?:https?://)?(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?\s*[-–_]*\s*)',
        '', cleaned, flags=re.IGNORECASE
    )
    cleaned = cleaned.strip() if cleaned.strip() else filename
    max_length = 100
    if len(cleaned) > max_length:
        name, ext = os.path.splitext(cleaned)
        space_left = max_length - len(ext) - 3
        cleaned = (name[:space_left] + "..." + ext) if space_left > 0 else cleaned[:max_length]
    return cleaned

def create_progress_bar(percentage: float) -> str:
    if percentage >= 100:
        return "[⬢⬢⬢⬢⬢⬢⬢⬢⬢⬢⬢⬢] 100%"
    filled = int(percentage / 100 * 12)
    return f"[{chr(11042) * filled}{chr(11041) * (12 - filled)}] {percentage:.1f}%"

def format_speed(speed: float) -> str:
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.2f} MB/s"
    elif speed >= 1024:
        return f"{speed / 1024:.2f} KB/s"
    return "0 B/s"

def format_size(size_bytes: int) -> str:
    gb = size_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f} GB"
    return f"{size_bytes / (1024 ** 2):.2f} MB"

def format_time(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
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
#  Status Text Builders
# ─────────────────────────────────────────────────────────────────────────────
def build_dl_text(task: DownloadTask, user_label: str) -> str:
    d         = task.dl
    gid_short = task.gid[:8]
    stats     = get_system_stats()

    size_text = "Fetching Metadata/Peers..." if d['total'] == 0 else \
        f"{format_size(d['downloaded'])} of {format_size(d['total'])}"
    time_line = f"Elapsed: {format_time(d['elapsed'])} | ETA: {format_time(d['eta'])}"

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
        f"└ **Stop** → `/stop_{gid_short}`\n\n"
        f"{bot_stats_block(stats)}"
    )


# ── Extraction UI now matches Download/Upload style exactly ──────────────────
def build_ext_text(task: DownloadTask, user_label: str) -> str:
    e         = task.ext
    gid_short = task.gid[:8]
    stats     = get_system_stats()

    size_text = f"{format_size(e['extracted'])} of {format_size(e['total'])}" \
        if e['total'] > 0 else "Preparing..."
    time_line = f"Elapsed: {format_time(e['elapsed'])} | ETA: {format_time(e['remaining'])}"
    file_line = f"`{e['cur_file']}`" if e['cur_file'] else "Preparing..."
    files_tag = f"[{e['file_index']}/{e['total_files']}]" if e['total_files'] > 0 else ""

    return (
        f"**{e['filename'] or 'Extracting…'}**\n\n"
        f"**Task By** {user_label} [Link]\n"
        f"├ {create_progress_bar(e['pct'])}\n"
        f"├ **Processed** → {size_text}\n"
        f"├ **Status** → Extracting {files_tag}\n"
        f"├ **Speed** → {format_speed(e['speed'])}\n"
        f"├ **Time** → {time_line}\n"
        f"├ **File** → {file_line}\n"
        f"├ **Engine** → {ENGINE_EXTRACT}\n"
        f"├ **In Mode** → #Extract\n"
        f"├ **Out Mode** → #Leech\n"
        f"└ **Stop** → `/stop_{gid_short}`\n\n"
        f"{bot_stats_block(stats)}"
    )


def build_ul_text(task: DownloadTask, user_label: str) -> str:
    u         = task.ul
    gid_short = task.gid[:8]
    stats     = get_system_stats()

    # ── Single file ──────────────────────────────────────────────────────────
    if u['total_files'] <= 1:
        pct       = min((u['uploaded'] / u['total']) * 100, 100) if u['total'] > 0 else 0
        time_line = f"Elapsed: {format_time(u['elapsed'])} | ETA: {format_time(u['eta'])}"
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
            f"└ **Stop** → `/stop_{gid_short}`\n\n"
            f"{bot_stats_block(stats)}"
        )

    # ── Multi-file ───────────────────────────────────────────────────────────
    file_map    = u['file_map']
    file_list   = u['file_list']
    total_files = u['total_files']
    total_up    = sum(v[0] for v in file_map.values())
    total_tot   = sum(v[1] for v in file_map.values())
    overall_pct = min((total_up / total_tot) * 100, 100) if total_tot > 0 else 0

    overall_speed = total_up / u['elapsed'] if u['elapsed'] > 0 else 0
    overall_eta   = (total_tot - total_up) / overall_speed if overall_speed > 0 else 0
    time_line     = f"Elapsed: {format_time(u['elapsed'])} | ETA: {format_time(overall_eta)}"

    lines = [
        f"**Task By** {user_label} [Link]\n",
        f"├ **Overall** {create_progress_bar(overall_pct)}\n",
        f"├ **Processed** → {format_size(total_up)} of {format_size(total_tot)}\n",
        f"├ **Status** → Upload ({total_files} files)\n",
        f"├ **Speed** → {format_speed(overall_speed)}\n",
        f"├ **Time** → {time_line}\n",
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
    lines.append(f"├ **Stop** → `/stop_{gid_short}`\n└ **Done** → {total_files} files\n\n{bot_stats_block(stats)}")
    return "".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Keyboards — Refresh ONLY (Stop button removed from all progress UIs)
# ─────────────────────────────────────────────────────────────────────────────
def refresh_only_keyboard(gid: str, phase: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{phase}:{gid}"),
    ]])

# Kept for /stop command only — not shown in progress UIs
def stop_keyboard_hidden(gid: str, phase: str) -> InlineKeyboardMarkup:
    """Only used internally if we ever want to re-add. Not rendered in progress."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{phase}:{gid}"),
    ]])


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
#  Callbacks (Refresh / Stop)
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
            try:
                download = aria2.get_download(task.gid)
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
                        task.dl["peer_line"] = f"├ **Seeders** → {seeders} | **Leechers** → {download.connections or 0}\n"
                    else:
                        task.dl["peer_line"] = f"├ **Connections** → {download.connections or 0}\n"
                except Exception:
                    task.dl["peer_line"] = ""
            except Exception as aria_err:
                await callback_query.answer(f"❌ aria2 error: {aria_err}", show_alert=True)
                return

            text = build_dl_text(task, user_label)
            kb   = refresh_only_keyboard(task.gid, "dl")

        elif phase == "ext":
            now = time.time()
            task.ext["elapsed"] = now - task.start_time
            if task.ext["speed"] > 0 and task.ext["total"] > 0:
                task.ext["remaining"] = max((task.ext["total"] - task.ext["extracted"]) / task.ext["speed"], 0)
            text = build_ext_text(task, user_label)
            kb   = refresh_only_keyboard(task.gid, "ext")

        elif phase == "ul":
            now = time.time()
            task.ul["elapsed"] = now - task.start_time
            if task.ul["total_files"] <= 1 and task.ul["speed"] > 0:
                remaining = task.ul["total"] - task.ul["uploaded"]
                task.ul["eta"] = max(remaining / task.ul["speed"], 0) if remaining > 0 else 0
            text = build_ul_text(task, user_label)
            kb   = refresh_only_keyboard(task.gid, "ul")

        else:
            await callback_query.answer("❌ Unknown phase.", show_alert=True)
            return

        await callback_query.edit_message_text(text, reply_markup=kb)
        await callback_query.answer("")

    except MessageNotModified:
        await callback_query.answer("ℹ️ Already up to date.")
    except Exception as e:
        await callback_query.answer(f"❌ Error: {e}", show_alert=True)


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
        download = aria2.get_download(task.gid)
        aria2.remove([download], force=True, files=True)
        cleanup_files(task)
    except Exception as e:
        print(f"Stop callback error: {e}")

    active_downloads.pop(gid, None)
    await callback_query.answer("🛑 Task cancelled!")
    await callback_query.edit_message_text("❌ **Download cancelled**\n✅ **Files cleaned up!**")


# ─────────────────────────────────────────────────────────────────────────────
#  Engines: Download, Extract, Upload
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
                        task.dl["peer_line"] = f"├ **Seeders** → {seeders} | **Leechers** → {download.connections or 0}\n"
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
                    # Refresh only — no stop button in UI
                    await safe_edit_message(message, text, task,
                                            reply_markup=refresh_only_keyboard(task.gid, "dl"))

            except Exception:
                pass

            update_count += 1
            await asyncio.sleep(3)

    except Exception as e:
        print(f"Progress update error: {e}")


async def extract_archive(file_path, extract_to, status_msg=None, task=None):
    try:
        raw_name   = os.path.basename(file_path)
        filename   = clean_filename(raw_name)
        total_size = os.path.getsize(file_path)
        start_time = time.time()
        last_render = [0.0]

        if task:
            task.current_phase = "ext"
            task.ext.update({"filename": filename, "archive_size": total_size, "total": total_size})

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

            if task:
                task.ext.update({
                    "pct": pct, "speed": speed,
                    "extracted": extracted_bytes, "total": total_bytes,
                    "elapsed": elapsed, "remaining": remaining,
                    "cur_file": cur_clean,
                    "file_index": file_index, "total_files": total_files,
                })

            if status_msg and task:
                user_label = get_user_label(status_msg, task)
                text = build_ext_text(task, user_label)
                # Refresh only — consistent with DL/UL
                await safe_edit_message(status_msg, text, task,
                                        reply_markup=refresh_only_keyboard(task.gid, "ext"))

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
                                _render(extracted, unc_total, member.filename, idx, n_files), loop)
                return True
            return await loop.run_in_executor(executor, extract_zip)

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
                                        filename, s.file_index, n_files), s.loop)
                    def report_postprocess(s): pass
                    def report_warning(s, m): pass

                try:
                    archive.extractall(path=extract_to, callback=_CB())
                    await _render(total_unc or total_size, total_unc or total_size, filename, n_files, n_files)
                except TypeError:
                    archive.extractall(path=extract_to)
                    await _render(total_size, total_size, filename, 1, 1)
                return True

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
                                _render(extracted, total_unc, member.name, idx, n_files), loop)
                return True
            return await loop.run_in_executor(executor, extract_tar)
        else:
            return False

    except Exception as e:
        print(f"Extraction error: {e}")
        return False


async def upload_to_telegram(file_path, message, caption="", status_msg=None, task=None):
    if task:
        task.current_phase = "ul"

    user_id  = message.from_user.id
    as_video = user_settings.get(user_id, {}).get("as_video", False)
    video_exts = ('.mp4', '.mkv', '.avi', '.webm')

    try:
        gid_full = task.gid if task else "unknown"

        # ── Single file ──────────────────────────────────────────────────────
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > MAX_UPLOAD_BYTES:
                await message.reply_text(f"❌ File too large (>{MAX_UPLOAD_LABEL})")
                return False

            raw_name   = os.path.basename(file_path)
            clean_name = clean_filename(raw_name)

            if raw_name != clean_name:
                new_path = os.path.join(os.path.dirname(file_path), clean_name)
                os.rename(file_path, new_path)
                file_path = new_path

            start_time       = time.time()
            last_render_time = [0.0]
            last_uploaded    = [0]
            last_tick_time   = [start_time]

            if task:
                task.ul.update({
                    "filename": clean_name, "uploaded": 0, "total": file_size,
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

                if task:
                    task.ul.update({
                        "filename": clean_name, "uploaded": current,
                        "total": total, "speed": speed,
                        "elapsed": elapsed, "eta": eta,
                    })

                text = build_ul_text(task, user_label)
                # Refresh only — no stop button
                await safe_edit_message(status_msg, text, task,
                                        reply_markup=refresh_only_keyboard(gid_full, "ul"))

            # Initial render
            text = build_ul_text(task, user_label)
            await safe_edit_message(status_msg, text, task,
                                    reply_markup=refresh_only_keyboard(gid_full, "ul"))

            final_caption = caption or clean_name
            is_video_file = file_path.lower().endswith(video_exts)

            if as_video and is_video_file:
                await message.reply_video(
                    video=file_path, caption=final_caption,
                    progress=_progress, supports_streaming=True,
                    disable_notification=True
                )
            else:
                await message.reply_document(
                    document=file_path, caption=final_caption,
                    progress=_progress, disable_notification=True
                )
            return True

        # ── Directory / multi-file ───────────────────────────────────────────
        elif os.path.isdir(file_path):
            files = [
                os.path.join(root, f)
                for root, _, filenames in os.walk(file_path)
                for f in filenames
                if os.path.getsize(os.path.join(root, f)) <= MAX_UPLOAD_BYTES
            ]

            if not files:
                await message.reply_text("❌ No uploadable files found.")
                return False

            start_time  = time.time()
            total_files = len(files)

            for index, fpath in enumerate(files, start=1):
                raw_name   = os.path.basename(fpath)
                clean_name = clean_filename(raw_name)

                if raw_name != clean_name:
                    new_path = os.path.join(os.path.dirname(fpath), clean_name)
                    os.rename(fpath, new_path)
                    fpath = new_path

                caption_text  = f"📄 {clean_name} [{index}/{total_files}]"
                is_video_file = fpath.lower().endswith(video_exts)

                if as_video and is_video_file:
                    await message.reply_video(
                        video=fpath, caption=caption_text, disable_notification=True)
                else:
                    await message.reply_document(
                        document=fpath, caption=caption_text, disable_notification=True)

            return True

    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Core Processing Engine
# ─────────────────────────────────────────────────────────────────────────────
async def process_task_execution(message: Message, status_msg: Message, download, extract: bool):
    gid  = download.gid
    task = DownloadTask(gid, message.from_user.id, status_msg.id, extract)
    active_downloads[gid] = task

    try:
        asyncio.create_task(update_progress(task, status_msg))

        while not task.cancelled:
            await asyncio.sleep(2)

            try:
                current_download = aria2.get_download(task.gid)
            except Exception:
                break

            # Magnet → real GID switch
            followed_by = getattr(current_download, 'followed_by', None)
            if followed_by:
                new_gid = followed_by[0].gid if hasattr(followed_by[0], 'gid') else followed_by[0]
                old_gid  = task.gid
                task.gid = new_gid
                active_downloads[new_gid] = task
                active_downloads.pop(old_gid, None)
                continue

            if current_download.is_complete:
                break
            elif getattr(current_download, 'has_failed', False):
                await safe_edit_message(
                    status_msg, f"❌ **Aria2 Error:** `{current_download.error_message}`", task)
                cleanup_files(task)
                active_downloads.pop(task.gid, None)
                return

        if task.cancelled:
            await safe_edit_message(
                status_msg, "❌ **Download cancelled**\n🧹 **Cleaning up...**", task)
            try:
                aria2.remove([aria2.get_download(task.gid)], force=True, files=True)
            except Exception:
                pass
            cleanup_files(task)
            active_downloads.pop(task.gid, None)
            return

        await safe_edit_message(
            status_msg, "✅ **Download completed!**\n📤 **Starting upload...**", task)

        try:
            final_download = aria2.get_download(task.gid)
            file_path = os.path.join(DOWNLOAD_DIR, final_download.name)
        except Exception:
            file_path = os.path.join(DOWNLOAD_DIR, task.dl["filename"])

        task.file_path = file_path

        if extract and file_path.endswith(('.zip', '.7z', '.tar.gz', '.tgz', '.tar')):
            extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
            os.makedirs(extract_dir, exist_ok=True)
            task.extract_dir = extract_dir
            await safe_edit_message(status_msg, "📦 **Starting extraction...**", task)

            if await extract_archive(file_path, extract_dir, status_msg=status_msg, task=task):
                await safe_edit_message(
                    status_msg, "✅ **Extraction done!**\n📤 **Uploading to Telegram...**", task)
                await upload_to_telegram(
                    extract_dir, message, caption="📁 Extracted files",
                    status_msg=status_msg, task=task)
            else:
                await safe_edit_message(
                    status_msg, "❌ **Extraction failed!** Uploading original...", task)
                await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)
        else:
            await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)

        await safe_edit_message(
            status_msg, "✅ **Upload completed!**\n🧹 **Cleaning up files...**", task)
        cleanup_files(task)
        active_downloads.pop(task.gid, None)

    except Exception as e:
        await safe_edit_message(
            status_msg, f"❌ **Error:** `{str(e)}`\n🧹 **Cleaning up...**", task)
        cleanup_files(task)
        active_downloads.pop(task.gid, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Commands
# ─────────────────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["leech", "l", "ql"]))
async def universal_leech_command(client, message: Message):
    extract = "-e" in message.text.lower()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Reply to .torrent file
    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        if doc.file_name.endswith(".torrent"):
            status_msg   = await message.reply_text("🔄 **Downloading torrent metadata...**")
            torrent_path = os.path.join(DOWNLOAD_DIR, f"{message.id}_{doc.file_name}")
            await message.reply_to_message.download(file_name=torrent_path)
            download = aria2.add_torrent(torrent_path, options=BT_OPTIONS)
            asyncio.create_task(process_task_execution(message, status_msg, download, extract))
            return

    args  = message.text.split()[1:]
    links = [a for a in args if a.startswith("http") or a.startswith("magnet:")]

    if not links:
        await message.reply_text(
            "❌ **Usage:** `/ql <link1> <magnet2>` or reply to a `.torrent` file.\n"
            "❌ **Usage:** `/l <link>` to download direct links")
        return

    for link in links:
        status_msg = await message.reply_text("🔄 **Adding to download queue...**")
        try:
            # Use BT_OPTIONS for magnets, DIRECT_OPTIONS for HTTP links
            opts = BT_OPTIONS if link.startswith("magnet:") else {**BT_OPTIONS, **DIRECT_OPTIONS}
            download = aria2.add_uris([link], options=opts)
            asyncio.create_task(process_task_execution(message, status_msg, download, extract))
        except Exception as e:
            await safe_edit_message(status_msg, f"❌ **Failed to add link:** `{str(e)}`")


@app.on_message(filters.command(["start"]))
async def start_command(client, message: Message):
    strt_txt = (
        "**🤖 Welcome to the Advanced Leech Bot!**\n\n"
        "I can download direct links, magnets, and `.torrent` files, and upload them directly to Telegram for you.\n\n"
        "Type /help to see all my features and commands.\n\n"
        "© Maintained By @im_goutham_josh"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Configure Upload Settings", callback_data=f"toggle_mode:{message.from_user.id}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")]
    ])
    await message.reply_text(strt_txt, reply_markup=kb)


@app.on_message(filters.command(["help"]))
async def help_command(client, message: Message):
    help_txt = (
        "**📖 Leech Bot - Help & Commands**\n\n"
        "**📥 Main Commands:**\n"
        "• `/ql <link1> <magnet2>` - Quick Leech (Download multiple links at once)\n"
        "• `/leech <link>` - Standard download\n"
        "• `/leech <link> -e` - Download & extract an archive (.zip, .7z, .tar)\n"
        "• **Upload a `.torrent` file** - Reply to it with `/ql` to begin\n\n"
        "**⚙️ Preferences & Control:**\n"
        "• `/settings` - Toggle between **📄 Document** and **🎬 Video** upload modes\n"
        "• `/stop <task_id>` - Cancel an active download/upload\n\n"
        "**✨ Features Active:**\n"
        "✓ Concurrent Multi-Downloading\n"
        "✓ Smart Filename Cleaning (Removes site URLs)\n"
        "✓ Auto-Extraction\n"
        "✓ High-Speed Telegram Uploads\n"
        "✓ 20 Supercharged Trackers\n"
        "✓ 200 Max Peers + LPD + DHT6"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Close", callback_data="close_help")]])
    await message.reply_text(help_txt, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^close_help$"))
async def close_help_callback(client, callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass


@app.on_message(filters.command(["settings"]))
async def settings_command(client, message: Message):
    user_id  = message.from_user.id
    as_video = user_settings.get(user_id, {}).get("as_video", False)
    mode_text = "🎬 Video (Playable)" if as_video else "📄 Document (File)"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"Toggle Mode: {mode_text}", callback_data=f"toggle_mode:{user_id}")
    ]])
    await message.reply_text(
        "⚙️ **Upload Settings**\n\n"
        "Choose how media files (.mp4, .mkv, .webm) should be sent to Telegram.",
        reply_markup=kb
    )


@app.on_callback_query(filters.regex(r"^toggle_mode:"))
async def toggle_mode_callback(client, callback_query: CallbackQuery):
    _, user_id_str = callback_query.data.split(":")
    user_id = int(user_id_str)

    if callback_query.from_user.id != user_id:
        await callback_query.answer("❌ These aren't your settings!", show_alert=True)
        return

    current_setting = user_settings.get(user_id, {}).get("as_video", False)
    new_setting     = not current_setting
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id]["as_video"] = new_setting

    mode_text = "🎬 Video (Playable)" if new_setting else "📄 Document (File)"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Toggle Mode: {mode_text}", callback_data=f"toggle_mode:{user_id}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")]
    ])
    await callback_query.edit_message_reply_markup(reply_markup=kb)
    await callback_query.answer(f"✅ Mode switched to {mode_text}!")


@app.on_message(filters.document)
async def handle_torrent_document(client, message: Message):
    if not message.document.file_name.endswith(".torrent"):
        return
    try:
        extract    = "-e" in (message.caption or "").lower()
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        status_msg = await message.reply_text("🔄 **Downloading torrent file...**")

        torrent_file_path = os.path.join(DOWNLOAD_DIR, f"{message.id}_{message.document.file_name}")
        await message.download(file_name=torrent_file_path)
        await safe_edit_message(status_msg, "🔄 **Fetching Peers & Metadata...**")

        download = aria2.add_torrent(torrent_file_path, options=BT_OPTIONS)
        await process_task_execution(message, status_msg, download, extract)
    except Exception as e:
        await message.reply_text(f"❌ **Error processing torrent:** `{str(e)}`")


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
            download = aria2.get_download(found_task.gid)
            aria2.remove([download], force=True, files=True)
            cleanup_files(found_task)
        except Exception as e:
            print(f"Stop error: {e}")

        active_downloads.pop(found_gid, None)
        active_downloads.pop(found_task.gid, None)
        await message.reply_text(f"✅ **Task `{gid_short}` cancelled & files cleaned!**")

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")


# ─────────────────────────────────────────────────────────────────────────────
#  Keep-alive web server
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
