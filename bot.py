import os
import time
import asyncio
import aria2p
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from qbittorrentapi import Client as qBitClient
import py7zr
import zipfile
import shutil
import humanize
from datetime import timedelta
import psutil

# Configuration
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/downloads"
ARIA2_HOST = "http://localhost"
ARIA2_PORT = 6800
QBIT_HOST = "localhost"
QBIT_PORT = 8080

# Initialize Pyrogram client
app = Client("leech_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize Aria2
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret=""))

# Active downloads tracking
active_downloads = {}


class DownloadTask:
    def __init__(self, gid, user_id, message_id, extract=False, engine="aria2", username=None):
        self.gid = gid
        self.user_id = user_id
        self.message_id = message_id
        self.extract = extract
        self.engine = engine
        self.cancelled = False
        self.start_time = time.time()
        self.file_path = None
        self.extract_dir = None
        self.filename = ""
        self.file_size = 0
        self.username = username or f"User"

    def get_elapsed_time(self):
        elapsed = time.time() - self.start_time
        return str(timedelta(seconds=int(elapsed)))


def create_progress_bar(percentage):
    """Create a visual progress bar with circles"""
    if percentage >= 100:
        return "[●●●●●●●●●●] 100%"
    filled = int(percentage / 10)
    bar = "●" * filled + "○" * (10 - filled)
    return f"[{bar}] {percentage:.1f}%"


def format_speed(speed):
    """Format speed in MB/s"""
    mb_speed = speed / (1024 * 1024)
    return f"{mb_speed:.2f}MB/s" if mb_speed > 0 else "0 B/s"


def format_size(size_bytes):
    """Format size in GB or MB"""
    gb = size_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f}GB"
    else:
        mb = size_bytes / (1024 ** 2)
        return f"{mb:.2f}MB"


def format_time(seconds):
    """Format time in hours:minutes:seconds or minutes:seconds"""
    if seconds <= 0:
        return "0s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}h{minutes}m{secs}s"
    elif minutes > 0:
        return f"{minutes}m{secs}s"
    else:
        return f"{secs}s"


def get_system_stats():
    """Get CPU and RAM stats"""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    ram_used_gb = ram.used / (1024 ** 3)
    ram_percent = ram.percent
    uptime = time.time() - psutil.boot_time()

    return {
        'cpu': cpu_percent,
        'ram_used': ram_used_gb,
        'ram_percent': ram_percent,
        'uptime': format_time(uptime)
    }


def cleanup_files(task):
    """Clean up all files associated with a download task"""
    try:
        if task.file_path and os.path.exists(task.file_path):
            if os.path.isfile(task.file_path):
                os.remove(task.file_path)
                print(f"✓ Cleaned up file: {task.file_path}")
            elif os.path.isdir(task.file_path):
                shutil.rmtree(task.file_path, ignore_errors=True)
                print(f"✓ Cleaned up directory: {task.file_path}")

        if task.extract_dir and os.path.exists(task.extract_dir):
            shutil.rmtree(task.extract_dir, ignore_errors=True)
            print(f"✓ Cleaned up extract dir: {task.extract_dir}")

    except Exception as e:
        print(f"⚠ Cleanup error: {e}")


