import os
import re
import time
import asyncio
import aria2p
import yt_dlp
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
ytdl_session     = {}   # { message.id: {"url": str, "user_id": int, "message": Message} }


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
    page        = max(0, min(page, total_pages - 1))

    start      = page * TASKS_PER_PAGE
    page_tasks = tasks[start: start + TASKS_PER_PAGE]

    stats  = get_system_stats()
    stats["dl_speed"] = sum(t.dl["speed"] for t in tasks if t.current_phase == "dl")
    stats["ul_speed"] = sum(t.ul["speed"] for t in tasks if t.current_phase == "ul")

    div    = "\n\n"
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
            loop = asyncio.get_event_loop()
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
            with py7zr.SevenZipFile(file_path, mode="r") as arc:
                ms = arc.list(); n = len(ms)
                tu = sum(getattr(m, "uncompressed", 0) or 0 for m in ms)
                done = 0; tick = 0
                class _CB(py7zr.callbacks.ExtractCallback):
                    def __init__(s): s.fi = 0; s.cur_path = ""; s.loop = asyncio.get_event_loop()
                    def report_start_preparation(s): pass
                    def report_start(s, p, b): s.fi += 1; s.cur_path = str(p)
                    def report_update(s, b): pass
                    def report_end(s, p, wrote):
                        nonlocal done, tick; done += wrote; tick += 1
                        if tick % 5 == 0:
                            asyncio.run_coroutine_threadsafe(
                                _update(done, tu or total_size, s.cur_path, s.fi, n), s.loop)
                    def report_postprocess(s): pass
                    def report_warning(s, m): pass
                try:
                    arc.extractall(path=extract_to, callback=_CB())
                    await _update(tu or total_size, tu or total_size, filename, n, n)
                except TypeError:
                    arc.extractall(path=extract_to)
                    await _update(total_size, total_size, filename, 1, 1)
            return True

        elif file_path.endswith((".tar.gz", ".tgz", ".tar")):
            import tarfile
            loop = asyncio.get_event_loop()
            def do_tar():
                with tarfile.open(file_path, "r:*") as tf:
                    ms = tf.getmembers(); n = len(ms); tu = sum(m.size for m in ms); done = 0
                    for i, m in enumerate(ms, 1):
                        tf.extract(m, extract_to); done += m.size
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

            n              = len(files)
            total_bytes    = sum(os.path.getsize(fp) for fp in files)
            uploaded_bytes = 0
            dir_start      = time.time()

            for idx, fp in enumerate(files, 1):
                if task and task.cancelled:
                    return False

                smart  = smart_episode_name(fp, file_path)
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

        if task.cancelled:
            cleanup_files(task); active_downloads.pop(task.gid, None)
            await push_dashboard_update(task.user_id); return

        try:
            fdl = aria2.get_download(task.gid); fp = os.path.join(DOWNLOAD_DIR, fdl.name)
        except Exception:
            fp = os.path.join(DOWNLOAD_DIR, task.dl["filename"])
        task.file_path = fp

        if extract and fp.endswith((".zip", ".7z", ".tar.gz", ".tgz", ".tar")):
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
#  YT-DLP INTEGRATION
# ════════════════════════════════════════════════════════

async def fetch_ytdl_formats(url: str) -> dict:
    """Fetch video info + formats from yt-dlp without downloading."""
    loop = asyncio.get_event_loop()
    ydl_opts = {
        "quiet":              True,
        "nocheckcertificate": True,
        "cookiefile":         "cookies.txt" if os.path.exists("cookies.txt") else None,
    }
    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    return await loop.run_in_executor(executor, _run)


