import os
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

import re
import time
import math
import uuid
import asyncio
import logging
import aria2p
import requests
import yt_dlp
from aiohttp import web
from contextlib import suppress
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, MessageNotModified, QueryIdInvalid
import py7zr
import zipfile
import shutil
import psutil
from concurrent.futures import ThreadPoolExecutor
from motor.motor_asyncio import AsyncIOMotorClient

from config import (
    API_ID, API_HASH, BOT_TOKEN, OWNER_ID,
    ARIA2_HOST, ARIA2_PORT, ARIA2_SECRET,
    DOWNLOAD_DIR, MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL, OWNER_PREMIUM,
    PORT, ENGINE_DL, ENGINE_UL, ENGINE_EXTRACT,
    DASHBOARD_REFRESH_INTERVAL, MIN_EDIT_GAP,
    WORKERS, MAX_CONCURRENT_TRANSMISSIONS, UPLOAD_PARALLEL_FILES,
    BT_OPTIONS, DIRECT_OPTIONS, TASKS_PER_PAGE,
    MONGO_URL
)

try:
    import uvloop; uvloop.install(); print("uvloop ok")
except ImportError: pass
try:
    import tgcrypto; print("tgcrypto ok - upload speed boost active")
except ImportError: print("WARNING: tgcrypto missing - uploads will be slow")

app = Client(
    "leech_bot",
    api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
    workers=WORKERS, max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
)
aria2    = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret=ARIA2_SECRET))
executor = ThreadPoolExecutor(max_workers=4)