async def update_progress(task, message):
    """Update download progress in detailed UI format"""
    try:
        update_count = 0
        while not task.cancelled:
            if task.engine == "aria2":
                download = aria2.get_download(task.gid)

                if download.is_complete:
                    break

                progress = download.progress
                speed = download.download_speed
                total_size = download.total_length
                downloaded = download.completed_length
                eta = download.eta.total_seconds() if download.eta else 0
                filename = download.name

            elif task.engine == "qbit":
                qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                qb.auth_log_in()
                torrent_info = qb.torrents_info(torrent_hashes=task.gid)[0]

                if torrent_info.state in ["uploading", "pausedUP", "stalledUP"]:
                    break

                progress = torrent_info.progress * 100
                speed = torrent_info.dlspeed
                total_size = torrent_info.size
                downloaded = torrent_info.downloaded
                eta = torrent_info.eta
                filename = torrent_info.name

            # Store task info
            task.filename = filename
            task.file_size = total_size

            # Create detailed status message
            progress_bar = create_progress_bar(progress)
            elapsed = task.get_elapsed_time()
            stats = get_system_stats()

            try:
                user_mention = f"@{message.chat.username}" if message.chat.username else f"User #{task.user_id}"
            except:
                user_mention = f"User #{task.user_id}"

            status_text = (
                f"**Task By** {user_mention} ( #{task.user_id} ) [Link]\n"
                f"├ {progress_bar}\n"
                f"├ **File** → `{filename}`\n"
                f"├ **Processed** → {format_size(downloaded)} of {format_size(total_size)}\n"
                f"├ **Status** → Download\n"
                f"├ **Speed** → {format_speed(speed)}\n"
                f"├ **Time** → {elapsed} ( {format_time(eta)} )\n"
                f"├ **Engine** → {task.engine.upper()} v{get_engine_version(task.engine)}\n"
                f"├ **In Mode** → #{task.engine}\n"
                f"├ **Out Mode** → #Leech (Zip)\n"
                f"└ **Stop** → /stop_{task.gid[:8]}\n\n"
                f"**📊 Bot Stats**\n"
                f"├ **CPU** → {stats['cpu']:.2f}% | **RAM** → {stats['ram_used']:.2f}GB [{stats['ram_percent']:.1f}%]\n"
                f"└ **UP** → {stats['uptime']}"
            )

            try:
                if update_count % 3 == 0:
                    await message.edit_text(status_text)
            except Exception:
                pass

            update_count += 1
            await asyncio.sleep(1)

    except Exception as e:
        print(f"Progress update error: {e}")


def get_engine_version(engine):
    """Get engine version"""
    if engine == "aria2":
        return "v2.2.18"
    elif engine == "qbit":
        return "v1.37.0"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
#  EXTRACTION WITH LIVE PROGRESS UI
# ─────────────────────────────────────────────────────────────────────────────