async def download_ytdl(url: str, task: DownloadTask, format_id: str = "bestvideo+bestaudio/best") -> str:
    """
    Download via yt-dlp and push progress into task.dl so the
    existing dashboard renders it correctly.
    """
    loop = asyncio.get_event_loop()
    last_push = [0.0]

    def _hook(d):
        if d["status"] != "downloading":
            return
        total    = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        current  = d.get("downloaded_bytes", 0)
        speed    = d.get("speed") or 0
        eta_s    = d.get("eta") or 0
        filename = clean_filename(os.path.basename(d.get("filename", "video")))
        progress = (current / total * 100) if total > 0 else 0
        elapsed  = time.time() - task.start_time

        task.dl.update({
            "filename":   filename,
            "progress":   progress,
            "speed":      speed,
            "downloaded": current,
            "total":      total,
            "elapsed":    elapsed,
            "eta":        eta_s,
            "peer_line":  "├ **Engine** → YT-DLP\n",
        })
        task.filename  = filename
        task.file_size = total

        now = time.time()
        if now - last_push[0] >= MIN_EDIT_GAP:
            last_push[0] = now
            asyncio.run_coroutine_threadsafe(push_dashboard_update(task.user_id), loop)

    ydl_opts = {
        "format":             format_id,
        "outtmpl":            os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        "quiet":              True,
        "nocheckcertificate": True,
        "noplaylist":         True,
        "progress_hooks":     [_hook],
        "cookiefile":         "cookies.txt" if os.path.exists("cookies.txt") else None,
        # merge video+audio into a single mp4 when possible
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key":            "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }

    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    raw_path = await loop.run_in_executor(executor, _run)

    # yt-dlp may produce .mp4 even if prepare_filename returns .webm
    # resolve the actual file on disk
    base, _ = os.path.splitext(raw_path)
    for ext in (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ""):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate
    return raw_path   # fallback


async def process_ytdl_task(message: Message, task: DownloadTask, url: str, format_id: str):
    """End-to-end pipeline for a yt-dlp task: download → upload → cleanup."""
    try:
        await push_dashboard_update(task.user_id)

        file_path = await download_ytdl(url, task, format_id)

        if task.cancelled:
            cleanup_files(task)
            active_downloads.pop(task.gid, None)
            await push_dashboard_update(task.user_id)
            return

        task.file_path = file_path
        await upload_to_telegram(file_path, message, task=task)
        cleanup_files(task)
        active_downloads.pop(task.gid, None)
        await push_dashboard_update(task.user_id)

    except Exception as e:
        await message.reply_text(f"❌ **YT-DLP Error:** `{str(e)}`")
        cleanup_files(task)
        active_downloads.pop(task.gid, None)
        await push_dashboard_update(task.user_id)


# ── /ytdl command ─────────────────────────────────────────────────────────────
@app.on_message(filters.command(["ytdl", "yt"]))
async def ytdl_command(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Usage:** `/ytdl <YouTube or supported URL>`\n\n"
            "Supported: YouTube, Instagram, Twitter, TikTok, and 1000+ sites."
        )
        return

    url       = message.text.split(None, 1)[1].strip()
    user_id   = message.from_user.id
    user_label = get_user_label(message)

    status_msg = await message.reply_text("🔍 **Fetching available formats...**")

    try:
        info     = await fetch_ytdl_formats(url)
        formats  = info.get("formats", [])
        title    = info.get("title", "Unknown")[:60]
        duration = info.get("duration", 0)
        dur_str  = f"{duration // 60}m {duration % 60}s" if duration else "Unknown"

        # Build quality buttons — deduplicate by height
        buttons  = []
        seen_res = set()
        for f in sorted(formats, key=lambda x: x.get("height") or 0, reverse=True):
            h = f.get("height")
            if h and h not in seen_res and f.get("vcodec", "none") != "none":
                seen_res.add(h)
                buttons.append([InlineKeyboardButton(
                    f"🎬 {h}p  ({f.get('ext','?').upper()})",
                    callback_data=f"yt_vid:{h}:{message.id}"
                )])

        # Audio-only option
        buttons.append([InlineKeyboardButton(
            "🎵 Audio only (best)",
            callback_data=f"yt_aud:best:{message.id}"
        )])
        buttons.append([InlineKeyboardButton("🗑 Cancel", callback_data="close_help")])

        ytdl_session[message.id] = {
            "url":       url,
            "user_id":   user_id,
            "message":   message,
        }

        await status_msg.edit_text(
            f"🎬 **{title}**\n"
            f"⏱ Duration: `{dur_str}`\n\n"
            f"Select the quality to download:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ **Failed to fetch formats:**\n`{str(e)}`")


