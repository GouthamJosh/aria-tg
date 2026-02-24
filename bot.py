import os
import re
import time
import asyncio
import aria2p
import aiohttp
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message
import py7zr
import zipfile
import shutil
from datetime import timedelta
import psutil

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────
API_ID       = os.environ.get("API_ID", '18979569')
API_HASH     = os.environ.get("API_HASH", '45db354387b8122bdf6c1b0beef93743')
BOT_TOKEN    = os.environ.get("BOT_TOKEN", '7531165057:AAHnMXz9LmogLXtpFfZaYL3kFL5YRZ5bOAU')
OWNER_ID     = int(os.environ.get("OWNER_ID", "6108995220"))
DOWNLOAD_DIR = "/tmp/downloads"
ARIA2_HOST   = "http://localhost"
ARIA2_PORT   = 6800
ARIA2_SECRET = os.environ.get("ARIA2_SECRET", "gjxml")

# ── Upload size limits ─────────────────────────────────────────────────────────
OWNER_PREMIUM    = os.environ.get("OWNER_PREMIUM", "false").lower() == "true"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024 if OWNER_PREMIUM else 2 * 1024 * 1024 * 1024
MAX_UPLOAD_LABEL = "4GB" if OWNER_PREMIUM else "2GB"

# ── Koyeb keep-alive ───────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", "8000"))

# Engine labels
ENGINE_DL     = "ARIA2 v1.36.0"
ENGINE_UL     = "Pyro v2.2.18"
ENGINE_EXTRACT = "py7zr / zipfile"

# Initialize clients
app   = Client("leech_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret=ARIA2_SECRET))

active_downloads = {}


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

    def get_elapsed_time(self):
        elapsed = time.time() - self.start_time
        return str(timedelta(seconds=int(elapsed)))


# ─────────────────────────────────────────────────────────────────────────────
#  Filename cleaner  — strips "www.site.com - " prefixes
# ─────────────────────────────────────────────────────────────────────────────
def clean_filename(filename: str) -> str:
    """
    Remove leading site-name junk like:
      www.1TamilMV.earth - The Housemaid...  →  The Housemaid...
      [www.site.com] - Movie...              →  Movie...
      www.site.com_Movie...                  →  Movie...
    """
    # Strip bracketed or plain www.* prefixes followed by a dash separator
    cleaned = re.sub(
        r'^\[?(?:www\.|WWW\.)[^\]\s]+\]?\s*[-–—_]\s*',
        '', filename
    )
    # Also strip if no dash: "www.site.com.Movie.mkv" → "Movie.mkv"  (dot-separated)
    if cleaned == filename:
        cleaned = re.sub(
            r'^(?:www\.|WWW\.)[a-zA-Z0-9.-]+?\.',
            '', filename
        )
    return cleaned.strip() if cleaned.strip() else filename


# ─────────────────────────────────────────────────────────────────────────────
#  Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────
def create_progress_bar(percentage: float) -> str:
    if percentage >= 100:
        return "[●●●●●●●●●●] 100%"
    filled = int(percentage / 10)
    bar = "●" * filled + "○" * (10 - filled)
    return f"[{bar}] {percentage:.1f}%"

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
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.2f}MB"

