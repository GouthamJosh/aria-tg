import os
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

import re
import time
import asyncio
import aria2p
from aiohttp import web
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
user_thumbnails   = {}   # uid -> Telegram file_id of custom thumbnail
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
# ── Thumbnail helpers ─────────────────────────────────────────────────────────
THUMB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".m4v", ".ts", ".wmv")

async def generate_thumbnail(file_path: str):
    """Auto-generate a thumbnail from a video file using ffmpeg.
    Returns the thumbnail path, or None on failure."""
    try:
        thumb_path = os.path.join(THUMB_DIR, f"auto_{os.path.basename(file_path)}.jpg")
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        duration = float(stdout.decode().strip() or "0")
        seek = max(1, duration * 0.1)

        proc2 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", str(seek), "-i", file_path,
            "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "5", thumb_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc2.communicate()
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        print(f"Thumbnail generation failed for {file_path}: {e}")
    return None


async def get_thumb_for_upload(user_id: int, file_path: str):
    """Return the best available thumbnail path for a given upload.
    Priority: custom user thumbnail -> auto-generated from video -> None."""
    custom_fid = user_thumbnails.get(user_id)
    if custom_fid:
        try:
            tmp = os.path.join(THUMB_DIR, f"custom_{user_id}.jpg")
            await app.download_media(custom_fid, file_name=tmp)
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                return tmp
        except Exception as e:
            print(f"Custom thumbnail download failed: {e}")

    if file_path.lower().endswith(VIDEO_EXTS):
        return await generate_thumbnail(file_path)
    return None


async def upload_to_telegram(file_path: str, message: Message, caption: str = "", task: DownloadTask = None) -> bool:
    if task:
        task.current_phase = "ul"
    user_id    = message.from_user.id
    as_video   = user_settings.get(user_id, {}).get("as_video", False)
    video_exts = VIDEO_EXTS  # defined globally near thumbnail helpers

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

            thumb_path = await get_thumb_for_upload(user_id, file_path)

            sent_msg = None
            if as_video and file_path.lower().endswith(video_exts):
                sent_msg = await message.reply_video(video=file_path, caption=fc, thumb=thumb_path, progress=_progress, supports_streaming=True, disable_notification=True)
            else:
                sent_msg = await message.reply_document(document=file_path, caption=fc, thumb=thumb_path, progress=_progress, disable_notification=True)

            # Clean up auto-generated thumb
            if thumb_path and thumb_path.startswith(THUMB_DIR) and os.path.basename(thumb_path).startswith("auto_"):
                try: os.remove(thumb_path)
                except Exception: pass

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

                    thumb_path = await get_thumb_for_upload(user_id, fp)

                    sent_msg = None
                    if as_video and fp.lower().endswith(video_exts):
                        sent_msg = await message.reply_video(video=fp, caption=cap, thumb=thumb_path, progress=_f_progress, disable_notification=True)
                    else:
                        sent_msg = await message.reply_document(document=fp, caption=cap, thumb=thumb_path, progress=_f_progress, disable_notification=True)

                    # Clean up auto-generated thumb
                    if thumb_path and thumb_path.startswith(THUMB_DIR) and os.path.basename(thumb_path).startswith("auto_"):
                        try: os.remove(thumb_path)
                        except Exception: pass

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
        "Download direct links, magnets, and `.torrent` files and upload them to Telegram.\n\n"
        "Type /help for all commands.\n\n© Maintained By @im_goutham_josh", reply_markup=kb)