# ── Quality selection callback ────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^yt_(vid|aud):"))
async def ytdl_quality_callback(client, cq: CallbackQuery):
    parts    = cq.data.split(":")          # ["yt_vid", "720", "12345"]
    mode     = parts[0]                    # "yt_vid" or "yt_aud"
    quality  = parts[1]                    # height string or "best"
    msg_id   = int(parts[2])

    session = ytdl_session.get(msg_id)
    if not session:
        await safe_answer(cq, "❌ Session expired. Please send the link again.", show_alert=True)
        return

    await safe_answer(cq)

    # Build yt-dlp format selector
    if mode == "yt_vid":
        format_id = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        label     = f"{quality}p video"
    else:
        format_id = "bestaudio/best"
        label     = "audio only"

    user_id    = session["user_id"]
    orig_msg   = session["message"]
    user_label = get_user_label(orig_msg)

    await cq.message.edit_text(
        f"⏳ **Starting download ({label})...**\n"
        f"Dashboard will appear shortly."
    )

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await get_or_create_dashboard(user_id, orig_msg, user_label)

    # Use a unique pseudo-GID so it integrates cleanly with active_downloads
    pseudo_gid = f"ytdl_{msg_id}_{int(time.time())}"
    task = DownloadTask(pseudo_gid, user_id)
    task.dl["peer_line"] = "├ **Engine** → YT-DLP\n"
    active_downloads[pseudo_gid] = task

    asyncio.create_task(process_ytdl_task(orig_msg, task, session["url"], format_id))
    ytdl_session.pop(msg_id, None)   # clean up session


# ════════════════════════════════════════════════════════
#  BOT COMMANDS (original, unchanged)
# ════════════════════════════════════════════════════════

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
            # Only call aria2.remove for real aria2 GIDs (not ytdl_ pseudo-GIDs)
            if not found_task.gid.startswith("ytdl_"):
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
        "Download direct links, magnets, torrents, and YouTube/1000+ sites.\n\n"
        "Type /help for all commands.\n\n© Maintained By @im_goutham_josh", reply_markup=kb)


@app.on_message(filters.command(["help"]))
async def help_command(client, message: Message):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Close", callback_data="close_help")]])
    await message.reply_text(
        "**📖 Leech Bot — Help & Commands**\n\n"
        "**📥 Download Commands:**\n"
        "• `/ql <link1> <link2>` — Download multiple direct/magnet links\n"
        "• `/leech <link>` — Standard aria2 download\n"
        "• `/leech <link> -e` — Download & auto-extract archive\n"
        "• **Upload a `.torrent` file** directly to start\n"
        "• `/ytdl <URL>` — Download from YouTube & 1000+ sites\n"
        "• `/yt <URL>` — Shorthand for /ytdl\n\n"
        "**⚙️ Control:**\n"
        "• `/settings` — Toggle Document / Video upload mode\n"
        "• `/stop <task_id>` — Cancel an active task\n\n"
        "**✨ Features:**\n"
        "✓ YT-DLP — quality selector with per-resolution buttons\n"
        "✓ All downloads appear in the same live dashboard\n"
        "✓ Paginated dashboard — 4 tasks per page with ◀ Prev / Next ▶\n"
        "✓ ONE dashboard message per user, auto-refreshes every 15s\n"
        "✓ FloodWait eliminated via serialised edit queue\n"
        "✓ 20 supercharged trackers + 200 max peers\n"
        "✓ Smart filename cleaning\n"
        "✓ cookies.txt support for age-restricted / private videos",
        reply_markup=kb)


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
