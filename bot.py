import os
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

from config import (
    API_ID, API_HASH, BOT_TOKEN, OWNER_ID,
    ARIA2_HOST, ARIA2_PORT, ARIA2_SECRET,
    DOWNLOAD_DIR, MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL, OWNER_PREMIUM,
    PORT, ENGINE_DL, ENGINE_UL, ENGINE_EXTRACT,
    DASHBOARD_REFRESH_INTERVAL, MIN_EDIT_GAP,
    WORKERS, MAX_CONCURRENT_TRANSMISSIONS,
    BT_OPTIONS, DIRECT_OPTIONS, TASKS_PER_PAGE
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

active_downloads = {}
user_settings    = {}
user_dashboards  = {}
user_edit_queues = {}

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
    # remove [tag] or (tag) at start
    c = re.sub(r'^\[.*?\]\s*|^\(.*?\)\s*', '', c)
    # remove @username
    c = re.sub(r'^@\w+\s*', '', c)
    c = re.sub(r'https?://\S+', '', c)
    c = re.sub(r'^(?:https?://)?(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:[._\-\s]+)', '', c, flags=re.IGNORECASE)
    # remove leftover domain patterns anywhere in name
    c = re.sub(r'(?:www\.)?[a-zA-Z0-9-]+\.(?:com|net|org|info|xyz|site|club)', '', c, flags=re.IGNORECASE)
    c = c.strip(" .-_")
    if not c:
        c = filename
    # limit length
    if len(c) > 120:
        name, ext = os.path.splitext(c)
        sl = 120 - len(ext) - 3
        c = (name[:sl] + "..." + ext) if sl > 0 else c[:120]
    return c
    
def create_progress_bar(pct: float) -> str:
    if pct >= 100: return "[" + chr(11042)*12 + "] 100%"
    f = int(pct / 100 * 12)
    return f"[{chr(11042)*f}{chr(11041)*(12-f)}] {pct:.1f}%"

def format_speed(s: float) -> str:
    if s >= 1048576: return f"{s/1048576:.2f} MB/s"
    if s >= 1024:    return f"{s/1024:.2f} KB/s"
    return "0 B/s"

def format_size(b: int) -> str:
    gb = b / (1024**3)
    return f"{gb:.2f} GB" if gb >= 1 else f"{b/(1024**2):.2f} MB"

def format_time(s: float) -> str:
    if s <= 0: return "0s"
    h, m, s = int(s//3600), int((s%3600)//60), int(s%60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def get_system_stats() -> dict:
    cpu  = psutil.cpu_percent(interval=0.1)
    ram  = psutil.virtual_memory()
    up   = time.time() - psutil.boot_time()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    disk = psutil.disk_usage(DOWNLOAD_DIR)
    dl_speed = ul_speed = 0
    try:
        gs = aria2.get_global_stat()
        dl_speed = int(getattr(gs, "download_speed", 0) or 0)
        ul_speed = int(getattr(gs, "upload_speed", 0) or 0)
    except Exception:
        pass
    return {
        "cpu":           cpu,
        "ram_percent":   ram.percent,
        "uptime":        format_time(up),
        "disk_free":     disk.free / (1024**3),
        "disk_free_pct": 100.0 - disk.percent,
        "dl_speed":      dl_speed,
        "ul_speed":      ul_speed,
    }

def bot_stats_block(st: dict, task_count: int = 0) -> str:
    return (
        f"⌬ **Bot Stats**\n"
        f"┠ **Tasks:** {task_count}\n"
        f"┠ **CPU:** {st['cpu']:.1f}% | **F:** {st['disk_free']:.2f}GB [{st['disk_free_pct']:.1f}%]\n"
        f"┠ **RAM:** {st['ram_percent']:.1f}% | **UPTIME:** {st['uptime']}\n"
        f"┖ **DL:** {format_speed(st['dl_speed'])} | **UL:** {format_speed(st['ul_speed'])}"
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
        d  = task.dl
        sz = ("Fetching Metadata/Peers..." if d["total"] == 0
              else f"{format_size(d['downloaded'])} of {format_size(d['total'])}")
        tl = f"Elapsed: {format_time(d['elapsed'])} | ETA: {format_time(d['eta'])}"
        return (f"**{index}. {d['filename'] or 'Connecting...'}**\n"
                f"├ {create_progress_bar(d['progress'])}\n"
                f"├ **Processed** → {sz}\n"
                f"├ **Status** → Download\n"
                f"├ **Speed** → {format_speed(d['speed'])}\n"
                f"├ **Time** → {tl}\n"
                f"{d['peer_line']}"
                f"├ **Engine** → {ENGINE_DL} | **Mode** → #ARIA2 → #Leech\n"
                f"└ **Stop** → /stop_{gs}")

    if p == "ext":
        e   = task.ext
        pct = e["pct"]
        if e["total"] > 0:
            sz = f"{format_size(e['extracted'])} of {format_size(e['total'])}"
        elif e["archive_size"] > 0:
            sz = f"Archive: {format_size(e['archive_size'])}"
        else:
            sz = "Preparing..."
        tl  = f"Elapsed: {format_time(e['elapsed'])} | ETA: {format_time(e['remaining'])}"
        ft  = f"📄 {e['file_index']} / {e['total_files']} files" if e["total_files"] > 0 else "Scanning archive..."
        cur = e["cur_file"]
        if cur and len(cur) > 45: cur = cur[:42] + "..."
        fl  = f"`{cur}`" if cur else "`preparing...`"
        sp  = format_speed(e["speed"]) if e["speed"] > 0 else "Calculating..."
        return (f"**{index}. 📦 {e['filename'] or 'Extracting...'}**\n"
                f"├ {create_progress_bar(pct)}\n"
                f"├ **Extracted** → {sz}\n"
                f"├ **Files** → {ft}\n"
                f"├ **Status** → Extracting\n"
                f"├ **Speed** → {sp}\n"
                f"├ **Time** → {tl}\n"
                f"├ **Current** → {fl}\n"
                f"├ **Engine** → {ENGINE_EXTRACT} | **Mode** → #Extract → #Leech\n"
                f"└ **Stop** → /stop_{gs}")

    if p == "ul":
        u   = task.ul
        pc  = min((u["uploaded"] / u["total"]) * 100, 100) if u["total"] > 0 else 0
        tl  = f"Elapsed: {format_time(u['elapsed'])} | ETA: {format_time(u['eta'])}"
        fc_badge = f"📄 {u['file_index']} / {u['total_files']} files" if u["total_files"] > 1 else None
        fname = clean_filename(u["filename"] or "Uploading...")
        lines = [
            f"**{index}. ⬆️ {fname}**\n",
            f"├ {create_progress_bar(pc)}\n",
            f"├ **Uploaded** → {format_size(u['uploaded'])} of {format_size(u['total'])}\n",
        ]
        if fc_badge:
            lines.append(f"├ **Files** → {fc_badge}\n")
        lines += [
            f"├ **Status** → Upload\n",
            f"├ **Speed** → {format_speed(u['speed'])}\n",
            f"├ **Time** → {tl}\n",
            f"├ **Engine** → {ENGINE_UL}\n",
            f"├ **In Mode** → #Aria2\n",
            f"├ **Out Mode** → #Leech\n",
            f"└ **Stop** → /stop_{gs}",
        ]
        return "".join(lines)

    return f"**{index}. Task** → Processing..."


# ── Paginated dashboard builder ───────────────────────────────────────────────
def get_total_pages(user_id: int) -> int:
    tasks = [t for t in active_downloads.values() if t.user_id == user_id]
    if not tasks: return 1
    return max(1, (len(tasks) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)


def build_dashboard_text(user_id: int, user_label: str, page: int = 0) -> str:
    tasks = [t for t in active_downloads.values() if t.user_id == user_id]
    if not tasks:
        return "✅ **All tasks completed!**"

    total_pages = max(1, (len(tasks) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))   # clamp

    start      = page * TASKS_PER_PAGE
    page_tasks = tasks[start: start + TASKS_PER_PAGE]

    stats  = get_system_stats()
    stats["dl_speed"] = sum(t.dl["speed"] for t in tasks if t.current_phase == "dl")
    stats["ul_speed"] = sum(t.ul["speed"] for t in tasks if t.current_phase == "ul")
    div    = "\n\n"
    # Global index so task numbers are continuous across pages (1-based)
    blocks = [build_task_block(t, start + i) for i, t in enumerate(page_tasks, 1)]
    body   = div.join(blocks)

    dl_c = sum(1 for t in tasks if t.current_phase == "dl")
    ex_c = sum(1 for t in tasks if t.current_phase == "ext")
    ul_c = sum(1 for t in tasks if t.current_phase == "ul")
    parts = []
    if dl_c: parts.append(f"⬇️ {dl_c} downloading")
    if ex_c: parts.append(f"📦 {ex_c} extracting")
    if ul_c: parts.append(f"⬆️ {ul_c} uploading")

    page_label = f"  •  Page {page + 1} / {total_pages}" if total_pages > 1 else ""
    return (f"**Task By** {user_label} — {' | '.join(parts)}{page_label}\n\n"
            f"{body}\n\n"
            f"{bot_stats_block(stats, len(tasks))}")


def dashboard_keyboard(user_id: int, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """
    When total_pages > 1:
      Row 1:  ◀ Prev  |  📄 X / Y  |  Next ▶
    Row 2 (always):  🔄 Refresh
    """
    buttons = []
    if total_pages > 1:
        nav = []
        nav.append(InlineKeyboardButton(
            "◀ Prev" if page > 0 else "◀",
            callback_data=f"dpage:{user_id}:{max(0, page - 1)}"
        ))
        nav.append(InlineKeyboardButton(
            f"📄 {page + 1} / {total_pages}",
            callback_data="noop"
        ))
        nav.append(InlineKeyboardButton(
            "Next ▶" if page < total_pages - 1 else "▶",
            callback_data=f"dpage:{user_id}:{min(total_pages - 1, page + 1)}"
        ))
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"dash:{user_id}")])
    return InlineKeyboardMarkup(buttons)


# ── FloodWait-safe edit queue ─────────────────────────────────────────────────
async def edit_worker(user_id: int):
    """Serialised edit queue — one coroutine per user, enforces MIN_EDIT_GAP."""
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
    # Snap back if current page is now out of range (tasks finished)
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
                await dash["msg"].edit_text("✅ **All tasks completed!**", reply_markup=None)
            except Exception: pass
            user_dashboards.pop(user_id, None)
            break
        if time.time() < dash.get("flood_until", 0):
            left = int(dash["flood_until"] - time.time())
            print(f"⏳ FloodWait active user {user_id} — {left}s left, skipping tick")
            continue
        now = time.time()
        if now - dash.get("last_edit_at", 0) < MIN_EDIT_GAP: continue
        await _enqueue_edit(user_id)

async def get_or_create_dashboard(user_id: int, trigger_msg: Message, user_label: str) -> Message:
    dash = user_dashboards.get(user_id)
    if dash:
        dash["user_label"] = user_label
        return dash["msg"]
    msg = await trigger_msg.reply_text(
        "⏳ **Initialising...**",
        reply_markup=dashboard_keyboard(user_id, 0, 1),
    )
    user_dashboards[user_id] = {
        "msg": msg, "flood_until": 0.0, "user_label": user_label,
        "last_text": "", "last_edit_at": 0.0,
        "page": 0,      # ← current visible page (0-indexed)
    }
    asyncio.create_task(dashboard_loop(user_id))
    return msg

async def safe_answer(cq, text: str = "", show_alert: bool = False):
    """Answer a callback query, silently ignoring expired query ID errors."""
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

# ── Callback: Page navigation (NO rate limit — instant switch) ────────────────
@app.on_callback_query(filters.regex(r"^dpage:"))
async def dashboard_page_callback(client, cq: CallbackQuery):
    parts    = cq.data.split(":")      # ["dpage", uid, page]
    user_id  = int(parts[1])
    new_page = int(parts[2])
    dash = user_dashboards.get(user_id)
    if not dash:
        await safe_answer(cq, "⚠️ No active tasks.", show_alert=True); return

    total_pages = get_total_pages(user_id)
    new_page    = max(0, min(new_page, total_pages - 1))

    if new_page == dash.get("page", 0):
        await safe_answer(cq); return     # already on this page

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

# ── Callback: no-op (page label button — tapping shows nothing) ───────────────
@app.on_callback_query(filters.regex(r"^noop$"))
async def noop_callback(client, cq: CallbackQuery):
    await safe_answer(cq)

# ── Download progress poller ──────────────────────────────────────────────────
async def poll_download_progress(task: DownloadTask):
    await asyncio.sleep(2)
    while not task.cancelled:
        try:
            dl = aria2.get_download(task.gid)
            if dl.is_complete: break
            _eta = dl.eta
            task.dl.update({
                "filename":   clean_filename(dl.name if dl.name else "Connecting..."),
                "progress":   dl.progress or 0.0,
                "speed":      dl.download_speed or 0,
                "downloaded": dl.completed_length or 0,
                "total":      dl.total_length or 0,
                "elapsed":    time.time() - task.start_time,
                "eta":        _eta.total_seconds() if _eta and _eta.total_seconds() > 0 else 0,
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
# ── Smart episode name: combines show name from parent folder + episode code ──
def smart_episode_name(file_path: str, base_dir: str) -> str:
    """
    If a file is named just 'S01E01.mkv' inside a folder like
    'ShowName.S01.720p.WEB-DL/', returns 'ShowName.S01E01.mkv'.
    Falls back to original filename for non-episode or already-named files.
    """
    filename = os.path.basename(file_path)
    name, ext = os.path.splitext(filename)

    # Only act when filename is purely an episode code e.g. S01E01
    if not re.match(r'^S\d+E\d+', name, re.IGNORECASE):
        return clean_filename(filename)

    # Get immediate parent folder relative to extract root
    rel    = os.path.relpath(file_path, base_dir).replace("\\", "/")
    parts  = rel.split("/")
    parent = parts[0] if len(parts) > 1 else ""
    if not parent:
        return clean_filename(filename)

    # Extract show name = everything before first quality/season tag in parent
    show_match = re.match(
        r'^(.*?)(?:[._\s](?:S\d{2}|720p|1080p|480p|405p|WEB|AMZN|HDRip|BluRay|DD|AAC|H\.264|x264))',
        parent, re.IGNORECASE
    )
    show_name = (show_match.group(1) if show_match
                 else re.split(r'[._]S\d{2}', parent)[0]).strip("._- ")

    if not show_name:
        return clean_filename(filename)

    # Season from parent folder (S01), episode from filename (E01)
    season_m  = re.search(r'(S\d{2})', parent, re.IGNORECASE)
    episode_m = re.match(r'S\d+(E\d+)', name, re.IGNORECASE)
    season    = season_m.group(1).upper()  if season_m  else ""
    episode   = episode_m.group(1).upper() if episode_m else name.upper()

    result = f"{show_name}.{season}{episode}{ext}" if season else f"{show_name}.{name}{ext}"
    return clean_filename(result)


# ── Upload to Telegram ────────────────────────────────────────────────────────
async def upload_to_telegram(file_path: str, message: Message, caption: str = "", task: DownloadTask = None) -> bool:
    if task:
        task.current_phase = "ul"
    user_id    = message.from_user.id
    as_video   = user_settings.get(user_id, {}).get("as_video", False)
    video_exts = (".mp4", ".mkv", ".avi", ".webm")

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
                dt = now - lt[0]; speed = (current - lu[0]) / dt if dt > 0 else 0
                eta = (total - current) / speed if speed > 0 else 0
                lt[0] = now; lu[0] = current; lr[0] = now
                if task:
                    task.ul.update({"uploaded":current,"total":total,"speed":speed,"elapsed":now-st,"eta":eta})
                    await push_dashboard_update(user_id)

            fc = caption or cn
            if as_video and file_path.lower().endswith(video_exts):
                await message.reply_video(video=file_path, caption=fc, progress=_progress, supports_streaming=True, disable_notification=True)
            else:
                await message.reply_document(document=file_path, caption=fc, progress=_progress, disable_notification=True)

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

            n            = len(files)
            total_bytes  = sum(os.path.getsize(fp) for fp in files)
            uploaded_bytes = 0
            dir_start    = time.time()

            for idx, fp in enumerate(files, 1):
                if task and task.cancelled:
                    return False

                # ── Smart rename: ShowName.S01E01.mkv ──
                smart = smart_episode_name(fp, file_path)
                new_fp = os.path.join(os.path.dirname(fp), smart)
                if fp != new_fp and not os.path.exists(new_fp):
                    os.rename(fp, new_fp); fp = new_fp

                file_sz = os.path.getsize(fp)
                cn      = os.path.basename(fp)
                cap     = f"📄 {cn} [{idx}/{n}]"

                if task:
                    elapsed = time.time() - dir_start
                    spd = uploaded_bytes / elapsed if elapsed > 0 else 0
                    eta = (total_bytes - uploaded_bytes) / spd if spd > 0 else 0
                    task.ul.update({
                        "filename": cn, "uploaded": uploaded_bytes, "total": total_bytes,
                        "speed": spd, "elapsed": elapsed, "eta": eta,
                        "file_index": idx, "total_files": n,
                    })
                    await push_dashboard_update(user_id)

                if as_video and fp.lower().endswith(video_exts):
                    await message.reply_video(video=fp, caption=cap, disable_notification=True)
                else:
                    await message.reply_document(document=fp, caption=cap, disable_notification=True)

                try: os.remove(fp)
                except Exception as e: print(f"Cleanup error (file {idx}/{n}): {e}")

                uploaded_bytes += file_sz

            try: shutil.rmtree(file_path, ignore_errors=True)
            except Exception as e: print(f"Cleanup error (dir): {e}")

            return True

    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}")
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
            try: cdl = aria2.get_download(task.gid)
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
                await message.reply_text(f"❌ **Aria2 Error:** `{cdl.error_message}`")
                cleanup_files(task); await push_dashboard_update(task.user_id); return

        if task.cancelled:
            try: aria2.remove([aria2.get_download(task.gid)], force=True, files=True)
            except Exception: pass
            cleanup_files(task); active_downloads.pop(task.gid, None)
            await push_dashboard_update(task.user_id); return

        # ── STOP CHECK after download loop, before extract/upload ──
        if task.cancelled:
            cleanup_files(task); active_downloads.pop(task.gid, None)
            await push_dashboard_update(task.user_id); return

        try:
            fdl = aria2.get_download(task.gid); fp = os.path.join(DOWNLOAD_DIR, fdl.name)
        except Exception:
            fp = os.path.join(DOWNLOAD_DIR, task.dl["filename"])
        task.file_path = fp

        if extract and fp.endswith((".zip", ".7z", ".tar.gz", ".tgz", ".tar")):
            # ── STOP CHECK before extract ──
            if task.cancelled:
                cleanup_files(task); active_downloads.pop(task.gid, None)
                await push_dashboard_update(task.user_id); return
            ed = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
            os.makedirs(ed, exist_ok=True); task.extract_dir = ed
            us, cap = (ed, "📁 Extracted files") if await extract_archive(fp, ed, task=task) else (fp, "")
        else:
            us, cap = fp, ""

        # ── STOP CHECK before upload ──
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

# ── Bot commands ──────────────────────────────────────────────────────────────
@app.on_message(filters.command(["leech", "l", "ql"]))
async def universal_leech_command(client, message: Message):
    extract = "-e" in message.text.lower()
    user_id = message.from_user.id; user_label = get_user_label(message)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await get_or_create_dashboard(user_id, message, user_label)

    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        if doc.file_name.endswith(".torrent"):
            tp = os.path.join(DOWNLOAD_DIR, f"{message.id}_{doc.file_name}")
            await message.reply_to_message.download(file_name=tp)
            dl = aria2.add_torrent(tp, options=BT_OPTIONS)
            task = DownloadTask(dl.gid, user_id, extract)
            asyncio.create_task(process_task_execution(message, task, dl, extract)); return

    args  = message.text.split()[1:]
    links = [a for a in args if a.startswith("http") or a.startswith("magnet:")]
    if not links:
        await message.reply_text("❌ **Usage:** `/ql <link1> <link2>` or reply to a `.torrent` file.\n❌ `/l <link>` for direct links"); return

    for link in links:
        try:
            opts = BT_OPTIONS if link.startswith("magnet:") else {**BT_OPTIONS, **DIRECT_OPTIONS}
            dl   = aria2.add_uris([link], options=opts)
            task = DownloadTask(dl.gid, user_id, extract)
            asyncio.create_task(process_task_execution(message, task, dl, extract))
        except Exception as e:
            await message.reply_text(f"❌ **Failed to add:** `{str(e)}`")


@app.on_message(filters.document)
async def handle_torrent_document(client, message: Message):
    if not message.document.file_name.endswith(".torrent"): return
    try:
        user_id = message.from_user.id; user_label = get_user_label(message)
        extract = "-e" in (message.caption or "").lower()
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        await get_or_create_dashboard(user_id, message, user_label)
        tp = os.path.join(DOWNLOAD_DIR, f"{message.id}_{message.document.file_name}")
        await message.download(file_name=tp)
        dl = aria2.add_torrent(tp, options=BT_OPTIONS)
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
            aria2.remove([aria2.get_download(found_task.gid)], force=True, files=True)
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
        "**📥 Commands:**\n"
        "• `/ql <link1> <link2>` — Download multiple links at once\n"
        "• `/leech <link>` — Standard download\n"
        "• `/leech <link> -e` — Download & auto-extract archive\n"
        "• **Upload a `.torrent` file** directly to start\n\n"
        "**⚙️ Control:**\n"
        "• `/settings` — Toggle Document / Video upload mode\n"
        "• `/stop <task_id>` — Cancel an active task\n\n"
        "**✨ Features:**\n"
        "✓ Paginated dashboard — 4 tasks per page with ◀ Prev / Next ▶\n"
        "✓ ONE dashboard message per user, auto-refreshes every 15s\n"
        "✓ FloodWait eliminated via serialised edit queue\n"
        "✓ 20 supercharged trackers + 200 max peers\n"
        "✓ Smart filename cleaning", reply_markup=kb)


@app.on_callback_query(filters.regex(r"^close_help$"))
async def close_help_callback(client, cq: CallbackQuery):
    try: await cq.message.delete()
    except Exception: pass


@app.on_message(filters.command(["settings"]))
async def settings_command(client, message: Message):
    uid = message.from_user.id
    av  = user_settings.get(uid, {}).get("as_video", False)
    mt  = "🎬 Video (Playable)" if av else "📄 Document (File)"
    kb  = InlineKeyboardMarkup([[InlineKeyboardButton(f"Toggle: {mt}", callback_data=f"toggle_mode:{uid}")]])
    await message.reply_text("⚙️ **Upload Settings**\n\nChoose how video files (.mp4, .mkv, .webm) are sent.", reply_markup=kb)


@app.on_callback_query(filters.regex(r"^toggle_mode:"))
async def toggle_mode_callback(client, cq: CallbackQuery):
    _, uid_str = cq.data.split(":"); uid = int(uid_str)
    if cq.from_user.id != uid:
        await safe_answer(cq, "❌ These aren't your settings!", show_alert=True); return
    cur = user_settings.get(uid, {}).get("as_video", False)
    user_settings.setdefault(uid, {})["as_video"] = not cur
    mt = "🎬 Video (Playable)" if not cur else "📄 Document (File)"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Toggle: {mt}", callback_data=f"toggle_mode:{uid}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close_help")]
    ])
    await cq.edit_message_reply_markup(reply_markup=kb)
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
    await start_web_server()
    print("🤖 Bot ready — listening for commands...")
    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
