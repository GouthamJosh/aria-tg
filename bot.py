import os
import time
import asyncio
import aria2p
from pyrogram import Client, filters
from pyrogram.types import Message
import py7zr
import zipfile
import shutil
from datetime import timedelta
import psutil

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────
API_ID = os.environ.get("API_ID",'18979569')
API_HASH = os.environ.get("API_HASH",'45db354387b8122bdf6c1b0beef93743')
BOT_TOKEN = os.environ.get("BOT_TOKEN",'7531165057:AAHnMXz9LmogLXtpFfZaYL3kFL5YRZ5bOAU')
DOWNLOAD_DIR = "/tmp/downloads"
ARIA2_HOST  = "http://localhost"
ARIA2_PORT  = 6800

# Initialize Pyrogram client
app = Client("leech_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize Aria2
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret=""))

# Active downloads tracking
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
#  Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────
def create_progress_bar(percentage):
    if percentage >= 100:
        return "[●●●●●●●●●●] 100%"
    filled = int(percentage / 10)
    bar = "●" * filled + "○" * (10 - filled)
    return f"[{bar}] {percentage:.1f}%"


def format_speed(speed):
    mb_speed = speed / (1024 * 1024)
    return f"{mb_speed:.2f}MB/s" if mb_speed > 0 else "0 B/s"


def format_size(size_bytes):
    gb = size_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f}GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.2f}MB"


def format_time(seconds):
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


def get_system_stats():
    cpu_percent  = psutil.cpu_percent(interval=0.1)
    ram          = psutil.virtual_memory()
    ram_used_gb  = ram.used / (1024 ** 3)
    ram_percent  = ram.percent
    uptime       = time.time() - psutil.boot_time()
    return {
        'cpu':        cpu_percent,
        'ram_used':   ram_used_gb,
        'ram_percent': ram_percent,
        'uptime':     format_time(uptime),
    }


def cleanup_files(task):
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