async def aria2_run(fn, *args, **kwargs):
    """Run a synchronous aria2p call in the thread executor.
    aria2p uses 'requests' under the hood — every aria2.get_download / add_uris /
    add_torrent / remove is a real synchronous HTTP call that would otherwise
    block the entire asyncio event loop for the duration of the RPC round-trip.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))

# MongoDB Setup
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["leech_bot_db"]
settings_col = db["settings"]

active_downloads  = {}
user_settings     = {}
user_dashboards   = {}
user_edit_queues  = {}
_dashboard_locks  = {}   # per-user asyncio.Lock to prevent duplicate dashboard creation

class DownloadTask:
    def __init__(self, gid: str, user_id: int, extract: bool = False):
        self.gid           = gid
        self.user_id       = user_id
        self.extract       = extract
        self.cancelled     = False
        self.start_time    = time.time()
        self.file_path     = None
        self.extract_dir   = None
        self.filename      = ""
        self.file_size     = 0
        self.current_phase = "dl"
        self.dl  = {"filename":"","progress":0.0,"speed":0,"downloaded":0,"total":0,"elapsed":0,"eta":0,"peer_line":""}
        self.ext = {"filename":"","pct":0.0,"speed":0,"extracted":0,"total":0,"elapsed":0,"remaining":0,"cur_file":"","file_index":0,"total_files":0,"archive_size":0}
        self.ul  = {"filename":"","uploaded":0,"total":0,"speed":0,"elapsed":0,"eta":0,"file_index":1,"total_files":1}


# ── Utility helpers ───────────────────────────────────────────────────────────
def clean_filename(filename: str) -> str:
    c = filename
    c = re.sub(r'^\[.*?\]\s*|^\(.*?\)\s*', '', c)
    c = re.sub(r'^@\w+\s*', '', c)
    c = re.sub(r'https?://\S+', '', c)
    c = re.sub(r'^(?:www\.)[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:[._\-\s]+)', '', c, flags=re.IGNORECASE)
    c = re.sub(r'^[a-zA-Z0-9-]+\.(?:gs|pw|me|to|io|cc|tv|ws)(?:[._\-\s]+)', '', c, flags=re.IGNORECASE)
    c = re.sub(r'[._\-][a-zA-Z][a-zA-Z0-9-]*\.(?:pw|gs|me|to|cc|ws|tv)\b', '', c, flags=re.IGNORECASE)
    c = re.sub(r'\b[a-zA-Z0-9-]+\.(?:com|net|org|info|xyz|site|club)\b', '', c, flags=re.IGNORECASE)
    c = re.sub(r'\.{2,}', '.', c)
    c = c.strip(" .-_")
    if not c:
        c = filename
    if len(c) > 120:
        name, ext = os.path.splitext(c)
        sl = 120 - len(ext) - 3
        c = (name[:sl] + "..." + ext) if sl > 0 else c[:120]
    return c

def smart_episode_name(file_path: str, base_dir: str) -> str:
    filename = os.path.basename(file_path)
    name, ext = os.path.splitext(filename)
    if not re.match(r'^S\d+E\d+', name, re.IGNORECASE):
        return clean_filename(filename)
    rel    = os.path.relpath(file_path, base_dir).replace("\\", "/")
    parts  = rel.split("/")
    parent = parts[0] if len(parts) > 1 else ""
    if not parent:
        return clean_filename(filename)
    show_match = re.match(
        r'^(.*?)(?:[._\s](?:S\d{2}|720p|1080p|480p|405p|WEB|AMZN|HDRip|BluRay|DD|AAC|H\.264|x264))',
        parent, re.IGNORECASE
    )
    show_name = (show_match.group(1) if show_match
                 else re.split(r'[._]S\d{2}', parent)[0]).strip("._- ")
    if not show_name:
        return clean_filename(filename)
    season_m  = re.search(r'(S\d{2})', parent, re.IGNORECASE)
    episode_m = re.match(r'S\d+(E\d+)', name, re.IGNORECASE)
    season    = season_m.group(1).upper()  if season_m  else ""
    episode   = episode_m.group(1).upper() if episode_m else name.upper()
    result = f"{show_name}.{season}{episode}{ext}" if season else f"{show_name}.{name}{ext}"
    return result

def create_progress_bar(pct: float) -> str:
    if pct >= 100: return "`[████████████]` **100%**"
    f = int(pct / 100 * 12)
    e = 12 - f
    bar = "█" * f + "░" * e
    return f"`[{bar}]` **{pct:.1f}%**"

def format_speed(s: float) -> str:
    if s >= 1048576: return f"{s/1048576:.2f} MB/s"
    if s >= 1024:    return f"{s/1024:.2f} KB/s"
    return "0 B/s"

def format_size(b: int) -> str:
    gb = b / (1024**3)
    return f"{gb:.2f} GB" if gb >= 1 else f"{b/(1024**2):.2f} MB"

def format_time(s: float) -> str:
    if s <= 0: return "0s"
    # Cap runaway ETAs that aria2 emits when speed is near-zero
    if s > 359999: return "∞"
    h, m, s = int(s//3600), int((s%3600)//60), int(s%60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def get_system_stats() -> dict:
    # BUG FIX: cpu_percent(interval=0.1) sleeps for 100ms synchronously, blocking
    # the entire async event loop on every dashboard render. interval=None returns
    # the value from the last call immediately without any blocking sleep.
    cpu  = psutil.cpu_percent(interval=None)
    ram  = psutil.virtual_memory()
    up   = time.time() - psutil.boot_time()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    disk = psutil.disk_usage(DOWNLOAD_DIR)
    return {
        "cpu":           cpu,
        "ram_percent":   ram.percent,
        "uptime":        format_time(up),
        "disk_free":     disk.free / (1024**3),
        "disk_free_pct": 100.0 - disk.percent,
        "dl_speed":      0,
        "ul_speed":      0,
    }

def bot_stats_block(st: dict, task_count: int = 0) -> str:
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⌬ **Bot Stats** — Active: `{task_count}` task(s)\n"
        f"┠ 🖥 **CPU:** {st['cpu']:.1f}%  💾 **RAM:** {st['ram_percent']:.1f}%\n"
        f"┠ 💿 **Free:** {st['disk_free']:.2f} GB [{st['disk_free_pct']:.1f}%]  🕐 **Up:** {st['uptime']}\n"
        f"┖ ⬇ **{format_speed(st['dl_speed'])}**  ⬆ **{format_speed(st['ul_speed'])}**"
    )

def get_user_label(message: Message) -> str:
    try:
        if message.from_user.username:
            return f"@{message.from_user.username} ( #ID{message.from_user.id} )"
    except Exception: pass
    return f"#ID{message.from_user.id}"

def cleanup_files(task: DownloadTask):
    try:
        if task.file_path and os.path.exists(task.file_path):
            os.remove(task.file_path) if os.path.isfile(task.file_path) else shutil.rmtree(task.file_path, ignore_errors=True)
        if task.extract_dir and os.path.exists(task.extract_dir):
            shutil.rmtree(task.extract_dir, ignore_errors=True)
    except Exception as e:
        print(f"Cleanup error: {e}")


# ── Task block renderer ───────────────────────────────────────────────────────
def build_task_block(task: DownloadTask, index: int) -> str:
    gs = task.gid[:8]
    p  = task.current_phase

    if p == "dl":
        d    = task.dl
        name = d["filename"] or "Connecting..."
        sz   = ("🔍 Fetching Metadata..." if d["total"] == 0
                else f"{format_size(d['downloaded'])} / **{format_size(d['total'])}**")
        peer = d["peer_line"].strip() if d["peer_line"] else ""
        peer_line = f"\n├ 🔗 {peer}" if peer else ""
        return (
            f"**{index}. ⬇️  {name}**\n"
            f"├ {create_progress_bar(d['progress'])}\n"
            f"├ 📦 {sz}\n"
            f"├ ⚡ **{format_speed(d['speed'])}**  •  ⏳ ETA: `{format_time(d['eta'])}`"
            f"{peer_line}\n"
            f"├ 🕐 Elapsed: `{format_time(d['elapsed'])}`\n"
            f"├ 🔧 `{ENGINE_DL}` → `#Leech`\n"
            f"└ ⛔ /stop_{gs}"
        )

    if p == "ext":
        e   = task.ext
        pct = e["pct"]
        if e["total"] > 0:
            sz = f"{format_size(e['extracted'])} / **{format_size(e['total'])}**"
        elif e["archive_size"] > 0:
            sz = f"Archive: **{format_size(e['archive_size'])}**"
        else:
            sz = "Preparing..."
        ft  = f"`{e['file_index']} / {e['total_files']} files`" if e["total_files"] > 0 else "`Scanning...`"
        cur = e["cur_file"]
        if cur and len(cur) > 45: cur = cur[:42] + "..."
        fl  = f"`{cur}`" if cur else "`preparing...`"
        sp  = f"**{format_speed(e['speed'])}**" if e["speed"] > 0 else "`Calculating...`"
        return (
            f"**{index}. 📦  {e['filename'] or 'Extracting...'}**\n"
            f"├ {create_progress_bar(pct)}\n"
            f"├ 🗜 {sz}\n"
            f"├ 📄 {ft}  •  ⚡ {sp}\n"
            f"├ 📂 Current: {fl}\n"
            f"├ 🕐 Elapsed: `{format_time(e['elapsed'])}`  •  ⏳ `{format_time(e['remaining'])}`\n"
            f"├ 🔧 `{ENGINE_EXTRACT}` → `#Extract` → `#Leech`\n"
            f"└ ⛔ /stop_{gs}"
        )

    if p == "ul":
        u    = task.ul
        pc   = min((u["uploaded"] / u["total"]) * 100, 100) if u["total"] > 0 else 0
        fname = clean_filename(u["filename"] or "Uploading...")
        sz   = f"{format_size(u['uploaded'])} / **{format_size(u['total'])}**"
        fc_line = f"\n├ 📄 Files: `{u['file_index']} / {u['total_files']}`" if u["total_files"] > 1 else ""
        return (
            f"**{index}. ⬆️  {fname}**\n"
            f"├ {create_progress_bar(pc)}\n"
            f"├ 📤 {sz}\n"
            f"├ ⚡ **{format_speed(u['speed'])}**  •  ⏳ ETA: `{format_time(u['eta'])}`"
            f"{fc_line}\n"
            f"├ 🕐 Elapsed: `{format_time(u['elapsed'])}`\n"
            f"├ 🔧 `{ENGINE_UL}` → `#Leech`\n"
            f"└ ⛔ /stop_{gs}"
        )

    return f"**{index}. ⏳ Task** — Processing..."


# ── Paginated dashboard builder ───────────────────────────────────────────────
def get_total_pages(user_id: int) -> int:
    tasks = [t for t in active_downloads.values() if t.user_id == user_id]
    if not tasks: return 1
    return max(1, (len(tasks) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)


def build_dashboard_text(user_id: int, user_label: str, page: int = 0) -> str:
    tasks = [t for t in active_downloads.values() if t.user_id == user_id]
    if not tasks:
        return (
            "🔰 **LEECH DASHBOARD**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ **All tasks completed!**"
        )

    total_pages = max(1, (len(tasks) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))

    start      = page * TASKS_PER_PAGE
    page_tasks = tasks[start: start + TASKS_PER_PAGE]

    stats  = get_system_stats()
    # Realtime speed: always sum from ALL active tasks regardless of page
    stats["dl_speed"] = sum(t.dl["speed"] for t in tasks if t.current_phase == "dl")
    stats["ul_speed"] = sum(t.ul["speed"] for t in tasks if t.current_phase == "ul")

    dl_c = sum(1 for t in tasks if t.current_phase == "dl")
    ex_c = sum(1 for t in tasks if t.current_phase == "ext")
    ul_c = sum(1 for t in tasks if t.current_phase == "ul")
    badges = []
    if dl_c: badges.append(f"⬇ `{dl_c}`")
    if ex_c: badges.append(f"📦 `{ex_c}`")
    if ul_c: badges.append(f"⬆ `{ul_c}`")
    badge_str = "  ".join(badges)

    page_label = f"  •  📄 `{page + 1}/{total_pages}`" if total_pages > 1 else ""
    header = (
        f"🔰 **LEECH DASHBOARD**\n"
        f"👤 {user_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{badge_str}{page_label}\n"
    )

    div    = "\n\n"
    blocks = [build_task_block(t, start + i) for i, t in enumerate(page_tasks, 1)]
    body   = div.join(blocks)

    return (f"{header}\n"
            f"{body}\n\n"
            f"{bot_stats_block(stats, len(tasks))}")


def dashboard_keyboard(user_id: int, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    buttons = []
    if total_pages > 1:
        nav = []
        nav.append(InlineKeyboardButton(
            "◀️ Prev" if page > 0 else "◀️",
            callback_data=f"dpage:{user_id}:{max(0, page - 1)}"
        ))
        nav.append(InlineKeyboardButton(
            f"📄 {page + 1} / {total_pages}",
            callback_data="noop"
        ))
        nav.append(InlineKeyboardButton(
            "Next ▶️" if page < total_pages - 1 else "▶️",
            callback_data=f"dpage:{user_id}:{min(total_pages - 1, page + 1)}"
        ))
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔄 Refresh Now", callback_data=f"dash:{user_id}")])
    return InlineKeyboardMarkup(buttons)


# ── FloodWait-safe edit queue ─────────────────────────────────────────────────
async def edit_worker(user_id: int):
    q = user_edit_queues[user_id]
    while True:
        item = await q.get()
        if item is None:
            q.task_done(); break
        text, kb = item
        dash = user_dashboards.get(user_id)
        if not dash:
            q.task_done(); break
        if text == dash.get("last_text", ""):
            q.task_done(); await asyncio.sleep(1); continue
        try:
            await dash["msg"].edit_text(text, reply_markup=kb)
            dash["last_text"]    = text
            dash["last_edit_at"] = time.time()
        except FloodWait as e:
            ws = e.value + 3
            dash["flood_until"] = time.time() + ws
            print(f"⚠️ FloodWait {e.value}s user {user_id} — edit worker sleeping {ws}s")
            await asyncio.sleep(ws)
        except MessageNotModified:
            dash["last_text"] = text
        except Exception as e:
            print(f"Edit worker error user {user_id}: {e}")
        q.task_done()
        await asyncio.sleep(MIN_EDIT_GAP)


async def _enqueue_edit(user_id: int):
    dash = user_dashboards.get(user_id)
    if not dash: return
    if time.time() < dash.get("flood_until", 0): return

    page        = dash.get("page", 0)
    total_pages = get_total_pages(user_id)
    if page >= total_pages:
        page = max(0, total_pages - 1)
        dash["page"] = page

    text = build_dashboard_text(user_id, dash.get("user_label", f"#ID{user_id}"), page)
    kb   = dashboard_keyboard(user_id, page, total_pages)
    if text == dash.get("last_text", ""): return

    if user_id not in user_edit_queues:
        user_edit_queues[user_id] = asyncio.Queue(maxsize=2)
        asyncio.create_task(edit_worker(user_id))
    q = user_edit_queues[user_id]
    while not q.empty():
        try: q.get_nowait(); q.task_done()
        except Exception: break
    try:
        q.put_nowait((text, kb))
    except asyncio.QueueFull:
        pass

async def push_dashboard_update(user_id: int):
    await _enqueue_edit(user_id)

async def dashboard_loop(user_id: int):
    while True:
        await asyncio.sleep(DASHBOARD_REFRESH_INTERVAL)
        dash = user_dashboards.get(user_id)
        if not dash: break
        user_tasks = [t for t in active_downloads.values() if t.user_id == user_id]
        if not user_tasks:
            q = user_edit_queues.pop(user_id, None)
            if q:
                try: q.put_nowait(None)
                except Exception: pass
            try:
                await dash["msg"].edit_text(
                    "🔰 **LEECH DASHBOARD**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ **All tasks completed!**",
                    reply_markup=None
                )
            except Exception: pass
            user_dashboards.pop(user_id, None)
            _dashboard_locks.pop(user_id, None)
            break
        if time.time() < dash.get("flood_until", 0):
            left = int(dash["flood_until"] - time.time())
            print(f"⏳ FloodWait active user {user_id} — {left}s left, skipping tick")
            continue
        now = time.time()
        if now - dash.get("last_edit_at", 0) < MIN_EDIT_GAP: continue
        await _enqueue_edit(user_id)

async def get_or_create_dashboard(user_id: int, trigger_msg: Message, user_label: str, force_new: bool = False) -> Message:
    # BUG FIX: When multiple torrents/links arrive simultaneously, concurrent calls
    # all see user_dashboards.get(user_id) == None before any one of them sets it,
    # so each creates its own "Initialising..." message. A per-user asyncio.Lock
    # ensures only the first caller does the send; the rest reuse the same message.
    if user_id not in _dashboard_locks:
        _dashboard_locks[user_id] = asyncio.Lock()
    async with _dashboard_locks[user_id]:
        dash = user_dashboards.get(user_id)

        # force_new=True: called on /l — delete the old dashboard and start fresh
        if dash and force_new:
            try:
                await dash["msg"].delete()
            except Exception:
                pass
            # Gracefully stop the old edit worker
            old_q = user_edit_queues.pop(user_id, None)
            if old_q:
                try: old_q.put_nowait(None)
                except Exception: pass
            user_dashboards.pop(user_id, None)
            _dashboard_locks.pop(user_id, None)
            _dashboard_locks[user_id] = asyncio.Lock()
            dash = None

        if dash:
            dash["user_label"] = user_label
            return dash["msg"]

        msg = await trigger_msg.reply_text(
            "⏳ **Initialising dashboard...**",
            reply_markup=dashboard_keyboard(user_id, 0, 1),
        )
        user_dashboards[user_id] = {
            "msg": msg, "flood_until": 0.0, "user_label": user_label,
            "last_text": "", "last_edit_at": 0.0,
            "page": 0,
        }
        asyncio.create_task(dashboard_loop(user_id))
        return msg

async def safe_answer(cq, text: str = "", show_alert: bool = False):
    try:
        await cq.answer(text, show_alert=show_alert)
    except (QueryIdInvalid, Exception):
        pass


# ── Callback: Refresh ─────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^dash:"))
async def dashboard_refresh_callback(client, cq: CallbackQuery):
    _, uid = cq.data.split(":", 1)
    user_id = int(uid)
    dash = user_dashboards.get(user_id)
    if not dash:
        await safe_answer(cq, "⚠️ No active tasks.", show_alert=True); return
    now = time.time()
    if now < dash.get("flood_until", 0):
        left = int(dash["flood_until"] - now)
        await safe_answer(cq, f"⏳ Rate limit active — auto-refresh resumes in {left}s", show_alert=True); return
    if now - dash.get("last_edit_at", 0) < MIN_EDIT_GAP:
        gap = int(MIN_EDIT_GAP - (now - dash.get("last_edit_at", 0)))
        await safe_answer(cq, f"⏳ Please wait {gap}s between refreshes.", show_alert=True); return

    page        = dash.get("page", 0)
    total_pages = get_total_pages(user_id)
    text = build_dashboard_text(user_id, dash.get("user_label", f"#ID{user_id}"), page)
    kb   = dashboard_keyboard(user_id, page, total_pages)
    try:
        await cq.edit_message_text(text, reply_markup=kb)
        dash["last_text"] = text; dash["last_edit_at"] = time.time()
        await safe_answer(cq, "✅ Refreshed!")
    except FloodWait as e:
        ws = e.value + 3; dash["flood_until"] = time.time() + ws
        await safe_answer(cq, f"⚠️ Rate limit ({e.value}s). Auto-refresh will continue.", show_alert=True)
    except MessageNotModified:
        await safe_answer(cq, "ℹ️ Already up to date.")
    except Exception as e:
        await safe_answer(cq, f"❌ {e}", show_alert=True)


# ── Callback: Page navigation ─────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^dpage:"))
async def dashboard_page_callback(client, cq: CallbackQuery):
    parts    = cq.data.split(":")
    user_id  = int(parts[1])
    new_page = int(parts[2])
    dash = user_dashboards.get(user_id)
    if not dash:
        await safe_answer(cq, "⚠️ No active tasks.", show_alert=True); return

    total_pages = get_total_pages(user_id)
    new_page    = max(0, min(new_page, total_pages - 1))

    if new_page == dash.get("page", 0):
        await safe_answer(cq); return

    dash["page"] = new_page
    text = build_dashboard_text(user_id, dash.get("user_label", f"#ID{user_id}"), new_page)
    kb   = dashboard_keyboard(user_id, new_page, total_pages)
    try:
        await cq.edit_message_text(text, reply_markup=kb)
        dash["last_text"] = text; dash["last_edit_at"] = time.time()
        await safe_answer(cq)
    except FloodWait as e:
        ws = e.value + 3; dash["flood_until"] = time.time() + ws
        await safe_answer(cq)
    except MessageNotModified:
        await safe_answer(cq)
    except Exception as e:
        await safe_answer(cq, f"❌ {e}", show_alert=True)


# ── Callback: no-op ───────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^noop$"))
async def noop_callback(client, cq: CallbackQuery):
    await safe_answer(cq)


# ── Download progress poller ──────────────────────────────────────────────────
async def poll_download_progress(task: DownloadTask):
    await asyncio.sleep(2)
    while not task.cancelled:
        try:
            dl = await aria2_run(aria2.get_download, task.gid)
            if dl.is_complete: break
            _eta = dl.eta
            raw_eta = _eta.total_seconds() if _eta and _eta.total_seconds() > 0 else 0
            # aria2 emits huge ETA values (billions of seconds) when speed is ~0.
            # Clamp to 100 hours max; format_time will render anything above that as ∞.
            clamped_eta = min(raw_eta, 360000)
            task.dl.update({
                "filename":   clean_filename(dl.name if dl.name else "Connecting..."),
                "progress":   dl.progress or 0.0,
                "speed":      dl.download_speed or 0,
                "downloaded": dl.completed_length or 0,
                "total":      dl.total_length or 0,
                "elapsed":    time.time() - task.start_time,
                "eta":        clamped_eta,
            })
            task.filename  = task.dl["filename"]
            task.file_size = task.dl["total"]
            try:
                s = getattr(dl, "num_seeders", None)
                if s and int(s) > 0:
                    task.dl["peer_line"] = f"├ **Seeders** → {s} | **Leechers** → {dl.connections or 0}\n"
                else:
                    task.dl["peer_line"] = f"├ **Connections** → {dl.connections or 0}\n"
            except Exception:
                task.dl["peer_line"] = ""
        except Exception:
            pass
        await asyncio.sleep(3)


# ── Extract archive ───────────────────────────────────────────────────────────
async def extract_archive(file_path: str, extract_to: str, task: DownloadTask = None) -> bool:
    try:
        filename   = clean_filename(os.path.basename(file_path))
        total_size = os.path.getsize(file_path)
        start_time = time.time()
        last_push  = [0.0]

        if task:
            task.current_phase = "ext"
            task.ext.update({"filename": filename, "archive_size": total_size, "total": total_size})

        async def _update(done, total, cur_file, fi, fn):
            elapsed   = time.time() - start_time
            speed     = done / elapsed if elapsed > 0 else 0
            remaining = (total - done) / speed if speed > 0 else 0
            pct       = min(done / total * 100, 100) if total > 0 else 0
            if task:
                rel_parts = str(cur_file).replace("\\", "/").strip("/").split("/")
                display   = "/".join(rel_parts[-2:]) if len(rel_parts) >= 2 else rel_parts[-1]
                task.ext.update({
                    "pct": pct, "speed": speed, "extracted": done, "total": total,
                    "elapsed": elapsed, "remaining": remaining,
                    "cur_file": display,
                    "file_index": fi, "total_files": fn,
                })
                now = time.time()
                if now - last_push[0] >= MIN_EDIT_GAP:
                    last_push[0] = now
                    await push_dashboard_update(task.user_id)

        if file_path.endswith(".zip"):
            loop = asyncio.get_running_loop()
            def do_zip():
                with zipfile.ZipFile(file_path, "r") as zf:
                    ms = zf.infolist(); n = len(ms); ut = sum(m.file_size for m in ms); done = 0
                    for i, m in enumerate(ms, 1):
                        zf.extract(m, extract_to); done += m.file_size
                        if i % 5 == 0 or i == n:
                            asyncio.run_coroutine_threadsafe(_update(done, ut, m.filename, i, n), loop)
                return True
            return await loop.run_in_executor(executor, do_zip)

        elif file_path.endswith(".7z"):
            # BUG FIX: py7zr extractall() is CPU-bound and was running directly on
            # the async event loop, blocking all other coroutines during extraction.
            # Run it in the executor just like the .zip and .tar branches.
            loop = asyncio.get_running_loop()
            extracted_stats = {"done": 0, "total_files": 0, "uncompressed": 0}

            def do_7z():
                with py7zr.SevenZipFile(file_path, mode="r") as arc:
                    ms = arc.list()
                    n  = len(ms)
                    tu = sum(getattr(m, "uncompressed", 0) or 0 for m in ms)
                    extracted_stats["total_files"]  = n
                    extracted_stats["uncompressed"] = tu
                    done_ref = [0]; tick_ref = [0]

                    class _CB(py7zr.callbacks.ExtractCallback):
                        def __init__(s): s.fi = 0; s.cur_path = ""
                        def report_start_preparation(s): pass
                        def report_start(s, p, b): s.fi += 1; s.cur_path = str(p)
                        def report_update(s, b): pass
                        def report_end(s, p, wrote):
                            done_ref[0] += wrote; tick_ref[0] += 1
                            if tick_ref[0] % 5 == 0:
                                asyncio.run_coroutine_threadsafe(
                                    _update(done_ref[0], tu or total_size, s.cur_path, s.fi, n),
                                    loop,
                                )
                        def report_postprocess(s): pass
                        def report_warning(s, m): pass

                    try:
                        arc.extractall(path=extract_to, callback=_CB())
                    except TypeError:
                        arc.extractall(path=extract_to)
                    extracted_stats["done"] = done_ref[0]
                return True

            result = await loop.run_in_executor(executor, do_7z)
            tu_final = extracted_stats["uncompressed"] or total_size
            await _update(tu_final, tu_final, filename,
                          extracted_stats["total_files"], extracted_stats["total_files"])
            return result

        elif file_path.endswith((".tar.gz", ".tgz", ".tar")):
            import tarfile
            loop = asyncio.get_running_loop()
            def do_tar():
                with tarfile.open(file_path, "r:*") as tf:
                    ms = tf.getmembers(); n = len(ms); tu = sum(m.size for m in ms); done = 0
                    for i, m in enumerate(ms, 1):
                        # BUG FIX: tf.extract(member, path) is deprecated since Python 3.12
                        # and has a path-traversal vulnerability. Use extractall with a
                        # single-member list and the 'data' filter (safe, strips absolute paths).
                        try:
                            tf.extractall(path=extract_to, members=[m], filter="data")
                        except TypeError:
                            # Python < 3.12 does not support the filter= parameter
                            tf.extract(m, extract_to)  # noqa: S202
                        done += m.size
                        if i % 5 == 0 or i == n:
                            asyncio.run_coroutine_threadsafe(_update(done, tu, m.name, i, n), loop)
                return True
            return await loop.run_in_executor(executor, do_tar)

        return False
    except Exception as e:
        print(f"Extraction error: {e}")
        return False


# ── Upload to Telegram ────────────────────────────────────────────────────────
async def upload_to_telegram(file_path: str, message: Message, caption: str = "", task: DownloadTask = None) -> bool:
    if task:
        task.current_phase = "ul"
    user_id    = message.from_user.id
    as_video   = user_settings.get(user_id, {}).get("as_video", False)
    video_exts = (".mp4", ".mkv", ".avi", ".webm")

    # Fetch dump channel once per upload call, not once per file in a directory
    global_settings = await settings_col.find_one({"_id": "global_dump"})
    dump_channel = None
    if global_settings and global_settings.get("enabled"):
        dump_channel = global_settings.get("channel_id")

    try:
        if os.path.isfile(file_path):
            fs = os.path.getsize(file_path)
            if fs > MAX_UPLOAD_BYTES:
                await message.reply_text(f"❌ File too large (>{MAX_UPLOAD_LABEL})")
                return False

            if task and task.cancelled:
                return False

            raw = os.path.basename(file_path)
            cn  = clean_filename(raw)
            if raw != cn:
                np = os.path.join(os.path.dirname(file_path), cn)
                os.rename(file_path, np); file_path = np

            st = time.time(); lr = [0.0]; lu = [0]; lt = [st]
            if task:
                task.ul.update({"filename":cn,"uploaded":0,"total":fs,"speed":0,"elapsed":0,"eta":0,"file_index":1,"total_files":1})
                await push_dashboard_update(user_id)

            async def _progress(current, total):
                now = time.time()
                if now - lr[0] < MIN_EDIT_GAP: return
                dt = now - lt[0]
                if dt <= 0: return
                speed = (current - lu[0]) / dt
                eta = (total - current) / speed if speed > 0 else 0
                lt[0] = now; lu[0] = current; lr[0] = now
                if task:
                    task.ul.update({"uploaded":current,"total":total,"speed":speed,"elapsed":now-st,"eta":eta})
                    await push_dashboard_update(user_id)

            fc = caption or cn

            sent_msg = None
            if as_video and file_path.lower().endswith(video_exts):
                sent_msg = await message.reply_video(video=file_path, caption=fc, progress=_progress, supports_streaming=True, disable_notification=True)
            else:
                sent_msg = await message.reply_document(document=file_path, caption=fc, progress=_progress, disable_notification=True)

            if sent_msg and dump_channel:
                try:
                    await sent_msg.copy(dump_channel)
                except Exception as e:
                    print(f"Failed to copy to dump channel {dump_channel}: {e}")

            try: os.remove(file_path)
            except Exception as e: print(f"Cleanup error (single): {e}")

            return True

        elif os.path.isdir(file_path):
            files = sorted([
                os.path.join(r, f) for r, _, fs2 in os.walk(file_path) for f in fs2
                if os.path.getsize(os.path.join(r, f)) <= MAX_UPLOAD_BYTES
            ])
            if not files:
                await message.reply_text("❌ No uploadable files found."); return False

            n              = len(files)
            total_bytes    = sum(os.path.getsize(fp) for fp in files)
            dir_start      = time.time()

            # Shared state for parallel uploads
            _ul_lock        = asyncio.Lock()
            _uploaded_bytes = [0]

            # Limit simultaneous Telegram upload streams to avoid flood-wait
            sem = asyncio.Semaphore(UPLOAD_PARALLEL_FILES)

            async def _upload_one(idx: int, fp: str):
                async with sem:
                    if task and task.cancelled:
                        return

                    smart  = smart_episode_name(fp, file_path)
                    new_fp = os.path.join(os.path.dirname(fp), smart)
                    if fp != new_fp and not os.path.exists(new_fp):
                        os.rename(fp, new_fp); fp = new_fp

                    file_sz = os.path.getsize(fp)
                    cn      = os.path.basename(fp)
                    cap     = f"📄 {cn} [{idx}/{n}]"

                    # Per-file progress feeding into shared dir-level dashboard
                    _f_lr = [0.0]
                    async def _f_progress(current, total, _cn=cn, _idx=idx, _file_sz=file_sz):
                        now = time.time()
                        if now - _f_lr[0] < MIN_EDIT_GAP: return
                        _f_lr[0] = now
                        if task:
                            async with _ul_lock:
                                elapsed = now - dir_start
                                approx  = _uploaded_bytes[0] + current
                                spd     = approx / elapsed if elapsed > 0 else 0
                                eta     = (total_bytes - approx) / spd if spd > 0 else 0
                                task.ul.update({
                                    "filename": _cn, "uploaded": approx,
                                    "total": total_bytes, "speed": spd,
                                    "elapsed": elapsed, "eta": eta,
                                    "file_index": _idx, "total_files": n,
                                })
                            await push_dashboard_update(user_id)

                    sent_msg = None
                    if as_video and fp.lower().endswith(video_exts):
                        sent_msg = await message.reply_video(video=fp, caption=cap, progress=_f_progress, disable_notification=True)
                    else:
                        sent_msg = await message.reply_document(document=fp, caption=cap, progress=_f_progress, disable_notification=True)

                    if sent_msg and dump_channel:
                        try:
                            await sent_msg.copy(dump_channel)
                        except Exception as e:
                            print(f"Failed to copy file {idx} to dump channel: {e}")

                    try: os.remove(fp)
                    except Exception as e: print(f"Cleanup error (file {idx}/{n}): {e}")

                    async with _ul_lock:
                        _uploaded_bytes[0] += file_sz

            await asyncio.gather(*[_upload_one(idx, fp) for idx, fp in enumerate(files, 1)])

            try: shutil.rmtree(file_path, ignore_errors=True)
            except Exception as e: print(f"Cleanup error (dir): {e}")

            return True

    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}")
        return False
    # BUG FIX: if file_path is neither a file nor a directory (e.g. deleted between
    # download and upload, or aria2 wrote to an unexpected location), the try block
    # falls through without returning, giving the caller an implicit None (falsy but
    # misleading). Return False explicitly so callers can detect the failure cleanly.
    return False


# ── Core task processor ───────────────────────────────────────────────────────
async def process_task_execution(message: Message, task: DownloadTask, download, extract: bool):
    gid = download.gid; task.gid = gid
    active_downloads[gid] = task
    try:
        asyncio.create_task(poll_download_progress(task))
        await push_dashboard_update(task.user_id)

        while not task.cancelled:
            await asyncio.sleep(2)
            try: cdl = await aria2_run(aria2.get_download, task.gid)
            except Exception: break
            fb = getattr(cdl, "followed_by", None)
            if fb:
                new_gid = fb[0].gid if hasattr(fb[0], "gid") else fb[0]
                old_gid = task.gid; task.gid = new_gid
                active_downloads[new_gid] = task; active_downloads.pop(old_gid, None)
                continue
            if cdl.is_complete: break
            elif getattr(cdl, "has_failed", False):
                active_downloads.pop(task.gid, None)
                task.cancelled = True   # BUG FIX: without this, poll_download_progress
                                        # keeps looping and making dead RPC calls forever
                await message.reply_text(f"❌ **Aria2 Error:** `{cdl.error_message}`")
                cleanup_files(task); await push_dashboard_update(task.user_id); return

        if task.cancelled:
            try:
                _dl_to_remove = await aria2_run(aria2.get_download, task.gid)
                await aria2_run(aria2.remove, [_dl_to_remove], force=True, files=True)
            except Exception: pass
            cleanup_files(task); active_downloads.pop(task.gid, None)
            await push_dashboard_update(task.user_id); return

        try:
            fdl = await aria2_run(aria2.get_download, task.gid)
            fp  = os.path.join(DOWNLOAD_DIR, fdl.name)
            # For multi-file torrents aria2 creates a directory — use it directly.
            # BUG FIX: task.dl["filename"] is the cleaned display name and may not
            # match the real file on disk. Use aria2's own fdl.name as source of
            # truth; if that path is also missing, scan DOWNLOAD_DIR for the most
            # recently modified entry as a last resort.
            if not os.path.exists(fp):
                try:
                    entries = [
                        os.path.join(DOWNLOAD_DIR, e)
                        for e in os.listdir(DOWNLOAD_DIR)
                    ]
                    if entries:
                        fp = max(entries, key=os.path.getmtime)
                except Exception:
                    pass  # keep fp as is; upload step will report the error
        except Exception:
            fp = os.path.join(DOWNLOAD_DIR, task.dl["filename"])
        task.file_path = fp

        # Extract only makes sense on a single archive file, not a directory
        if extract and os.path.isfile(fp) and fp.endswith((".zip", ".7z", ".tar.gz", ".tgz", ".tar")):
            if task.cancelled:
                cleanup_files(task); active_downloads.pop(task.gid, None)
                await push_dashboard_update(task.user_id); return
            ed = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
            os.makedirs(ed, exist_ok=True); task.extract_dir = ed
            us, cap = (ed, "📁 Extracted files") if await extract_archive(fp, ed, task=task) else (fp, "")
        else:
            us, cap = fp, ""

        if task.cancelled:
            cleanup_files(task); active_downloads.pop(task.gid, None)
            await push_dashboard_update(task.user_id); return

        await upload_to_telegram(us, message, caption=cap, task=task)
        cleanup_files(task); active_downloads.pop(task.gid, None)
        await push_dashboard_update(task.user_id)

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")
        cleanup_files(task); active_downloads.pop(task.gid, None)
        await push_dashboard_update(task.user_id)


# ════════════════════════════════════════════════════════
#  BOT COMMANDS
# ════════════════════════════════════════════════════════

@app.on_message(filters.command(["setdump"]))
async def set_dump_channel(client, message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ **Access Denied:** Only the bot owner can configure the dump channel.")

    args = message.text.split()
    if len(args) < 3:
        return await message.reply_text("❌ **Usage:** `/setdump <channel_id> -on` or `/setdump <channel_id> -off`\n*(Make sure the bot is an admin in the channel)*")

    try:
        channel_id = int(args[1])
        flag = args[2].lower()
        if flag not in ["-on", "-off"]:
            return await message.reply_text("❌ **Invalid flag.** Use `-on` to enable or `-off` to disable.")

        is_enabled = (flag == "-on")
        await settings_col.update_one(
            {"_id": "global_dump"},
            {"$set": {"channel_id": channel_id, "enabled": is_enabled}},
            upsert=True
        )
        state_msg = "🟢 **ENABLED**" if is_enabled else "🔴 **DISABLED**"
        await message.reply_text(
            f"✅ **Dump Channel Updated!**\n\n"
            f"**Channel:** `{channel_id}`\n"
            f"**Status:** {state_msg}"
        )
    except ValueError:
        await message.reply_text("❌ **Invalid channel ID.** It must be an integer (e.g., `-100123456789`).")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")


@app.on_message(filters.command(["leech", "l", "ql"]))
async def universal_leech_command(client, message: Message):
    extract    = "-e" in message.text.lower()
    user_id    = message.from_user.id
    user_label = get_user_label(message)
    cmd        = message.command[0].lower()   # "l", "leech", or "ql"
    # /l and /leech → fresh dashboard per new task (delete old, start clean)
    # /ql            → append to existing dashboard (batch mode)
    force_new  = cmd in ("l", "leech")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await get_or_create_dashboard(user_id, message, user_label, force_new=force_new)

    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        if doc.file_name.endswith(".torrent"):
            tp = os.path.join(DOWNLOAD_DIR, f"{message.id}_{doc.file_name}")
            await message.reply_to_message.download(file_name=tp)
            dl = await aria2_run(aria2.add_torrent, tp, options=BT_OPTIONS)
            task = DownloadTask(dl.gid, user_id, extract)
            asyncio.create_task(process_task_execution(message, task, dl, extract)); return

    args  = message.text.split()[1:]
    links = [a for a in args if a.startswith("http") or a.startswith("magnet:")]
    if not links:
        await message.reply_text("❌ **Usage:** `/ql <link1> <link2>` or reply to a `.torrent` file.\n❌ `/l <link>` for direct links"); return

    for link in links:
        try:
            opts = BT_OPTIONS if link.startswith("magnet:") else {**BT_OPTIONS, **DIRECT_OPTIONS}
            dl   = await aria2_run(aria2.add_uris, [link], options=opts)
            task = DownloadTask(dl.gid, user_id, extract)
            asyncio.create_task(process_task_execution(message, task, dl, extract))
        except Exception as e:
            await message.reply_text(f"❌ **Failed to add:** `{str(e)}`")


@app.on_message(filters.document)
async def handle_document_upload(client, message: Message):
    file_name = message.document.file_name or ""

    if file_name.endswith(".torrent"):
        try:
            user_id = message.from_user.id; user_label = get_user_label(message)
            extract = "-e" in (message.caption or "").lower()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await get_or_create_dashboard(user_id, message, user_label)
            tp = os.path.join(DOWNLOAD_DIR, f"{message.id}_{file_name}")
            await message.download(file_name=tp)
            dl = await aria2_run(aria2.add_torrent, tp, options=BT_OPTIONS)
            task = DownloadTask(dl.gid, user_id, extract)
            asyncio.create_task(process_task_execution(message, task, dl, extract))
        except Exception as e:
            await message.reply_text(f"❌ **Error processing torrent:** `{str(e)}`")


@app.on_message(filters.command(["stop"]) | filters.regex(r"^/stop_\w+"))
async def stop_command(client, message: Message):
    try:
        text = message.text or ""
        gid_short = (text.split("_", 1)[1].strip() if text.startswith("/stop_")
                     else (text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else None))
        if not gid_short:
            await message.reply_text("❌ **Usage:** `/stop <task_id>`"); return
        found_task = found_gid = None
        for gid, t in list(active_downloads.items()):
            if gid.startswith(gid_short) or gid[:8] == gid_short:
                found_task = t; found_gid = gid; break
        if not found_task:
            await message.reply_text(f"❌ **Task `{gid_short}` not found!**"); return
        found_task.cancelled = True
        try:
            _dl_stop = await aria2_run(aria2.get_download, found_task.gid)
            await aria2_run(aria2.remove, [_dl_stop], force=True, files=True)
            cleanup_files(found_task)
        except Exception as e: print(f"Stop error: {e}")
        active_downloads.pop(found_gid, None); active_downloads.pop(found_task.gid, None)
        await message.reply_text(f"✅ **Task `{gid_short}` cancelled & files cleaned!**")
        await push_dashboard_update(found_task.user_id)
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")


@app.on_message(filters.command(["start"]))
async def start_command(client, message: Message):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Upload Settings", callback_data=f"toggle_mode:{message.from_user.id}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")]
    ])
    await message.reply_text(
        "**🤖 Welcome to the Advanced Leech Bot!**\n\n"
        "Download direct links, magnets, `.torrent` files and YouTube videos to Telegram.\n\n"
        "Type /help for all commands.\n\n© Maintained By @im_goutham_josh", reply_markup=kb)


@app.on_message(filters.command(["help"]))
async def help_command(client, message: Message):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Close", callback_data="close_help")]])
    await message.reply_text(
        "**📖 Leech Bot — Help & Commands**\n\n"
        "**📥 Download Commands:**\n"
        "• `/yl <url>` — Download YouTube/Instagram/TikTok & more\n"
        "• `/ql <link1> <link2>` — Download multiple direct/magnet links at once\n"
        "• `/l <link>` — Single direct link download\n"
        "• `/l <link> -e` — Download & auto-extract archive\n"
        "• **Upload a `.torrent` file** directly to start\n\n"
        "**⚙️ Control:**\n"
        "• `/settings` — Toggle Document / Video upload mode\n"
        "• `/stop <task_id>` — Cancel an active task\n"
        "• `/setdump <channel_id> -on/-off` — Manage global dump channel (Owner Only)\n\n"
        "**✨ Features:**\n"
        "✓ New paginated dashboard — 4 tasks per page with ◀ Prev / Next ▶\n"
        "✓ ONE dashboard per user, auto-refreshes & shows realtime speed\n"
        "✓ FloodWait eliminated via serialised edit queue\n"
        "✓ 38 supercharged trackers + 500 max peers\n"
        "✓ Smart filename cleaning\n"
        "✓ /l resets dashboard on each new task", reply_markup=kb)


@app.on_callback_query(filters.regex(r"^close_help$"))
async def close_help_callback(client, cq: CallbackQuery):
    try: await cq.message.delete()
    except Exception: pass


@app.on_message(filters.command(["settings"]))
async def settings_command(client, message: Message):
    uid = message.from_user.id
    av  = user_settings.get(uid, {}).get("as_video", False)
    mt  = "🎬 Video (Playable)" if av else "📄 Document (File)"
    kb_buttons = [
        [InlineKeyboardButton(f"Toggle: {mt}", callback_data=f"toggle_mode:{uid}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")],
    ]
    await message.reply_text(
        "⚙️ **Upload Settings**\n\nChoose how video files (.mp4, .mkv, .webm) are sent.",
        reply_markup=InlineKeyboardMarkup(kb_buttons)
    )


@app.on_callback_query(filters.regex(r"^toggle_mode:"))
async def toggle_mode_callback(client, cq: CallbackQuery):
    _, uid_str = cq.data.split(":"); uid = int(uid_str)
    if cq.from_user.id != uid:
        await safe_answer(cq, "❌ These aren't your settings!", show_alert=True); return
    cur = user_settings.get(uid, {}).get("as_video", False)
    new_val = not cur
    user_settings.setdefault(uid, {})["as_video"] = new_val
    # Persist to MongoDB so it survives restarts
    await settings_col.update_one(
        {"_id": f"user_settings_{uid}"},
        {"$set": {"as_video": new_val}},
        upsert=True
    )
    mt = "🎬 Video (Playable)" if new_val else "📄 Document (File)"
    kb_buttons = [
        [InlineKeyboardButton(f"Toggle: {mt}", callback_data=f"toggle_mode:{uid}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")],
    ]
    await cq.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb_buttons))
    await safe_answer(cq, f"✅ Switched to {mt}!")


# ── Keep-alive web server ─────────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(text=(
        "✅ Leech Bot is alive\n"
        f"Active downloads : {len(active_downloads)}\n"
        f"Active dashboards: {len(user_dashboards)}\n"
        f"Tasks per page   : {TASKS_PER_PAGE}\n"
        f"Upload limit     : {MAX_UPLOAD_LABEL} ({'Premium' if OWNER_PREMIUM else 'Standard'})\n"
        f"Edit interval    : {MIN_EDIT_GAP}s min gap / {DASHBOARD_REFRESH_INTERVAL}s auto-refresh"
    ), content_type="text/plain")


async def start_web_server():
    wa = web.Application()
    wa.router.add_get("/", health_handler); wa.router.add_get("/health", health_handler)
    runner = web.AppRunner(wa); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"🌐 Keep-alive server on port {PORT}")


async def main():
    print("🚀 Starting Leech Bot...")
    print(f"📦 Max upload      : {MAX_UPLOAD_LABEL} ({'Premium' if OWNER_PREMIUM else 'Standard'})")
    print(f"🔄 Auto-refresh    : every {DASHBOARD_REFRESH_INTERVAL}s")
    print(f"⏱️  Min edit gap   : {MIN_EDIT_GAP}s")
    print(f"📄 Tasks per page  : {TASKS_PER_PAGE}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    await app.start()

    # Load persisted user settings (as_video toggle etc.) into memory
    async for doc in settings_col.find({"_id": {"$regex": "^user_settings_"}}):
        try:
            uid = int(doc["_id"].replace("user_settings_", ""))
            user_settings[uid] = {"as_video": doc.get("as_video", False)}
        except Exception:
            pass

    try:
        await app.send_message(OWNER_ID, "✅ **Bot Restarted Successfully!**")
        print(f"✅ Restart notification sent to Owner ({OWNER_ID})")
    except Exception as e:
        print(f"⚠️ Could not send restart notification to Owner: {e}")

    await start_web_server()
    print("🤖 Bot ready — listening for commands...")
    await idle()
    await app.stop()



# ════════════════════════════════════════════════════════
#  YOUTUBE DOWNLOADER  (/yl command — powered by yt-dlp)
# ════════════════════════════════════════════════════════

# ── YT Config ────────────────────────────────────────────────────────────────
YT_PROXY_URL   = os.environ.get("YT_PROXY_URL", "http://dLAG1sTQ6:qKE6euVsA@138.249.190.195:62694")       # Optional: set in .env
YT_AUTH_USERS  = []                                         # Empty = all users allowed
YT_MAX_PL      = 50                                         # Max playlist items
YT_SESSION_TTL = 600                                        # Session expiry (seconds)

_YT_BASE = os.path.dirname(os.path.abspath(__file__))
YT_DL_DIR = os.path.join(DOWNLOAD_DIR, "yt_downloads")
os.makedirs(YT_DL_DIR, exist_ok=True)

YT_COOKIES = None
for _p in [os.path.join(_YT_BASE, "cookies.txt"), os.path.expanduser("~/cookies.txt")]:
    if os.path.exists(_p):
        YT_COOKIES = _p
        break

yt_logger = logging.getLogger("YTLeech")

# ── YT Session stores ─────────────────────────────────────────────────────────
YT_URL_SESSIONS = {}   # uid → session data for single video quality picker
YT_PL_SESSIONS  = {}   # uid → playlist session
YT_WAITING_SEL  = {}   # user_id → pl_uid (waiting for selection text)

def _yt_new_uid():
    return uuid.uuid4().hex[:10]

def _yt_cleanup():
    now = time.time()
    for d in [YT_URL_SESSIONS, YT_PL_SESSIONS]:
        for k in list(d.keys()):
            if now - d[k].get("created", 0) > YT_SESSION_TTL:
                d.pop(k, None)

# ── YT Helpers ────────────────────────────────────────────────────────────────
def yt_humanbytes(size):
    if not size: return "0 B"
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0: return f"{size:.2f} {u}"
        size /= 1024.0

def yt_time_fmt(sec):
    sec = int(sec or 0)
    h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def yt_pbar(pct, w=10):
    f = math.floor(pct / (100 / w))
    return f"[{'█'*f}{'░'*(w-f)}] {pct:.1f}%"

def yt_is_auth(uid):
    return not YT_AUTH_USERS or uid in YT_AUTH_USERS

def yt_clean_url(url):
    url = url.strip().rstrip("/")
    url = re.sub(r"[?&]si=[^&]+", "", url)
    url = re.sub(r"[?&]utm_[^&\s]+", "", url)
    return url.rstrip("?&")

def yt_extract_url(text):
    m = re.search(r'((?:rtmps?|mms|rtsp|https?|ftp)://[^\s]+)', text)
    return m.group(0) if m else None

def yt_is_playlist(url):
    if re.search(r"[?&]list=PL[a-zA-Z0-9_-]+", url): return True
    if re.search(r"(playlist\?list=|/playlist/)", url, re.I): return True
    return False

async def _yt_safe_edit(msg, text):
    with suppress(Exception):
        await msg.edit_text(text)

# ── YT Progress ───────────────────────────────────────────────────────────────
class YtDlpProgress:
    def __init__(self, msg, loop, prefix="", is_pl=False):
        self.msg = msg; self.loop = loop; self.prefix = prefix; self.is_pl = is_pl
        self._dl = 0; self._last = 0; self._speed = 0
        self._eta = 0; self._size = 0; self._t = 0

    def hook(self, d):
        now = time.time()
        if now - self._t < 4: return
        self._t = now
        if d["status"] == "finished":
            if self.is_pl: self._last = 0
            return
        if d["status"] != "downloading": return
        self._speed = d.get("speed") or 0
        self._eta   = d.get("eta")   or 0
        if self.is_pl:
            chunk = (d.get("downloaded_bytes") or 0) - self._last
            self._last = d.get("downloaded_bytes") or 0
            self._dl  += chunk
        else:
            self._size = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            self._dl   = d.get("downloaded_bytes") or 0
        try:
            pct = (self._dl / self._size * 100) if self._size else 0
        except ZeroDivisionError:
            pct = 0
        bar  = yt_pbar(pct)
        text = (
            f"{self.prefix}⬇️ **Downloading...**\n\n{bar}\n\n"
            f"📦 `{yt_humanbytes(self._dl)}`" +
            (f" / `{yt_humanbytes(self._size)}`" if self._size else "") +
            f"\n⚡ Speed: `{yt_humanbytes(self._speed)}/s`" +
            (f"\n⏳ ETA: `{yt_time_fmt(self._eta)}`" if self._eta else "")
        )
        asyncio.run_coroutine_threadsafe(_yt_safe_edit(self.msg, text), self.loop)

async def yt_upload_progress(current, total, msg, start_time, label="Uploading"):
    now = time.time(); diff = now - start_time
    if round(diff % 5) != 0 and current != total: return
    pct   = current * 100 / total if total else 0
    speed = current / diff if diff > 0 else 0
    eta   = (total - current) / speed if speed > 0 else 0
    text  = (
        f"📤 **{label}**\n\n{yt_pbar(pct)}\n\n"
        f"📦 `{yt_humanbytes(current)}` / `{yt_humanbytes(total)}`\n"
        f"⚡ Speed: `{yt_humanbytes(speed)}/s`\n"
        f"⏳ ETA: `{yt_time_fmt(eta)}`"
    )
    await _yt_safe_edit(msg, text)

# ── yt-dlp Options ────────────────────────────────────────────────────────────
def _yt_base_opts():
    o = {
        "usenetrc": True,
        "allow_multiple_video_streams": True,
        "allow_multiple_audio_streams": True,
        "noprogress": True,
        "overwrites": True,
        "writethumbnail": True,
        "trim_file_name": 200,
        "fragment_retries": 10,
        "retries": 10,
        "nocheckcertificate": True,
        "retry_sleep_functions": {
            "http": lambda n: 3, "fragment": lambda n: 3,
            "file_access": lambda n: 3, "extractor": lambda n: 3,
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {"youtube": {"player_client": ["web", "tv"], "skip": ["dash"]}},
    }
    if YT_COOKIES:   o["cookiefile"] = YT_COOKIES
    if YT_PROXY_URL: o["proxy"]      = YT_PROXY_URL
    return o

def _yt_info_opts():
    o = _yt_base_opts()
    o.update({"quiet": True, "no_warnings": True, "playlist_items": "0", "format": "bv*+ba/b"})
    return o

def _yt_dl_opts(fmt, out_tmpl, tracker=None, is_pl=False):
    o = _yt_base_opts()
    o["outtmpl"] = {"default": out_tmpl, "thumbnail": out_tmpl.replace(".%(ext)s", "_t.%(ext)s")}
    o["postprocessors"] = [{"add_chapters": True, "add_infojson": "if_exists",
                             "add_metadata": True, "key": "FFmpegMetadata"}]
    if tracker: o["progress_hooks"] = [tracker.hook]
    if is_pl:   o["ignoreerrors"]   = True

    is_audio = fmt.startswith("ba/b") or fmt in ("mp3",)
    if is_audio:
        parts = fmt.split("-") if "-" in fmt else ["ba/b", "mp3", "192"]
        afmt  = parts[1] if len(parts) > 1 else "mp3"
        arate = parts[2] if len(parts) > 2 else "192"
        o["format"] = "ba/b"
        o["postprocessors"].append({"key": "FFmpegExtractAudio",
                                     "preferredcodec": afmt, "preferredquality": arate})
    else:
        o["format"] = fmt

    o["postprocessors"].append({"format": "jpg", "key": "FFmpegThumbnailsConvertor", "when": "before_dl"})
    ext = ".mp3" if is_audio else ".mp4"
    if ext in [".mp3", ".mkv", ".mka", ".ogg", ".opus", ".flac", ".m4a", ".mp4", ".mov", ".m4v"]:
        o["postprocessors"].append({"already_have_thumbnail": True, "key": "EmbedThumbnail"})
    return o

# ── Format Parser ─────────────────────────────────────────────────────────────
def yt_parse_formats(result):
    if "entries" in result:
        fmts = {}
        for h in ["144", "240", "360", "480", "720", "1080", "1440", "2160"]:
            fmts[f"{h}|mp4"]  = f"bv*[height<=?{h}][ext=mp4]+ba[ext=m4a]/b[height<=?{h}]"
            fmts[f"{h}|webm"] = f"bv*[height<=?{h}][ext=webm]+ba/b[height<=?{h}]"
        return fmts, True

    fmts = {}; is_m4a = False
    for item in result.get("formats", []):
        if not item.get("tbr"): continue
        fid  = item["format_id"]
        size = item.get("filesize") or item.get("filesize_approx") or 0
        if item.get("video_ext") == "none" and (item.get("resolution") == "audio only" or item.get("acodec") != "none"):
            if item.get("audio_ext") == "m4a": is_m4a = True
            b_name = f"{item.get('acodec') or fid}-{item['ext']}"
            v_fmt  = fid
        elif item.get("height"):
            h = item["height"]; ext = item["ext"]
            fps = item["fps"] if item.get("fps") else ""
            b_name = f"{h}p{fps}-{ext}"
            ba_ext = "[ext=m4a]" if is_m4a and ext == "mp4" else ""
            v_fmt  = f"{fid}+ba{ba_ext}/b[height=?{h}]"
        else:
            continue
        fmts.setdefault(b_name, {})[f"{item['tbr']}"] = [size, v_fmt]
    return fmts, False

# ── YT Keyboards ──────────────────────────────────────────────────────────────
def _yt_kb_main(fmts, uid, is_pl, tl):
    btns = []; row = []
    if is_pl:
        for key in ["144|mp4","240|mp4","360|mp4","480|mp4","720|mp4","1080|mp4","1440|mp4","2160|mp4"]:
            row.append(InlineKeyboardButton(f"{key.split('|')[0]}p-mp4", callback_data=f"yl|{uid}|fmt|{key}"))
            if len(row) == 3: btns.append(row); row = []
        if row: btns.append(row); row = []
        for key in ["144|webm","240|webm","360|webm","480|webm","720|webm","1080|webm","1440|webm","2160|webm"]:
            row.append(InlineKeyboardButton(f"{key.split('|')[0]}p-webm", callback_data=f"yl|{uid}|fmt|{key}"))
            if len(row) == 3: btns.append(row); row = []
        if row: btns.append(row)
        msg = f"📋 **Playlist Quality:**\n⏳ `{yt_time_fmt(tl)}`"
    else:
        for b_name, tbr_dict in fmts.items():
            if len(tbr_dict) == 1:
                tbr, vl = next(iter(tbr_dict.items()))
                row.append(InlineKeyboardButton(f"{b_name} ({yt_humanbytes(vl[0])})", callback_data=f"yl|{uid}|sub|{b_name}|{tbr}"))
            else:
                row.append(InlineKeyboardButton(b_name, callback_data=f"yl|{uid}|dict|{b_name}"))
            if len(row) == 2: btns.append(row); row = []
        if row: btns.append(row)
        msg = f"🎬 **Select Quality:**\n⏳ `{yt_time_fmt(tl)}`"
    btns.append([
        InlineKeyboardButton("🎵 MP3", callback_data=f"yl|{uid}|mp3|"),
        InlineKeyboardButton("🎧 Audio Formats", callback_data=f"yl|{uid}|audiofmt|"),
    ])
    btns.append([
        InlineKeyboardButton("⭐ Best Video", callback_data=f"yl|{uid}|fmt|bv*+ba/b"),
        InlineKeyboardButton("🔊 Best Audio", callback_data=f"yl|{uid}|fmt|ba/b"),
    ])
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data=f"yl|{uid}|cancel|")])
    return msg, InlineKeyboardMarkup(btns)

def _yt_kb_sub(b_name, tbr_dict, uid, tl):
    btns = []; row = []
    for tbr, vl in tbr_dict.items():
        row.append(InlineKeyboardButton(f"{tbr}K ({yt_humanbytes(vl[0])})", callback_data=f"yl|{uid}|sub|{b_name}|{tbr}"))
        if len(row) == 2: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("◀️ Back", callback_data=f"yl|{uid}|back|"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"yl|{uid}|cancel|")])
    return f"🎚️ **{b_name}** bitrate:\n⏳ `{yt_time_fmt(tl)}`", InlineKeyboardMarkup(btns)

def _yt_kb_mp3(uid, tl):
    return (f"🎵 MP3 Bitrate:\n⏳ `{yt_time_fmt(tl)}`", InlineKeyboardMarkup([[
        InlineKeyboardButton("64K",  callback_data=f"yl|{uid}|fmt|ba/b-mp3-64"),
        InlineKeyboardButton("128K", callback_data=f"yl|{uid}|fmt|ba/b-mp3-128"),
        InlineKeyboardButton("192K", callback_data=f"yl|{uid}|fmt|ba/b-mp3-192"),
        InlineKeyboardButton("320K", callback_data=f"yl|{uid}|fmt|ba/b-mp3-320"),
    ],[InlineKeyboardButton("◀️ Back", callback_data=f"yl|{uid}|back|"),
       InlineKeyboardButton("❌ Cancel", callback_data=f"yl|{uid}|cancel|")]]))

def _yt_kb_audiofmt(uid, tl):
    btns = []; row = []
    for f in ["aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav"]:
        row.append(InlineKeyboardButton(f, callback_data=f"yl|{uid}|audioq|ba/b-{f}-"))
        if len(row) == 4: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("◀️ Back", callback_data=f"yl|{uid}|back|"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"yl|{uid}|cancel|")])
    return f"🎧 Audio Format:\n⏳ `{yt_time_fmt(tl)}`", InlineKeyboardMarkup(btns)

def _yt_kb_audioq(prefix, uid, tl):
    btns = []; row = []
    for q in range(11):
        row.append(InlineKeyboardButton(str(q), callback_data=f"yl|{uid}|fmt|{prefix}{q}"))
        if len(row) == 4: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("◀️ Back", callback_data=f"yl|{uid}|audiofmt|"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"yl|{uid}|cancel|")])
    return f"🎚️ Quality (0=best, 10=worst):\n⏳ `{yt_time_fmt(tl)}`", InlineKeyboardMarkup(btns)

def _yt_kb_playlist(uid, total):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📥 Download All ({total})", callback_data=f"ylpl|{uid}|all")],
        [InlineKeyboardButton("🎯 Select Videos", callback_data=f"ylpl|{uid}|select")],
        [InlineKeyboardButton("🖼️ Thumbnails", callback_data=f"ylpl|{uid}|thumbs"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"ylpl|{uid}|cancel")],
    ])

# ── Info Extraction (blocking, runs in executor) ──────────────────────────────
def _yt_blocking_info(url):
    try:
        with yt_dlp.YoutubeDL(_yt_info_opts()) as ydl:
            r = ydl.extract_info(url, download=False)
            if r is None: raise ValueError("Info result is None")
            return r
    except Exception as e:
        yt_logger.error(f"Info: {e}")
        return None

def _yt_blocking_playlist_info(url):
    opts = _yt_info_opts()
    opts.pop("playlist_items", None)
    opts.pop("format", None)
    opts.update({"extract_flat": True, "playlistend": YT_MAX_PL, "ignoreerrors": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        yt_logger.error(f"PL info: {e}")
        return None

# ── Download (blocking, runs in executor) ─────────────────────────────────────
def _yt_blocking_download(url, fmt, out_tmpl, smsg, loop, is_pl=False):
    try:
        tracker = YtDlpProgress(smsg, loop, is_pl=is_pl)
        opts    = _yt_dl_opts(fmt, out_tmpl, tracker, is_pl)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info   = ydl.extract_info(url, download=True)
            if not info: return None
            actual = ydl.prepare_filename(info)
            if fmt.startswith("ba/b") or fmt == "mp3":
                parts = fmt.split("-") if "-" in fmt else ["ba/b", "mp3", "192"]
                afmt  = parts[1] if len(parts) > 1 else "mp3"
                ext   = "ogg" if afmt == "vorbis" else "m4a" if afmt == "alac" else afmt
                actual = os.path.splitext(actual)[0] + f".{ext}"
            if not os.path.exists(actual):
                dl_dir = os.path.dirname(actual)
                base   = os.path.splitext(os.path.basename(actual))[0][:30]
                for f in os.listdir(dl_dir or "."):
                    if base in f and not f.endswith((".jpg", ".jpeg", ".png", ".webp", ".part")):
                        actual = os.path.join(dl_dir or ".", f); break
            if not os.path.exists(actual) or os.path.getsize(actual) == 0:
                dl_dir = os.path.dirname(actual); found = None; best_sz = 0
                for f in os.listdir(dl_dir or "."):
                    if f.endswith((".jpg", ".jpeg", ".png", ".webp", ".part", ".ytdl")): continue
                    fp2 = os.path.join(dl_dir or ".", f); fsz = os.path.getsize(fp2)
                    if fsz > best_sz: best_sz = fsz; found = fp2
                if found and best_sz > 0: actual = found
                else: return None
            if not os.path.exists(actual) or os.path.getsize(actual) == 0:
                return None
            real_info = info
            if "entries" in info and info["entries"]:
                real_info = info["entries"][0] or info
            return {"filepath": actual, "info": real_info,
                    "duration": real_info.get("duration", 0),
                    "title": real_info.get("title", "Video")}
    except Exception as e:
        yt_logger.error(f"DL: {e}")
        return None

# ── YT Upload ─────────────────────────────────────────────────────────────────
def _yt_find_thumb(dl_dir):
    for f in os.listdir(dl_dir):
        if f.endswith((".jpg", ".jpeg")):
            return os.path.join(dl_dir, f)
    return None

async def _yt_upload(chat_id, result, fmt, smsg):
    fp    = result["filepath"]
    info  = result["info"]
    dur   = result["duration"]
    upl   = info.get("uploader", "") or info.get("channel", "")
    sz    = os.path.getsize(fp)
    fname = os.path.basename(fp)
    cap   = f"📁 `{fname}`"
    thumb = _yt_find_thumb(os.path.dirname(fp))
    if sz > 2 * 1024**3:
        await _yt_safe_edit(smsg, "❌ File >2GB!"); return False
    start = time.time()
    try:
        is_audio = fmt.startswith("ba/b") or fmt == "mp3"
        if is_audio:
            await app.send_audio(chat_id, fp, caption=cap, duration=dur,
                title=fname[:64], performer=upl, thumb=thumb,
                progress=yt_upload_progress, progress_args=(smsg, start, "Uploading Audio..."))
        else:
            await app.send_video(chat_id, fp, caption=cap, duration=dur,
                width=info.get("width", 1280), height=info.get("height", 720),
                thumb=thumb, supports_streaming=True,
                progress=yt_upload_progress, progress_args=(smsg, start, "Uploading Video..."))
        return True
    except Exception as e:
        yt_logger.error(f"Upload: {e}")
        await _yt_safe_edit(smsg, f"❌ Upload failed: `{str(e)[:200]}`")
        return False

# ── Quality Picker ────────────────────────────────────────────────────────────
async def _yt_show_quality_picker(url, smsg, user_id=None):
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _yt_blocking_info, url)
    if not info:
        await _yt_safe_edit(smsg,
            "❌ **Could not fetch video info!**\n\n"
            "• Private/age-restricted video?\n"
            "• Try refreshing cookies.txt\n"
            "• Check the URL is correct")
        return
    fmts, is_pl = yt_parse_formats(info)
    uid = _yt_new_uid()
    YT_URL_SESSIONS[uid] = {
        "url": url, "info": info, "fmts": fmts,
        "is_pl": is_pl, "created": time.time(), "timeout": 120,
        "user_id": user_id,
    }
    title  = (info.get("title", "") or "")[:60]
    upl    = info.get("uploader", "") or info.get("channel", "")
    dur    = yt_time_fmt(info.get("duration", 0))
    views  = info.get("view_count", 0) or 0
    likes  = info.get("like_count",  0) or 0
    udate  = info.get("upload_date", "")
    if udate:
        try: udate = datetime.strptime(udate, "%Y%m%d").strftime("%d %b %Y")
        except: pass
    info_txt = (
        f"🎬 **{title}**\n\n"
        f"👤 `{upl}`\n"
        f"⏱️ `{dur}`  📅 `{udate}`\n"
        f"👁️ `{views:,}`  ❤️ `{likes:,}`\n\n"
    )
    q_msg, kb = _yt_kb_main(fmts, uid, is_pl, 120)
    thumb = info.get("thumbnail")
    with suppress(Exception): await smsg.delete()
    try:
        if thumb:
            await smsg.reply_photo(photo=thumb, caption=info_txt + q_msg, reply_markup=kb)
        else:
            raise Exception()
    except Exception:
        with suppress(Exception):
            await smsg.reply_text(info_txt + q_msg, reply_markup=kb)

# ── Playlist selection text handler ──────────────────────────────────────────
@app.on_message(filters.text & ~filters.command(["start","help","settings","l","leech","ql","yl","ytleech","stop","setdump","ping"]))
async def yt_handle_selection_text(client, message: Message):
    """Intercepts text replies when user is in playlist video-selection mode."""
    uid = message.from_user.id
    if uid not in YT_WAITING_SEL:
        return
    pl_uid = YT_WAITING_SEL[uid]
    e      = YT_PL_SESSIONS.get(pl_uid)
    if not e:
        YT_WAITING_SEL.pop(uid, None); return
    total   = len(e["entries"])
    indices = _yt_parse_sel(message.text.strip(), total)
    if indices is None:
        await message.reply_text(f"❌ Invalid selection!\nFormat: `1,3,5` or `1-10`\nTotal: `{total}`")
        return
    e["sel"] = indices
    YT_WAITING_SEL.pop(uid, None)
    smsg = await message.reply_text(f"✅ `{len(indices)}` selected. Fetching quality options...")
    loop = asyncio.get_running_loop()
    fu   = e["entries"][indices[0]].get("url") or e["entries"][indices[0]].get("webpage_url")
    try:
        info = await loop.run_in_executor(None, _yt_blocking_info, fu)
        if not info: raise ValueError()
        fmts, _ = yt_parse_formats(info)
        s_uid   = _yt_new_uid()
        YT_URL_SESSIONS[s_uid] = {
            "url": e["url"], "info": info, "fmts": fmts,
            "is_pl": True, "created": time.time(), "timeout": 120,
            "user_id": uid, "pl_uid": pl_uid, "pl_indices": indices,
        }
        msg_t, kb = _yt_kb_main(fmts, s_uid, False, 120)
        await smsg.edit_text(f"🎨 **Quality:** `{len(indices)}` videos:\n\n{msg_t}", reply_markup=kb)
    except Exception as ex:
        await _yt_safe_edit(smsg, f"❌ {ex}")


# ── /yl command ───────────────────────────────────────────────────────────────
@app.on_message(filters.command(["yl", "ytleech"]))
async def yl_command(client, message: Message):
    """Download a YouTube (or any yt-dlp-supported) URL and leech to Telegram."""
    if not yt_is_auth(message.from_user.id):
        return await message.reply_text("⛔ Not authorized.")
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text(
            "❌ **Usage:** `/yl <youtube_url>`\n\n"
            "**Examples:**\n"
            "• `/yl https://youtu.be/dQw4w9WgXcQ`\n"
            "• `/yl https://youtube.com/playlist?list=PLxxx`\n\n"
            "Supports: YouTube, Instagram, TikTok, Twitter, Vimeo, SoundCloud & more."
        )
    raw_url = yt_extract_url(args[1]) or args[1].strip()
    url = yt_clean_url(raw_url)
    if not url.startswith(("http://", "https://", "rtmp://")):
        return await message.reply_text("❌ Invalid URL. Please send a valid link.")
    _yt_cleanup()
    status = await message.reply_text("🔍 **Fetching info...**")
    loop = asyncio.get_running_loop()
    if yt_is_playlist(url):
        await _yt_safe_edit(status, "📋 **Fetching playlist info...**")
        info = await loop.run_in_executor(None, _yt_blocking_playlist_info, url)
        if info and info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e][:YT_MAX_PL]
            if entries:
                p_uid = _yt_new_uid()
                YT_PL_SESSIONS[p_uid] = {"url": url, "entries": entries,
                                          "info": info, "created": time.time(),
                                          "user_id": message.from_user.id}
                total_dur = sum(e.get("duration", 0) or 0 for e in entries)
                pl_title  = (info.get("title", "Playlist"))[:60]
                channel   = info.get("uploader") or info.get("channel", "")
                vl = ""
                for i, e in enumerate(entries[:12], 1):
                    vl += f"`{i:02d}.` {(e.get('title') or f'Video {i}')[:35]} `[{yt_time_fmt(e.get('duration', 0))}]`\n"
                if len(entries) > 12: vl += f"_...and {len(entries)-12} more_"
                cap = (f"📋 **{pl_title}**\n👤 `{channel}`\n"
                       f"🎬 `{len(entries)}` videos  ⏱️ `{yt_time_fmt(total_dur)}`\n\n{vl}\n\n**What to download?**")
                with suppress(Exception): await status.delete()
                try:
                    th = (info.get("thumbnails") or [{}])[-1].get("url", "")
                    if th: await message.reply_photo(photo=th, caption=cap, reply_markup=_yt_kb_playlist(p_uid, len(entries)))
                    else:  raise Exception()
                except Exception:
                    await message.reply_text(cap, reply_markup=_yt_kb_playlist(p_uid, len(entries)))
                return
    await _yt_show_quality_picker(url, status, message.from_user.id)

# ── Quality Picker Callback ────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^yl\|"))
async def yt_quality_cb(client, cq: CallbackQuery):
    parts  = cq.data.split("|")
    uid    = parts[1]; action = parts[2]; rest = parts[3:] if len(parts) > 3 else []
    e      = YT_URL_SESSIONS.get(uid)
    if not e:
        await safe_answer(cq, "⚠️ Session expired! Send the link again.", show_alert=True); return
    if e.get("user_id") and cq.from_user.id != e["user_id"]:
        await safe_answer(cq, "❌ This is not your menu!", show_alert=True); return
    await safe_answer(cq)
    tl = max(0, e["timeout"] - (time.time() - e["created"]))
    fmts = e["fmts"]; is_pl = e["is_pl"]

    if action == "cancel":
        YT_URL_SESSIONS.pop(uid, None)
        with suppress(Exception): await cq.message.delete()
        return
    if action == "back":
        msg, kb = _yt_kb_main(fmts, uid, is_pl, tl)
        await cq.message.edit_text(msg, reply_markup=kb); return
    if action == "mp3":
        msg, kb = _yt_kb_mp3(uid, tl)
        await cq.message.edit_text(msg, reply_markup=kb); return
    if action == "audiofmt":
        msg, kb = _yt_kb_audiofmt(uid, tl)
        await cq.message.edit_text(msg, reply_markup=kb); return
    if action == "audioq":
        prefix = rest[0] if rest else "ba/b-mp3-"
        msg, kb = _yt_kb_audioq(prefix, uid, tl)
        await cq.message.edit_text(msg, reply_markup=kb); return
    if action == "dict":
        b_name = rest[0]; tbr_dict = fmts.get(b_name, {})
        msg, kb = _yt_kb_sub(b_name, tbr_dict, uid, tl)
        await cq.message.edit_text(msg, reply_markup=kb); return
    if action == "sub":
        b_name = rest[0]; tbr = rest[1]
        vl = fmts.get(b_name, {}).get(tbr)
        if not vl: await safe_answer(cq, "Format not found!"); return
        await _yt_start_dl(uid, vl[1], e, cq, client); return
    if action == "fmt":
        qual = rest[0] if rest else "bv*+ba/b"
        if "|" in qual and qual in fmts: qual = fmts[qual]
        await _yt_start_dl(uid, qual, e, cq, client); return

# ── Playlist Callback ─────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^ylpl\|"))
async def yt_playlist_cb(client, cq: CallbackQuery):
    parts = cq.data.split("|"); uid = parts[1]; sub = parts[2]
    e = YT_PL_SESSIONS.get(uid)
    if sub == "cancel":
        YT_PL_SESSIONS.pop(uid, None); YT_WAITING_SEL.pop(cq.from_user.id, None)
        with suppress(Exception): await cq.message.delete()
        await safe_answer(cq); return
    if not e: await safe_answer(cq, "⚠️ Session expired!", show_alert=True); return
    await safe_answer(cq)

    if sub == "thumbs":
        sm = await cq.message.reply_text("🖼️ Fetching thumbnails..."); sent = 0
        for i, en in enumerate(e["entries"], 1):
            tu = en.get("thumbnail") or (en.get("thumbnails") or [{}])[-1].get("url", "")
            if not tu: continue
            try:
                r = requests.get(tu, timeout=10)
                if r.status_code != 200: continue
                p = os.path.join(YT_DL_DIR, f"th_{uid}_{i}.jpg")
                with open(p, "wb") as f: f.write(r.content)
                await client.send_document(cq.message.chat.id, p,
                    caption=f"🖼️ `{i}.` {(en.get('title') or '')[:40]}")
                with suppress(Exception): os.remove(p)
                sent += 1; await asyncio.sleep(0.5)
            except Exception: pass
            if i % 5 == 0: await _yt_safe_edit(sm, f"🖼️ `{i}/{len(e['entries'])}`...")
        await _yt_safe_edit(sm, f"✅ `{sent}` thumbnails sent!"); return

    if sub == "select":
        YT_WAITING_SEL[cq.from_user.id] = uid; total = len(e["entries"]); vl = ""
        for i, en in enumerate(e["entries"][:20], 1):
            vl += f"`{i:02d}.` {(en.get('title') or f'Video {i}')[:35]} `[{yt_time_fmt(en.get('duration', 0))}]`\n"
        if total > 20: vl += f"_...{total-20} more_"
        await cq.message.reply_text(
            f"🎯 **Select videos:**\nTotal: `{total}`\n\n{vl}\nFormat: `1,3,5` or `1-10`"); return

    if sub == "all":
        sm = await cq.message.reply_text("🔍 Fetching quality options...")
        loop = asyncio.get_running_loop()
        fu = e["entries"][0].get("url") or e["entries"][0].get("webpage_url")
        try:
            info = await loop.run_in_executor(None, _yt_blocking_info, fu)
            if not info: raise ValueError()
            fmts2, _ = yt_parse_formats(info)
            s_uid = _yt_new_uid()
            YT_URL_SESSIONS[s_uid] = {
                "url": e["url"], "info": info, "fmts": fmts2,
                "is_pl": True, "created": time.time(), "timeout": 120,
                "user_id": cq.from_user.id, "pl_uid": uid,
                "pl_indices": list(range(len(e["entries"]))),
            }
            msg_t, kb = _yt_kb_main(fmts2, s_uid, True, 120)
            await sm.edit_text(f"🎨 **Quality:**\n📋 `{len(e['entries'])}` videos:\n\n{msg_t}", reply_markup=kb)
        except Exception as ex:
            await _yt_safe_edit(sm, f"❌ {ex}")

# ── Download Tasks ─────────────────────────────────────────────────────────────
async def _yt_start_dl(uid, qual, e, cq, client):
    YT_URL_SESSIONS.pop(uid, None)
    chat_id    = cq.message.chat.id
    url        = e["url"]
    pl_uid     = e.get("pl_uid")
    pl_indices = e.get("pl_indices")
    smsg = await cq.message.reply_text(f"⚙️ **Starting download...**\n🎞️ `{qual[:50]}`")
    if pl_uid and pl_indices is not None:
        pl_e    = YT_PL_SESSIONS.get(pl_uid, {})
        entries = [pl_e["entries"][i] for i in pl_indices if i < len(pl_e.get("entries", []))]
        asyncio.create_task(_yt_dl_playlist(entries, qual, e["info"], smsg, client, chat_id, pl_uid))
    else:
        asyncio.create_task(_yt_dl_single(url, qual, smsg, client, chat_id))

async def _yt_dl_single(url, qual, smsg, client, chat_id):
    loop    = asyncio.get_running_loop()
    uid     = _yt_new_uid()
    dl_dir  = os.path.join(YT_DL_DIR, uid)
    os.makedirs(dl_dir, exist_ok=True)
    out_tmpl = os.path.join(dl_dir, "%(title,fulltitle,alt_title)s %(height)sp%(fps)s.fps %(tbr)d.%(ext)s")
    await _yt_safe_edit(smsg, "⬇️ **Downloading...**")
    result = await loop.run_in_executor(None, _yt_blocking_download, url, qual, out_tmpl, smsg, loop, False)
    if not result:
        await _yt_safe_edit(smsg, "❌ **Download failed!**\n\n• Refresh cookies.txt\n• Private video?\n• Check URL")
        with suppress(Exception): shutil.rmtree(dl_dir)
        return
    await _yt_safe_edit(smsg, f"📤 **Uploading...**\n📦 `{yt_humanbytes(os.path.getsize(result['filepath']))}`")
    ok = await _yt_upload(chat_id, result, qual, smsg)
    if ok:
        with suppress(Exception): await smsg.delete()
    with suppress(Exception): shutil.rmtree(dl_dir)

async def _yt_dl_playlist(entries, qual, base_info, smsg, client, chat_id, pl_uid):
    loop = asyncio.get_running_loop(); total = len(entries); failed = []
    for idx, en in enumerate(entries, 1):
        vid_id = en.get("id", ""); ie_key = en.get("ie_key", "") or en.get("extractor", "")
        if vid_id and ("youtube" in ie_key.lower() or not en.get("url")):
            vurl = f"https://www.youtube.com/watch?v={vid_id}"
        else:
            vurl = en.get("url") or en.get("webpage_url") or ""
            if vurl and not vurl.startswith("http"):
                vurl = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
        if not vurl:
            failed.append(en.get("title") or f"Video {idx}"); continue
        vtitle = (en.get("title") or f"Video {idx}")[:50]
        prefix = f"📋 **{idx}/{total}** — `{vtitle}`\n\n"
        await _yt_safe_edit(smsg, f"{prefix}⬇️ Downloading...")
        uid    = _yt_new_uid()
        dl_dir = os.path.join(YT_DL_DIR, uid)
        os.makedirs(dl_dir, exist_ok=True)
        out_tmpl = os.path.join(dl_dir, "%(title,fulltitle,alt_title)s %(height)sp%(fps)s.fps %(tbr)d.%(ext)s")
        result = await loop.run_in_executor(None, _yt_blocking_download, vurl, qual, out_tmpl, smsg, loop, False)
        if not result:
            failed.append(vtitle)
            with suppress(Exception): shutil.rmtree(dl_dir)
            continue
        await _yt_safe_edit(smsg, f"{prefix}📤 `{yt_humanbytes(os.path.getsize(result['filepath']))}`...")
        ok = await _yt_upload(chat_id, result, qual, smsg)
        with suppress(Exception): shutil.rmtree(dl_dir)
        if not ok: failed.append(vtitle)
    YT_PL_SESSIONS.pop(pl_uid, None)
    if failed:
        await _yt_safe_edit(smsg,
            f"✅ `{total-len(failed)}/{total}` uploaded.\n❌ Failed:\n" +
            "\n".join(f"• `{t}`" for t in failed[:10]))
    else:
        await _yt_safe_edit(smsg, f"✅ **Playlist done!** `{total}` videos! 🎉")

def _yt_parse_sel(text, total):
    idx = set()
    try:
        for p in text.strip().split(","):
            p = p.strip()
            if "-" in p:
                a, b = map(int, p.split("-", 1))
                if a < 1 or b > total or a > b: return None
                idx.update(range(a - 1, b))
            else:
                n = int(p)
                if n < 1 or n > total: return None
                idx.add(n - 1)
        return sorted(idx)
    except: return None


if __name__ == "__main__":
    app.run(main())
