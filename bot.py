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
    def __init__(self, gid, user_id, message_id, extract=False, engine="aria2"):
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
    gb = size_bytes / (1024**3)
    if gb >= 1:
        return f"{gb:.2f}GB"
    else:
        mb = size_bytes / (1024**2)
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
    ram_used_gb = ram.used / (1024**3)
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
            
            # Create detailed status message (similar to the image)
            progress_bar = create_progress_bar(progress)
            elapsed = task.get_elapsed_time()
            stats = get_system_stats()
            
            status_text = (
                f"**Task By** {message.from_user.mention} ( #{task.user_id} ) [Link]\n"
                f"├ {progress_bar}\n"
                f"├ **Processed** → {format_size(downloaded)} of {format_size(total_size)}\n"
                f"├ **Status** → Download\n"
                f"├ **Speed** → {format_speed(speed)}\n"
                f"├ **Time** → {elapsed} ( {format_time(eta)} )\n"
                f"├ **Engine** → {task.engine.upper()} v{get_engine_version(task.engine)}\n"
                f"├ **In Mode** → #aria2\n"
                f"├ **Out Mode** → #Leech (Zip)\n"
                f"└ **Stop** → /stop_{task.gid[:8]}\n\n"
                f"**📊 Bot Stats**\n"
                f"├ **CPU** → {stats['cpu']:.2f}% | **RAM** → {stats['ram_used']:.2f}GB [{stats['ram_percent']:.1f}%]\n"
                f"└ **UP** → {stats['uptime']}"
            )
            
            # Only update every 3 seconds to avoid flood
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

async def extract_archive(file_path, extract_to):
    """Extract archive files (zip, 7z, tar.gz)"""
    try:
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
        elif file_path.endswith('.7z'):
            with py7zr.SevenZipFile(file_path, mode='r') as archive:
                archive.extractall(path=extract_to)
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            import tarfile
            with tarfile.open(file_path, 'r:*') as tar:
                tar.extractall(extract_to)
        return True
    except Exception as e:
        print(f"Extraction error: {e}")
        return False

async def upload_to_telegram(file_path, message, caption=""):
    """Upload file/folder to Telegram"""
    try:
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > 2000 * 1024 * 1024:  # 2GB Telegram limit
                await message.reply_text("❌ File too large for Telegram (>2GB)")
                return False
                
            await message.reply_document(
                document=file_path,
                caption=caption or os.path.basename(file_path),
                progress=lambda current, total: None
            )
            return True
            
        elif os.path.isdir(file_path):
            files = []
            for root, dirs, filenames in os.walk(file_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
            
            for file in files:
                file_size = os.path.getsize(file)
                if file_size <= 2000 * 1024 * 1024:
                    await message.reply_document(
                        document=file,
                        caption=os.path.basename(file)
                    )
                    await asyncio.sleep(1)  # Avoid flood
            return True
    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}")
        return False

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    """Start/Help command handler"""
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
        "✓ Detailed progress tracking\n"
        "✓ Speed, ETA, elapsed time\n"
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
    """Leech command - download using aria2"""
    gid = None
    task = None
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/leech <link>` or `/leech <link> -e`")
            return
        
        url = args[1].split()[0]
        extract = "-e" in message.text.lower()
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        status_msg = await message.reply_text("🔄 **Starting download...**")
        
        try:
            download = aria2.add_uris([url], options={"dir": DOWNLOAD_DIR})
            gid = download.gid
            
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
                except:
                    pass
                cleanup_files(task)
                if gid in active_downloads:
                    del active_downloads[gid]
                await status_msg.edit_text("❌ **Download cancelled**\n✅ **Files cleaned up!**")
                return
            
            await status_msg.edit_text("✅ **Download completed!**\n📤 **Uploading to Telegram...**")
            
            download.update()
            file_path = os.path.join(DOWNLOAD_DIR, download.name)
            task.file_path = file_path
            
            if extract and file_path.endswith(('.zip', '.7z', '.tar.gz', '.tgz', '.tar')):
                extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir
                
                await status_msg.edit_text("📦 **Extracting archive...**")
                
                if await extract_archive(file_path, extract_dir):
                    await status_msg.edit_text("✅ **Extraction completed!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message, "📁 Extracted files")
                else:
                    await status_msg.edit_text("❌ **Extraction failed!** Uploading original file...")
                    await upload_to_telegram(file_path, message)
            else:
                await upload_to_telegram(file_path, message)
            
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
    """qBittorrent leech command - download torrents"""
    torrent_hash = None
    task = None
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/qbleech <magnet/torrent>`")
            return
        
        url = args[1].split()[0]
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
            
            await status_msg.edit_text("✅ **Download completed!**\n📤 **Uploading to Telegram...**")
            
            torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
            file_path = os.path.join(DOWNLOAD_DIR, torrent_info.name)
            task.file_path = file_path
            
            if extract and os.path.isfile(file_path) and file_path.endswith(('.zip', '.7z', '.tar.gz')):
                extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir
                
                await status_msg.edit_text("📦 **Extracting archive...**")
                
                if await extract_archive(file_path, extract_dir):
                    await status_msg.edit_text("✅ **Extraction completed!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message, "📁 Extracted files")
                else:
                    await upload_to_telegram(file_path, message)
            else:
                await upload_to_telegram(file_path, message)
            
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
                except:
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
            except:
                pass
        if task:
            cleanup_files(task)
        if torrent_hash and torrent_hash in active_downloads:
            del active_downloads[torrent_hash]

@app.on_message(filters.command(["stop"]) | filters.regex(r"^/stop_"))
async def stop_command(client, message: Message):
    """Stop/cancel a download task"""
    try:
        if message.text.startswith("/stop_"):
            gid_short = message.text.replace("/stop_", "").strip()
        else:
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("❌ **Usage:** `/stop <task_id>`")
                return
            gid_short = args[1].strip()
        
        # Find the task
        found_task = None
        found_gid = None
        
        for gid, task in active_downloads.items():
            if gid.startswith(gid_short) or gid[:8] == gid_short:
                found_task = task
                found_gid = gid
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
    print("🎨 UI: Detailed progress tracking with stats")
    print("🧹 Auto cleanup after every upload")
    app.run()