# ─────────────────────────────────────────────────────────────────────────────
#  Download progress loop
# ─────────────────────────────────────────────────────────────────────────────
async def update_progress(task, message):
    try:
        # Give aria2 a moment to register the download before first poll
        await asyncio.sleep(2)

        update_count = 0
        while not task.cancelled:
            try:   # per-iteration guard so one bad poll can't kill the whole loop
                download = aria2.get_download(task.gid)

                if download.is_complete:
                    break

                progress   = download.progress or 0.0
                speed      = download.download_speed or 0
                total_size = download.total_length or 0
                downloaded = download.completed_length or 0
                _eta_td    = download.eta
                eta        = _eta_td.total_seconds() if _eta_td and _eta_td.total_seconds() > 0 else 0
                # aria2 returns "" until HTTP headers arrive —
                # empty backticks make Telegram reject edit_text silently
                filename   = download.name if download.name else "Connecting…"

                task.filename  = filename
                task.file_size = total_size

                # Show "Fetching…" instead of "0.00MB of 0.00MB" while headers load
                size_text = (
                    f"{format_size(downloaded)} of {format_size(total_size)}"
                    if total_size > 0 else "Fetching file info…"
                )

                progress_bar = create_progress_bar(progress)
                elapsed      = task.get_elapsed_time()
                stats        = get_system_stats()

                try:
                    user_mention = f"@{message.chat.username}" if message.chat.username else f"User #{task.user_id}"
                except Exception:
                    user_mention = f"User #{task.user_id}"

                status_text = (
                    f"├ **File** → `{filename}`\n\n"
                    f"**Task By** {user_mention} ( #{task.user_id} ) [Link]\n"
                    f"├ {progress_bar}\n"
                    f"├ **Processed** → {size_text}\n"
                    f"├ **Status** → Download\n"
                    f"├ **Speed** → {format_speed(speed)}\n"
                    f"├ **Time** → {elapsed} ( {format_time(eta)} )\n"
                    f"├ **Engine** → ARIA2 v2.2.18\n"
                    f"├ **In Mode** → #aria2\n"
                    f"├ **Out Mode** → #Leech\n"
                    f"└ **Stop** → /stop_{task.gid[:8]}\n\n"
                    f"**📊 Bot Stats**\n"
                    f"├ **CPU** → {stats['cpu']:.2f}% | **RAM** → {stats['ram_used']:.2f}GB [{stats['ram_percent']:.1f}%]\n"
                    f"└ **UP** → {stats['uptime']}"
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
#  Extraction with live progress UI
# ─────────────────────────────────────────────────────────────────────────────
async def extract_archive(file_path, extract_to, status_msg=None, task=None):
    try:
        filename   = os.path.basename(file_path)
        total_size = os.path.getsize(file_path)
        start_time = time.time()

        async def _render_extract_status(extracted_bytes, total_bytes,
                                         current_file, file_index, total_files):
            pct       = min((extracted_bytes / total_bytes) * 100, 100) if total_bytes > 0 else 0
            elapsed   = time.time() - start_time
            speed     = extracted_bytes / elapsed if elapsed > 0 else 0
            remaining = (total_bytes - extracted_bytes) / speed if speed > 0 else 0
            prog_bar  = create_progress_bar(pct)
            stats     = get_system_stats()

            user_label = f"User #{task.user_id}" if task else "Unknown"
            try:
                if status_msg and status_msg.chat.username:
                    user_label = f"@{status_msg.chat.username} ( #{task.user_id} )"
            except Exception:
                pass

            file_label = f"{file_index}/{total_files}"
            text = (
                f"├ **File** → `{current_file}`\n"
                f"├ **Files** → {file_label}\n\n"
                f"**Task By** {user_label} [Link]\n"
                f"├ {prog_bar}\n"
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
                members           = zf.infolist()
                total_files       = len(members)
                uncompressed_total = sum(m.file_size for m in members)
                extracted_bytes   = 0
                update_tick       = 0

                for idx, member in enumerate(members, start=1):
                    zf.extract(member, extract_to)
                    extracted_bytes += member.file_size
                    update_tick     += 1
                    if update_tick % 5 == 0 or idx == total_files:
                        await _render_extract_status(
                            extracted_bytes, uncompressed_total,
                            member.filename, idx, total_files
                        )
                    await asyncio.sleep(0)

        # ── 7-Zip ─────────────────────────────────────────────────────────────
        elif file_path.endswith('.7z'):
            with py7zr.SevenZipFile(file_path, mode='r') as archive:
                members     = archive.list()
                total_files = len(members)
                total_unc   = sum(getattr(m, 'uncompressed', 0) or 0 for m in members)
                extracted_bytes = 0
                update_tick     = 0

                class _ProgressCallback(py7zr.callbacks.ExtractCallback):
                    def __init__(self_cb): self_cb.file_index = 0
                    def report_start_preparation(self_cb): pass
                    def report_start(self_cb, processing_file_path, processing_bytes):
                        self_cb.file_index += 1
                    def report_update(self_cb, decompressed_bytes): pass
                    def report_end(self_cb, processing_file_path, wrote_bytes):
                        nonlocal extracted_bytes, update_tick
                        extracted_bytes += wrote_bytes
                        update_tick     += 1
                    def report_postprocess(self_cb): pass
                    def report_warning(self_cb, message): pass

                try:
                    archive.extractall(path=extract_to, callback=_ProgressCallback())
                    await _render_extract_status(
                        total_unc or total_size, total_unc or total_size,
                        filename, total_files, total_files
                    )
                except TypeError:
                    archive.extractall(path=extract_to)
                    await _render_extract_status(total_size, total_size, filename, 1, 1)

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
#  Upload with live progress UI
# ─────────────────────────────────────────────────────────────────────────────
async def upload_to_telegram(file_path, message, caption="", status_msg=None, task=None):

    async def _render_upload_status(filename, uploaded, total,
                                     file_index, total_files,
                                     speed, elapsed, eta):
        pct      = min((uploaded / total) * 100, 100) if total > 0 else 0
        prog_bar = create_progress_bar(pct)
        stats    = get_system_stats()

        user_label = f"User #{task.user_id}" if task else "Unknown"
        try:
            if status_msg and status_msg.chat.username:
                user_label = f"@{status_msg.chat.username} ( #{task.user_id} )"
        except Exception:
            pass

        file_line = f"├ **Files** → {file_index}/{total_files}\n" if total_files > 1 else ""

        text = (
            f"├ **File** → `{filename}`\n\n"
            f"{file_line}"
            f"**Task By** {user_label} [Link]\n"
            f"├ {prog_bar}\n"
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
            file_size = os.path.getsize(file_path)
            if file_size > 2000 * 1024 * 1024:
                await message.reply_text("❌ File too large for Telegram (>2GB)")
                return False

            filename          = os.path.basename(file_path)
            start_time        = time.time()
            last_update_time  = [time.time()]
            last_uploaded_ref = [0]

            async def _progress(current, total):
                now     = time.time()
                elapsed = now - start_time
                dt      = now - last_update_time[0]
                if dt >= 2:
                    speed = (current - last_uploaded_ref[0]) / dt if dt > 0 else 0
                    eta   = (total - current) / speed if speed > 0 else 0
                    last_update_time[0]  = now
                    last_uploaded_ref[0] = current
                    await _render_upload_status(filename, current, total, 1, 1, speed, elapsed, eta)

            await _render_upload_status(filename, 0, file_size, 1, 1, 0, 0, 0)
            await message.reply_document(
                document=file_path,
                caption=caption or filename,
                progress=_progress
            )
            elapsed = time.time() - start_time
            await _render_upload_status(filename, file_size, file_size, 1, 1, 0, elapsed, 0)
            return True

        elif os.path.isdir(file_path):
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

            # Track per-file progress so concurrent uploads don't share state
            file_progress = {i: (0, os.path.getsize(fp))
                             for i, fp in enumerate(files, start=1)}
            last_render   = [0.0]

            async def _render_all_progress():
                """Combined status message showing all files uploading at once."""
                now = time.time()
                if now - last_render[0] < 2:
                    return
                last_render[0] = now

                stats      = get_system_stats()
                user_label = f"User #{task.user_id}" if task else "Unknown"
                try:
                    if status_msg and status_msg.chat.username:
                        user_label = f"@{status_msg.chat.username} ( #{task.user_id} )"
                except Exception:
                    pass

                total_uploaded = sum(u for u, _ in file_progress.values())
                total_bytes    = sum(t for _, t in file_progress.values())
                overall_pct    = min((total_uploaded / total_bytes) * 100, 100) if total_bytes > 0 else 0
                overall_bar    = create_progress_bar(overall_pct)

                lines = [
                    f"**Task By** {user_label} [Link]\n",
                    f"├ **Overall** {overall_bar}\n",
                    f"├ **Processed** → {format_size(total_uploaded)} of {format_size(total_bytes)}\n",
                    f"├ **Status** → Uploading ({total_files} files simultaneously)\n",
                ]
                for i, fp in enumerate(files, start=1):
                    fname         = os.path.basename(fp)
                    uploaded, tot = file_progress[i]
                    pct           = min((uploaded / tot) * 100, 100) if tot > 0 else 0
                    bar           = create_progress_bar(pct)
                    lines.append(f"├ `{fname}`  {format_size(uploaded)}/{format_size(tot)}  {bar}\n")

                lines.append(
                    f"\n**📊 Bot Stats**\n"
                    f"├ **CPU** → {stats['cpu']:.2f}% | **RAM** → {stats['ram_used']:.2f}GB [{stats['ram_percent']:.1f}%]\n"
                    f"└ **UP** → {stats['uptime']}"
                )
                try:
                    if status_msg:
                        await status_msg.edit_text("".join(lines))
                except Exception:
                    pass

            async def _upload_one(file_index, fpath):
                """Upload one file concurrently, updating shared progress dict."""
                file_size = os.path.getsize(fpath)
                filename  = os.path.basename(fpath)

                async def _progress(current, total):
                    file_progress[file_index] = (current, total)
                    await _render_all_progress()

                file_progress[file_index] = (0, file_size)
                await message.reply_document(
                    document=fpath,
                    caption=f"📄 {filename}  [{file_index}/{total_files}]" + (f"\n{caption}" if caption else ""),
                    progress=_progress
                )
                file_progress[file_index] = (file_size, file_size)

            # Show initial state then upload all files at the same time
            await _render_all_progress()
            await asyncio.gather(*[_upload_one(i, fp) for i, fp in enumerate(files, start=1)])
            # Final 100% frame
            last_render[0] = 0
            await _render_all_progress()
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
        "**✨ Features:**\n"
        "✓ Direct links (HTTP/HTTPS/FTP)\n"
        "✓ Auto extraction (.zip, .7z, .tar.gz)\n"
        "✓ Live progress — Download / Extract / Upload\n"
        "✓ Speed, ETA, elapsed time on every phase\n"
        "✓ CPU/RAM monitoring\n"
        "✓ Auto cleanup after upload\n\n"
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

            task = DownloadTask(gid, message.from_user.id, status_msg.id, extract)
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

                await status_msg.edit_text("📦 **Starting extraction...**")
                if await extract_archive(file_path, extract_dir, status_msg=status_msg, task=task):
                    await status_msg.edit_text("✅ **Extraction done!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message,
                                             caption="📁 Extracted files",
                                             status_msg=status_msg, task=task)
                else:
                    await status_msg.edit_text("❌ **Extraction failed!** Uploading original...")
                    await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)
            else:
                await upload_to_telegram(file_path, message, status_msg=status_msg, task=task)

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
            download = aria2.get_download(found_gid)
            aria2.remove([download], force=True, files=True)
            cleanup_files(found_task)
        except Exception as e:
            print(f"Stop error: {e}")

        await message.reply_text(f"✅ **Task `{gid_short}` cancelled & files cleaned!**")

    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")


if __name__ == "__main__":
    print("🚀 Starting Leech Bot...")
    print("📋 Commands: /leech, /l, /stop")
    print("🎨 Live progress — Download / Extract / Upload")
    print("🧹 Auto cleanup after every upload")
    app.run()