def format_time(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    hours   = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    if hours > 0:
        return f"{hours}h{minutes}m{secs}s"
    elif minutes > 0:
        return f"{minutes}m{secs}s"
    return f"{secs}s"

def get_system_stats() -> dict:
    cpu_percent = psutil.cpu_percent(interval=0.1)
    ram         = psutil.virtual_memory()
    uptime      = time.time() - psutil.boot_time()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    disk        = psutil.disk_usage(DOWNLOAD_DIR)
    disk_free   = disk.free  / (1024 ** 3)
    disk_free_pct = 100.0 - disk.percent          # free %
    return {
        'cpu':          cpu_percent,
        'ram_percent':  ram.percent,
        'uptime':       format_time(uptime),
        'disk_free':    disk_free,
        'disk_free_pct': disk_free_pct,
    }

def bot_stats_block(stats: dict) -> str:
    """© Bot Stats footer block — matches reference image format."""
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
            elif os.path.isdir(task.file_path):
                shutil.rmtree(task.file_path, ignore_errors=True)
        if task.extract_dir and os.path.exists(task.extract_dir):
            shutil.rmtree(task.extract_dir, ignore_errors=True)
    except Exception as e:
        print(f"⚠ Cleanup error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Download progress loop  ── NEW UI matching reference Image 1
# ─────────────────────────────────────────────────────────────────────────────
async def update_progress(task: DownloadTask, message):
    try:
        await asyncio.sleep(2)
        update_count = 0
        while not task.cancelled:
            try:
                download   = aria2.get_download(task.gid)
                if download.is_complete:
                    break

                progress   = download.progress or 0.0
                speed      = download.download_speed or 0
                total_size = download.total_length or 0
                downloaded = download.completed_length or 0
                _eta_td    = download.eta
                eta        = _eta_td.total_seconds() if _eta_td and _eta_td.total_seconds() > 0 else 0
                elapsed    = time.time() - task.start_time
                raw_name   = download.name if download.name else "Connecting…"
                filename   = clean_filename(raw_name)
                task.filename  = filename
                task.file_size = total_size

                # Seeders / Leechers (torrent) or Connections (HTTP)
                try:
                    seeders   = getattr(download, 'num_seeders', None)
                    leechers  = getattr(download, 'connections', None)
                    if seeders is not None and seeders > 0:
                        peer_line = f"├ **Seeders** → {seeders} | **Leechers** → {leechers or 0}\n"
                    else:
                        conns = download.connections or 0
                        peer_line = f"├ **Connections** → {conns}\n"
                except Exception:
                    peer_line = ""

                size_text = (
                    f"{format_size(downloaded)} of {format_size(total_size)}"
                    if total_size > 0 else "Fetching file info…"
                )

                # Time line: "elapsed of total_est ( eta )"
                total_est = elapsed + eta
                time_line = f"{format_time(elapsed)} of {format_time(total_est)} ( {format_time(eta)} )"

                stats      = get_system_stats()
                user_label = get_user_label(message, task)

                status_text = (
                    f"**{filename}**\n\n"
                    f"**Task By** {user_label} [Link]\n"
                    f"├ {create_progress_bar(progress)}\n"
                    f"├ **Processed** → {size_text}\n"
                    f"├ **Status** → Download\n"
                    f"├ **Speed** → {format_speed(speed)}\n"
                    f"├ **Time** → {time_line}\n"
                    f"{peer_line}"
                    f"├ **Engine** → {ENGINE_DL}\n"
                    f"├ **In Mode** → #ARIA2\n"
                    f"├ **Out Mode** → #Leech\n"
                    f"└ **Stop** → /stop_{task.gid[:8]}\n\n"
                    f"{bot_stats_block(stats)}"
                )

                if update_count % 3 == 0:
                    try:
                        await message.edit_text(status_text)
                    except Exception:
                        pass

            except Exception as iter_err:
                print(f"Progress iteration error: {iter_err}")

            update_count += 1
            await asyncio.sleep(1)

    except Exception as e:
        print(f"Progress update error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Extraction with live progress UI  ── NEW UI
# ─────────────────────────────────────────────────────────────────────────────
async def extract_archive(file_path, extract_to, status_msg=None, task=None):
    try:
        raw_name   = os.path.basename(file_path)
        filename   = clean_filename(raw_name)
        total_size = os.path.getsize(file_path)
        start_time = time.time()

        async def _render(extracted_bytes, total_bytes, current_file, file_index, total_files):
            pct       = min((extracted_bytes / total_bytes) * 100, 100) if total_bytes > 0 else 0
            elapsed   = time.time() - start_time
            speed     = extracted_bytes / elapsed if elapsed > 0 else 0
            remaining = (total_bytes - extracted_bytes) / speed if speed > 0 else 0
            stats     = get_system_stats()
            cur_clean = clean_filename(os.path.basename(current_file))
            user_label = get_user_label(status_msg, task) if task else "Unknown"

            text = (
                f"**{filename}**\n\n"
                f"**Task By** {user_label} [Link]\n"
                f"├ {create_progress_bar(pct)}\n"
                f"├ **Processed** → {format_size(extracted_bytes)} of {format_size(total_bytes)}\n"
                f"├ **Status** → Extracting\n"
                f"├ **Speed** → {format_speed(speed)}\n"
                f"├ **Time** → {format_time(elapsed)} ( {format_time(remaining)} )\n"
                f"├ **File** → `{cur_clean}` [{file_index}/{total_files}]\n"
                f"├ **Engine** → {ENGINE_EXTRACT}\n"
                f"├ **In Mode** → #Extract\n"
                f"├ **Out Mode** → #Leech\n"
                f"└ **Archive Size** → {format_size(total_size)}\n\n"
                f"{bot_stats_block(stats)}"
            )
            try:
                if status_msg:
                    await status_msg.edit_text(text)
            except Exception:
                pass

        # ── ZIP ───────────────────────────────────────────────────────────────
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zf:
                members             = zf.infolist()
                total_files         = len(members)
                uncompressed_total  = sum(m.file_size for m in members)
                extracted_bytes     = 0
                update_tick         = 0
                for idx, member in enumerate(members, start=1):
                    zf.extract(member, extract_to)
                    extracted_bytes += member.file_size
                    update_tick += 1
                    if update_tick % 5 == 0 or idx == total_files:
                        await _render(extracted_bytes, uncompressed_total, member.filename, idx, total_files)
                    await asyncio.sleep(0)

        # ── 7z ───────────────────────────────────────────────────────────────
        elif file_path.endswith('.7z'):
            with py7zr.SevenZipFile(file_path, mode='r') as archive:
                members     = archive.list()
                total_files = len(members)
                total_unc   = sum(getattr(m, 'uncompressed', 0) or 0 for m in members)
                extracted_bytes = 0
                update_tick = 0

                class _CB(py7zr.callbacks.ExtractCallback):
                    def __init__(s): s.file_index = 0
                    def report_start_preparation(s): pass
                    def report_start(s, p, b): s.file_index += 1
                    def report_update(s, b): pass
                    def report_end(s, p, wrote_bytes):
                        nonlocal extracted_bytes, update_tick
                        extracted_bytes += wrote_bytes
                        update_tick += 1
                    def report_postprocess(s): pass
                    def report_warning(s, m): pass

                try:
                    archive.extractall(path=extract_to, callback=_CB())
                    await _render(
                        total_unc or total_size, total_unc or total_size,
                        filename, total_files, total_files
                    )
                except TypeError:
                    archive.extractall(path=extract_to)
                    await _render(total_size, total_size, filename, 1, 1)

        # ── TAR ───────────────────────────────────────────────────────────────
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            import tarfile
            with tarfile.open(file_path, 'r:*') as tf:
                members     = tf.getmembers()
                total_files = len(members)
                total_unc   = sum(m.size for m in members)
                extracted_bytes = 0
                update_tick = 0
                for idx, member in enumerate(members, start=1):
                    tf.extract(member, extract_to)
                    extracted_bytes += member.size
                    update_tick += 1
                    if update_tick % 5 == 0 or idx == total_files:
                        await _render(extracted_bytes, total_unc, member.name, idx, total_files)
                    await asyncio.sleep(0)
        else:
            return False

        return True

    except Exception as e:
        print(f"Extraction error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Upload with live progress UI  ── NEW UI matching reference Image 2
# ─────────────────────────────────────────────────────────────────────────────
async def upload_to_telegram(file_path, message, caption="", status_msg=None, task=None):

    async def _render_upload(raw_filename, uploaded, total, file_index, total_files, speed, elapsed, eta, gid_short=""):
        filename = clean_filename(raw_filename)
        pct      = min((uploaded / total) * 100, 100) if total > 0 else 0
        stats    = get_system_stats()
        user_label = get_user_label(message, task) if task else "Unknown"

        file_line  = f"├ **Files** → {file_index}/{total_files}\n" if total_files > 1 else ""
        time_line  = f"of {format_time(elapsed + eta)} ( {format_time(elapsed)} )"

        stop_id    = gid_short or (task.gid[:8] if task else "unknown")

        text = (
            f"**{filename}**\n\n"
            f"**Task By** {user_label} [Link]\n"
            f"{file_line}"
            f"├ {create_progress_bar(pct)}\n"
            f"├ **Processed** → {format_size(uploaded)} of {format_size(total)}\n"
            f"├ **Status** → Upload\n"
            f"├ **Speed** → {format_speed(speed)}\n"
            f"├ **Time** → {time_line}\n"
            f"├ **Engine** → {ENGINE_UL}\n"
            f"├ **In Mode** → #Aria2\n"
            f"├ **Out Mode** → #Leech\n"
            f"└ **Stop** → /stop_{stop_id}\n\n"
            f"{bot_stats_block(stats)}"
        )
        try:
            if status_msg:
                await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        gid_short = task.gid[:8] if task else "unknown"

        # ── Single file ────────────────────────────────────────────────────────
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > MAX_UPLOAD_BYTES:
                await message.reply_text(f"❌ File too large for Telegram (>{MAX_UPLOAD_LABEL})")
                return False

            raw_name         = os.path.basename(file_path)
            start_time       = time.time()
            last_update_time = [time.time()]
            last_uploaded    = [0]

            async def _progress(current, total):
                now     = time.time()
                elapsed = now - start_time
                dt      = now - last_update_time[0]
                if dt >= 2:
                    speed = (current - last_uploaded[0]) / dt if dt > 0 else 0
                    eta   = (total - current) / speed if speed > 0 else 0
                    last_update_time[0] = now
                    last_uploaded[0]    = current
                    await _render_upload(raw_name, current, total, 1, 1, speed, elapsed, eta, gid_short)

            await _render_upload(raw_name, 0, file_size, 1, 1, 0, 0, 0, gid_short)
            clean_cap = clean_filename(raw_name)
            await message.reply_document(
                document=file_path,
                caption=caption or clean_cap,
                progress=_progress
            )
            elapsed = time.time() - start_time
            await _render_upload(raw_name, file_size, file_size, 1, 1, 0, elapsed, 0, gid_short)
            return True

        # ── Directory (multi-file) ─────────────────────────────────────────────
        elif os.path.isdir(file_path):
            files = []
            for root, dirs, filenames in os.walk(file_path):
                for fname in filenames:
                    fpath = os.path.join(root, fname)
                    if os.path.getsize(fpath) <= MAX_UPLOAD_BYTES:
                        files.append(fpath)

            total_files = len(files)
            if total_files == 0:
                await message.reply_text("❌ No uploadable files found.")
                return False

            file_progress = {i: (0, os.path.getsize(fp)) for i, fp in enumerate(files, start=1)}
            last_render   = [0.0]
            start_time    = time.time()

            async def _render_multi():
                now = time.time()
                if now - last_render[0] < 2:
                    return
                last_render[0]  = now
                elapsed         = now - start_time
                stats           = get_system_stats()
                user_label      = get_user_label(message, task) if task else "Unknown"
                total_uploaded  = sum(u for u, _ in file_progress.values())
                total_bytes     = sum(t for _, t in file_progress.values())
                overall_pct     = min((total_uploaded / total_bytes) * 100, 100) if total_bytes > 0 else 0

                lines = [
                    f"**Task By** {user_label} [Link]\n",
                    f"├ **Overall** {create_progress_bar(overall_pct)}\n",
                    f"├ **Processed** → {format_size(total_uploaded)} of {format_size(total_bytes)}\n",
                    f"├ **Status** → Upload ({total_files} files)\n",
                    f"├ **Time** → {format_time(elapsed)}\n",
                    f"├ **Engine** → {ENGINE_UL}\n",
                    f"├ **In Mode** → #Aria2\n",
                    f"├ **Out Mode** → #Leech\n",
                ]
                for i, fp in enumerate(files, start=1):
                    fname         = clean_filename(os.path.basename(fp))
                    uploaded, tot = file_progress[i]
                    pct           = min((uploaded / tot) * 100, 100) if tot > 0 else 0
                    lines.append(
                        f"├ `{fname}` {format_size(uploaded)}/{format_size(tot)} "
                        f"{create_progress_bar(pct)}\n"
                    )
                lines.append(
                    f"└ **Stop** → /stop_{gid_short}\n\n"
                    f"{bot_stats_block(stats)}"
                )
                try:
                    if status_msg:
                        await status_msg.edit_text("".join(lines))
                except Exception:
                    pass

            async def _upload_one(file_index, fpath):
                file_size = os.path.getsize(fpath)
                raw_name  = os.path.basename(fpath)
                clean_cap = clean_filename(raw_name)

                async def _progress(current, total):
                    file_progress[file_index] = (current, total)
                    await _render_multi()

                file_progress[file_index] = (0, file_size)
                await message.reply_document(
                    document=fpath,
                    caption=f"📄 {clean_cap}  [{file_index}/{total_files}]" + (f"\n{caption}" if caption else ""),
                    progress=_progress
                )
                file_progress[file_index] = (file_size, file_size)

            await _render_multi()
            await asyncio.gather(*[_upload_one(i, fp) for i, fp in enumerate(files, start=1)])
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
                await status_msg.edit_text("❌ **Download cancelled**\n🧹 **Cleaning up...**")
                try:
                    aria2.remove([download], force=True, files=True)
                except Exception:
                    pass
                cleanup_files(task)
                active_downloads.pop(gid, None)
                await status_msg.edit_text("❌ **Download cancelled**\n✅ **Files cleaned up!**")
                return

            await status_msg.edit_text("✅ **Download completed!**\n📤 **Starting upload...**")
            download.update()
            file_path      = os.path.join(DOWNLOAD_DIR, download.name)
            task.file_path = file_path

            if extract and file_path.endswith(('.zip', '.7z', '.tar.gz', '.tgz', '.tar')):
                extract_dir      = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir
                await status_msg.edit_text("📦 **Starting extraction...**")
                if await extract_archive(file_path, extract_dir, status_msg=status_msg, task=task):
                    await status_msg.edit_text("✅ **Extraction done!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(
                        extract_dir, message,
                        caption="📁 Extracted files",
                        status_msg=status_msg, task=task
                    )
                else:
                    await status_msg.edit_text("❌ **Extraction failed!** Uploading original...")
                    await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)
            else:
                await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)

            await status_msg.edit_text("✅ **Upload completed!**\n🧹 **Cleaning up files...**")
            cleanup_files(task)
            await status_msg.edit_text("✅ **Task completed successfully!**")
            active_downloads.pop(gid, None)

        except Exception as e:
            await status_msg.edit_text(f"❌ **Error:** `{str(e)}`\n🧹 **Cleaning up...**")
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

        found_task = None
        found_gid  = None
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
#  Koyeb keep-alive web server
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
    print(f"🌐 Koyeb keep-alive on port {PORT}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    await app.start()
    print("✅ Bot started successfully")

    await start_web_server()
    print("✅ Web server started")

    print("🤖 Bot ready — listening for commands...")
    await idle()

    print("🛑 Stopping bot...")
    await app.stop()
    print("✅ Bot stopped cleanly")


if __name__ == "__main__":
    app.run(main())