async def extract_archive(file_path, extract_to, status_msg=None, task=None):
    """
    Extract archive files with a live progress UI in Telegram.
    Shows: filename, file number, processed size, total size, elapsed time,
    estimated time, and a progress bar – mirroring the download UI.
    """
    try:
        filename   = os.path.basename(file_path)
        total_size = os.path.getsize(file_path)
        start_time = time.time()

        # ── helpers shared across formats ────────────────────────────────────
        async def _render_extract_status(extracted_bytes, total_bytes,
                                         current_file, file_index, total_files):
            """Build and push the extraction status message."""
            if total_bytes > 0:
                pct = min((extracted_bytes / total_bytes) * 100, 100)
            else:
                pct = 0

            elapsed     = time.time() - start_time
            speed       = extracted_bytes / elapsed if elapsed > 0 else 0
            remaining   = (total_bytes - extracted_bytes) / speed if speed > 0 else 0

            prog_bar    = create_progress_bar(pct)
            stats       = get_system_stats()
            user_label  = f"User #{task.user_id}" if task else "Unknown"

            try:
                if status_msg and status_msg.chat.username:
                    user_label = f"@{status_msg.chat.username} ( #{task.user_id} )"
            except Exception:
                pass

            file_label  = f"{file_index}/{total_files}" if total_files > 1 else "1/1"

            text = (
                f"**Task By** {user_label} [Link]\n"
                f"├ {prog_bar}\n"
                f"├ **File** → `{current_file}`\n"
                f"├ **Files** → {file_label}\n"
                f"├ **Processed** → {format_size(extracted_bytes)} of {format_size(total_bytes)}\n"
                f"├ **Status** → Extracting\n"
                f"├ **Speed** → {format_speed(speed)}\n"
                f"├ **Time** → {format_time(elapsed)} ( {format_time(remaining)} )\n"
                f"├ **Archive** → `{filename}`\n"
                f"└ **Archive Size** → {format_size(total_size)}\n\n"
                f"**📊 Bot Stats**\n"
                f"├ **CPU** → {stats['cpu']:.2f}% | **RAM** → {stats['ram_used']:.2f}GB [{stats['ram_percent']:.1f}%]\n"
                f"└ **UP** → {stats['uptime']}"
            )
            try:
                if status_msg:
                    await status_msg.edit_text(text)
            except Exception:
                pass

        # ── ZIP ───────────────────────────────────────────────────────────────
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zf:
                members     = zf.infolist()
                total_files = len(members)
                # Use uncompressed sizes for a more accurate "processed" counter
                uncompressed_total = sum(m.file_size for m in members)
                extracted_bytes    = 0
                update_tick        = 0

                for idx, member in enumerate(members, start=1):
                    zf.extract(member, extract_to)
                    extracted_bytes += member.file_size
                    update_tick     += 1

                    if update_tick % 5 == 0 or idx == total_files:
                        await _render_extract_status(
                            extracted_bytes, uncompressed_total,
                            member.filename, idx, total_files
                        )
                    await asyncio.sleep(0)   # yield to event loop

        # ── 7-Zip ─────────────────────────────────────────────────────────────
        elif file_path.endswith('.7z'):
            with py7zr.SevenZipFile(file_path, mode='r') as archive:
                members     = archive.list()
                total_files = len(members)
                total_unc   = sum(getattr(m, 'uncompressed', 0) or 0 for m in members)
                extracted_bytes = 0
                update_tick     = 0

                # py7zr doesn't have per-file callbacks easily, so we use a
                # custom callback via the `callback` parameter (py7zr ≥ 0.15)
                class _ProgressCallback(py7zr.callbacks.ExtractCallback):
                    def __init__(self_cb):
                        self_cb.file_index = 0

                    def report_start_preparation(self_cb): pass
                    def report_start(self_cb, processing_file_path, processing_bytes):
                        self_cb.file_index += 1

                    def report_update(self_cb, decompressed_bytes):
                        pass   # handled in report_end

                    def report_end(self_cb, processing_file_path, wrote_bytes):
                        nonlocal extracted_bytes, update_tick
                        extracted_bytes += wrote_bytes
                        update_tick     += 1

                    def report_postprocess(self_cb): pass
                    def report_warning(self_cb, message): pass

                try:
                    cb = _ProgressCallback()
                    archive.extractall(path=extract_to, callback=cb)
                    # Final render with 100 %
                    await _render_extract_status(
                        total_unc or total_size, total_unc or total_size,
                        filename, total_files, total_files
                    )
                except TypeError:
                    # Older py7zr – fall back to plain extract + single update
                    archive.extractall(path=extract_to)
                    await _render_extract_status(
                        total_size, total_size, filename, 1, 1
                    )

        # ── TAR / TAR.GZ / TGZ ───────────────────────────────────────────────
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            import tarfile
            with tarfile.open(file_path, 'r:*') as tf:
                members     = tf.getmembers()
                total_files = len(members)
                total_unc   = sum(m.size for m in members)
                extracted_bytes = 0
                update_tick     = 0

                for idx, member in enumerate(members, start=1):
                    tf.extract(member, extract_to)
                    extracted_bytes += member.size
                    update_tick     += 1

                    if update_tick % 5 == 0 or idx == total_files:
                        await _render_extract_status(
                            extracted_bytes, total_unc,
                            member.name, idx, total_files
                        )
                    await asyncio.sleep(0)
        else:
            return False

        return True

    except Exception as e:
        print(f"Extraction error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  UPLOAD WITH LIVE PROGRESS UI
# ─────────────────────────────────────────────────────────────────────────────

async def upload_to_telegram(file_path, message, caption="", status_msg=None, task=None):
    """
    Upload file / folder to Telegram with a live progress UI that mirrors
    the download UI: filename, file size, uploaded / total, speed, ETA,
    elapsed time, CPU/RAM stats.
    """

    async def _render_upload_status(filename, uploaded, total,
                                     file_index, total_files,
                                     speed, elapsed, eta):
        """Build and push the upload status message."""
        if total > 0:
            pct = min((uploaded / total) * 100, 100)
        else:
            pct = 0

        prog_bar   = create_progress_bar(pct)
        stats      = get_system_stats()
        user_label = f"User #{task.user_id}" if task else "Unknown"

        try:
            if status_msg and status_msg.chat.username:
                user_label = f"@{status_msg.chat.username} ( #{task.user_id} )"
        except Exception:
            pass

        file_label = f"{file_index}/{total_files}" if total_files > 1 else ""
        file_line  = f"├ **Files** → {file_label}\n" if total_files > 1 else ""

        text = (
            f"**Task By** {user_label} [Link]\n"
            f"├ {prog_bar}\n"
            f"├ **File** → `{filename}`\n"
            f"{file_line}"
            f"├ **Processed** → {format_size(uploaded)} of {format_size(total)}\n"
            f"├ **Status** → Uploading\n"
            f"├ **Speed** → {format_speed(speed)}\n"
            f"├ **Time** → {format_time(elapsed)} ( {format_time(eta)} )\n"
            f"├ **Out Mode** → #Leech\n"
            f"└ **File Size** → {format_size(total)}\n\n"
            f"**📊 Bot Stats**\n"
            f"├ **CPU** → {stats['cpu']:.2f}% | **RAM** → {stats['ram_used']:.2f}GB [{stats['ram_percent']:.1f}%]\n"
            f"└ **UP** → {stats['uptime']}"
        )
        try:
            if status_msg:
                await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        if os.path.isfile(file_path):
            # ── Single file upload ────────────────────────────────────────────
            file_size = os.path.getsize(file_path)
            if file_size > 2000 * 1024 * 1024:
                await message.reply_text("❌ File too large for Telegram (>2GB)")
                return False

            filename   = os.path.basename(file_path)
            start_time = time.time()
            last_update_time   = [time.time()]
            last_uploaded_ref  = [0]

            async def _progress(current, total):
                now     = time.time()
                elapsed = now - start_time
                dt      = now - last_update_time[0]

                if dt >= 2:                    # throttle to every 2 s
                    speed   = (current - last_uploaded_ref[0]) / dt if dt > 0 else 0
                    eta     = (total - current) / speed if speed > 0 else 0
                    last_update_time[0]  = now
                    last_uploaded_ref[0] = current
                    await _render_upload_status(
                        filename, current, total, 1, 1, speed, elapsed, eta
                    )

            # Show initial status
            await _render_upload_status(filename, 0, file_size, 1, 1, 0, 0, 0)

            await message.reply_document(
                document=file_path,
                caption=caption or filename,
                progress=_progress
            )
            # Final 100 % frame
            elapsed = time.time() - start_time
            await _render_upload_status(filename, file_size, file_size, 1, 1, 0, elapsed, 0)
            return True

        elif os.path.isdir(file_path):
            # ── Multi-file (folder) upload ────────────────────────────────────
            files = []
            for root, dirs, filenames in os.walk(file_path):
                for fname in filenames:
                    fpath = os.path.join(root, fname)
                    if os.path.getsize(fpath) <= 2000 * 1024 * 1024:
                        files.append(fpath)

            total_files = len(files)
            if total_files == 0:
                await message.reply_text("❌ No uploadable files found.")
                return False

            for file_index, fpath in enumerate(files, start=1):
                file_size  = os.path.getsize(fpath)
                filename   = os.path.basename(fpath)
                start_time = time.time()
                last_update_time  = [time.time()]
                last_uploaded_ref = [0]

                async def _progress_multi(current, total,
                                           _fi=file_index, _fn=filename,
                                           _fs=file_size, _st=start_time):
                    now     = time.time()
                    elapsed = now - _st
                    dt      = now - last_update_time[0]

                    if dt >= 2:
                        speed  = (current - last_uploaded_ref[0]) / dt if dt > 0 else 0
                        eta    = (total - current) / speed if speed > 0 else 0
                        last_update_time[0]  = now
                        last_uploaded_ref[0] = current
                        await _render_upload_status(
                            _fn, current, total, _fi, total_files, speed, elapsed, eta
                        )

                # Initial frame for this file
                await _render_upload_status(filename, 0, file_size, file_index, total_files, 0, 0, 0)

                await message.reply_document(
                    document=fpath,
                    caption=f"📄 {filename}  [{file_index}/{total_files}]"
                            + (f"\n{caption}" if caption else ""),
                    progress=_progress_multi
                )

                elapsed = time.time() - start_time
                await _render_upload_status(filename, file_size, file_size,
                                             file_index, total_files, 0, elapsed, 0)
                await asyncio.sleep(1)   # flood guard

            return True

    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  BOT COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    help_text = (
        "**🤖 Leech Bot - Help**\n\n"
        "**📥 Download Commands:**\n"
        "• `/leech <link>` - Download direct link\n"
        "• `/l <link>` - Short for /leech\n"
        "• `/leech <link> -e` - Download & extract\n\n"
        "**🌪️ Torrent Commands:**\n"
        "• `/qbleech <magnet/torrent>` - Download via qBittorrent\n"
        "• `/qb <link> -e` - Download torrent & extract\n\n"
        "**✨ Features:**\n"
        "✓ Direct links (HTTP/HTTPS/FTP)\n"
        "✓ Torrent files & magnet links\n"
        "✓ Auto extraction (.zip, .7z, .tar.gz)\n"
        "✓ Detailed progress tracking (Download / Extract / Upload)\n"
        "✓ Speed, ETA, elapsed time on every phase\n"
        "✓ CPU/RAM monitoring\n"
        "✓ Auto cleanup after upload\n\n"
        "**📖 Examples:**\n"
        "`/leech https://example.com/file.zip`\n"
        "`/l https://example.com/archive.7z -e`\n"
        "`/qbleech magnet:?xt=urn:btih:...`"
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

            task = DownloadTask(gid, message.from_user.id, status_msg.id, extract, "aria2")
            active_downloads[gid] = task

            asyncio.create_task(update_progress(task, status_msg))

            while not download.is_complete and not task.cancelled:
                await asyncio.sleep(1)
                download.update()

            if task.cancelled:
                await status_msg.edit_text("❌ **Download cancelled**\n🧹 **Cleaning up files...**")
                try:
                    aria2.remove([download], force=True, files=True)
                except Exception:
                    pass
                cleanup_files(task)
                if gid in active_downloads:
                    del active_downloads[gid]
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

                # ── Extraction with live UI ───────────────────────────────────
                await status_msg.edit_text("📦 **Starting extraction...**")
                if await extract_archive(file_path, extract_dir,
                                         status_msg=status_msg, task=task):
                    await status_msg.edit_text("✅ **Extraction done!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message,
                                             caption="📁 Extracted files",
                                             status_msg=status_msg, task=task)
                else:
                    await status_msg.edit_text("❌ **Extraction failed!** Uploading original...")
                    await upload_to_telegram(file_path, message,
                                             status_msg=status_msg, task=task)
            else:
                # ── Upload with live UI ───────────────────────────────────────
                await upload_to_telegram(file_path, message,
                                         status_msg=status_msg, task=task)

            await status_msg.edit_text("✅ **Upload completed!**\n🧹 **Cleaning up files...**")
            cleanup_files(task)
            await status_msg.edit_text("✅ **Task completed successfully!**")

            if gid in active_downloads:
                del active_downloads[gid]

        except Exception as e:
            await status_msg.edit_text(f"❌ **Error:** `{str(e)}`\n🧹 **Cleaning up...**")
            if task:
                cleanup_files(task)
            if gid and gid in active_downloads:
                del active_downloads[gid]

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")
        if task:
            cleanup_files(task)
        if gid and gid in active_downloads:
            del active_downloads[gid]


@app.on_message(filters.command(["qbleech", "qb"]))
async def qbit_leech_command(client, message: Message):
    torrent_hash = None
    task         = None

    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/qbleech <magnet/torrent>`")
            return

        url     = args[1].split()[0]
        extract = "-e" in message.text.lower()

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        status_msg = await message.reply_text("🔄 **Adding torrent to qBittorrent...**")

        try:
            qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
            qb.auth_log_in()

            if url.startswith("magnet:"):
                qb.torrents_add(urls=url, save_path=DOWNLOAD_DIR)
            else:
                qb.torrents_add(torrent_files=url, save_path=DOWNLOAD_DIR)

            await asyncio.sleep(2)

            torrents = qb.torrents_info()
            if not torrents:
                await status_msg.edit_text("❌ **Failed to add torrent**")
                return

            torrent_hash = torrents[-1].hash

            task = DownloadTask(torrent_hash, message.from_user.id, status_msg.id, extract, "qbit")
            active_downloads[torrent_hash] = task

            asyncio.create_task(update_progress(task, status_msg))

            while True:
                torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
                if torrent_info.state in ["uploading", "pausedUP", "stalledUP"] or task.cancelled:
                    break
                await asyncio.sleep(1)

            if task.cancelled:
                await status_msg.edit_text("❌ **Download cancelled**\n🧹 **Cleaning up files...**")
                qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                cleanup_files(task)
                if torrent_hash in active_downloads:
                    del active_downloads[torrent_hash]
                await status_msg.edit_text("❌ **Download cancelled**\n✅ **Files cleaned up!**")
                return

            await status_msg.edit_text("✅ **Download completed!**\n📤 **Starting upload...**")

            torrent_info   = qb.torrents_info(torrent_hashes=torrent_hash)[0]
            file_path      = os.path.join(DOWNLOAD_DIR, torrent_info.name)
            task.file_path = file_path

            if extract and os.path.isfile(file_path) and file_path.endswith(('.zip', '.7z', '.tar.gz')):
                extract_dir      = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir

                await status_msg.edit_text("📦 **Starting extraction...**")
                if await extract_archive(file_path, extract_dir,
                                         status_msg=status_msg, task=task):
                    await status_msg.edit_text("✅ **Extraction done!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message,
                                             caption="📁 Extracted files",
                                             status_msg=status_msg, task=task)
                else:
                    await upload_to_telegram(file_path, message,
                                             status_msg=status_msg, task=task)
            else:
                await upload_to_telegram(file_path, message,
                                         status_msg=status_msg, task=task)

            await status_msg.edit_text("✅ **Upload completed!**\n🧹 **Cleaning up files...**")
            qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
            cleanup_files(task)
            await status_msg.edit_text("✅ **Task completed successfully!**")

            if torrent_hash in active_downloads:
                del active_downloads[torrent_hash]

        except Exception as e:
            await status_msg.edit_text(f"❌ **Error:** `{str(e)}`\n🧹 **Cleaning up...**")
            if torrent_hash:
                try:
                    qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                    qb.auth_log_in()
                    qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                except Exception:
                    pass
            if task:
                cleanup_files(task)
            if torrent_hash and torrent_hash in active_downloads:
                del active_downloads[torrent_hash]

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")
        if torrent_hash:
            try:
                qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                qb.auth_log_in()
                qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
            except Exception:
                pass
        if task:
            cleanup_files(task)
        if torrent_hash and torrent_hash in active_downloads:
            del active_downloads[torrent_hash]


@app.on_message(filters.command(["stop"]) | filters.regex(r"^/stop_"))
async def stop_command(client, message: Message):
    try:
        if message.text.startswith("/stop_"):
            gid_short = message.text.replace("/stop_", "").strip()
        else:
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("❌ **Usage:** `/stop <task_id>`")
                return
            gid_short = args[1].strip()

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
            if found_task.engine == "aria2":
                download = aria2.get_download(found_gid)
                aria2.remove([download], force=True, files=True)
            elif found_task.engine == "qbit":
                qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                qb.auth_log_in()
                qb.torrents_delete(delete_files=True, torrent_hashes=found_gid)

            cleanup_files(found_task)
        except Exception as e:
            print(f"Stop error: {e}")

        await message.reply_text(f"✅ **Task `{gid_short}` cancelled & files cleaned!**")

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")


if __name__ == "__main__":
    print("🚀 Starting Leech Bot...")
    print("📋 Commands: /leech, /l, /qbleech, /qb")
    print("🎨 UI: Detailed progress tracking — Download / Extract / Upload")
    print("🧹 Auto cleanup after every upload")
    app.run()