@app.on_message(filters.command(["help"]))
async def help_command(client, message: Message):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Close", callback_data="close_help")]])
    await message.reply_text(
        "**📖 Leech Bot — Help & Commands**\n\n"
        "**📥 Download Commands:**\n"
        "• `/ql <link1> <link2>` — Download multiple direct/magnet links at once\n"
        "• `/l <link>` — Single direct link download\n"
        "• `/l <link> -e` — Download & auto-extract archive\n"
        "• **Upload a `.torrent` file** directly to start\n\n"
        "**⚙️ Control:**\n"
        "• `/settings` — Toggle Document / Video upload mode\n"
        "• `/setthumbnail` — Set custom thumbnail (reply to or send a photo)\n"
        "• `/delthumbnail` — Remove custom thumbnail\n"
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


@app.on_message(filters.command(["setthumbnail"]))
async def set_thumbnail_command(client, message: Message):
    """Store the replied-to (or attached) photo as the user's custom upload thumbnail."""
    uid = message.from_user.id
    photo = None

    # User can either attach a photo directly OR reply to a photo message
    if message.photo:
        photo = message.photo
    elif message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo

    if not photo:
        await message.reply_text(
            "📷 **Set Custom Thumbnail**\n\n"
            "Send a photo with the caption `/setthumbnail`, "
            "or reply to any photo with `/setthumbnail`."
        )
        return

    file_id = photo.file_id
    user_thumbnails[uid] = file_id
    await settings_col.update_one(
        {"_id": f"user_settings_{uid}"},
        {"$set": {"thumbnail_file_id": file_id}},
        upsert=True,
    )
    await message.reply_text("✅ **Custom thumbnail saved!** It will be used for all future uploads.")


@app.on_message(filters.command(["delthumbnail"]))
async def del_thumbnail_command(client, message: Message):
    """Remove the user's custom upload thumbnail."""
    uid = message.from_user.id
    if uid not in user_thumbnails:
        await message.reply_text("ℹ️ You don't have a custom thumbnail set.")
        return
    user_thumbnails.pop(uid, None)
    await settings_col.update_one(
        {"_id": f"user_settings_{uid}"},
        {"$unset": {"thumbnail_file_id": ""}},
    )
    # Remove cached file if present
    cached = os.path.join(THUMB_DIR, f"custom_{uid}.jpg")
    if os.path.exists(cached):
        try: os.remove(cached)
        except Exception: pass
    await message.reply_text("🗑 **Custom thumbnail removed.** Auto-thumbnails will be used for videos.")


@app.on_message(filters.command(["settings"]))
async def settings_command(client, message: Message):
    uid = message.from_user.id
    av  = user_settings.get(uid, {}).get("as_video", False)
    mt  = "🎬 Video (Playable)" if av else "📄 Document (File)"
    has_thumb = uid in user_thumbnails
    thumb_label = "🖼 Delete Custom Thumbnail" if has_thumb else "🖼 No Custom Thumbnail"
    kb_buttons = [
        [InlineKeyboardButton(f"Toggle: {mt}", callback_data=f"toggle_mode:{uid}")],
        [InlineKeyboardButton(thumb_label, callback_data=f"del_thumb:{uid}")] if has_thumb else
        [InlineKeyboardButton("🖼 Set Thumbnail → Send photo with /setthumbnail", callback_data="noop")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")],
    ]
    thumb_status = "✅ Custom thumbnail set" if has_thumb else "🔄 Auto (from video frame)"
    await message.reply_text(
        f"⚙️ **Upload Settings**\n\n"
        f"• Upload mode : **{mt}**\n"
        f"• Thumbnail   : **{thumb_status}**\n\n"
        f"_Use /setthumbnail to set a custom thumbnail._",
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


@app.on_callback_query(filters.regex(r"^del_thumb:"))
async def del_thumb_callback(client, cq: CallbackQuery):
    _, uid_str = cq.data.split(":"); uid = int(uid_str)
    if cq.from_user.id != uid:
        await safe_answer(cq, "❌ These aren't your settings!", show_alert=True); return
    user_thumbnails.pop(uid, None)
    await settings_col.update_one(
        {"_id": f"user_settings_{uid}"},
        {"$unset": {"thumbnail_file_id": ""}},
    )
    cached = os.path.join(THUMB_DIR, f"custom_{uid}.jpg")
    if os.path.exists(cached):
        try: os.remove(cached)
        except Exception: pass
    # Rebuild the settings keyboard without the thumbnail button
    av  = user_settings.get(uid, {}).get("as_video", False)
    mt  = "🎬 Video (Playable)" if av else "📄 Document (File)"
    kb_buttons = [
        [InlineKeyboardButton(f"Toggle: {mt}", callback_data=f"toggle_mode:{uid}")],
        [InlineKeyboardButton("🖼 Set Thumbnail → Send photo with /setthumbnail", callback_data="noop")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")],
    ]
    await cq.edit_message_text(
        f"⚙️ **Upload Settings**\n\n"
        f"• Upload mode : **{mt}**\n"
        f"• Thumbnail   : **🔄 Auto (from video frame)**\n\n"
        f"_Use /setthumbnail to set a custom thumbnail._",
        reply_markup=InlineKeyboardMarkup(kb_buttons)
    )
    await safe_answer(cq, "🗑 Custom thumbnail removed!")


@app.on_callback_query(filters.regex(r"^noop$"))
async def noop_callback(client, cq: CallbackQuery):
    await safe_answer(cq, "ℹ️ Send a photo with /setthumbnail to set a custom thumbnail.")



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

    # Load persisted user settings (as_video toggle, custom thumbnails) into memory
    async for doc in settings_col.find({"_id": {"$regex": "^user_settings_"}}):
        try:
            uid = int(doc["_id"].replace("user_settings_", ""))
            user_settings[uid] = {"as_video": doc.get("as_video", False)}
            if doc.get("thumbnail_file_id"):
                user_thumbnails[uid] = doc["thumbnail_file_id"]
        except Exception:
            pass
    print(f"🖼 Custom thumbnails loaded: {len(user_thumbnails)}")

    try:
        await app.send_message(OWNER_ID, "✅ **Bot Restarted Successfully!**")
        print(f"✅ Restart notification sent to Owner ({OWNER_ID})")
    except Exception as e:
        print(f"⚠️ Could not send restart notification to Owner: {e}")

    await start_web_server()
    print("🤖 Bot ready — listening for commands...")
    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
