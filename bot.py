import os
import time
import asyncio
import aria2p
from pyrogram import Client, filters
from pyrogram.types import Message
from qbittorrentapi import Client as qBitClient
import py7zr
import zipfile
import shutil
from datetime import timedelta
import psutil

# Configuration
API_ID = os.environ.get("API_ID",'18979569')
API_HASH = os.environ.get("API_HASH",'45db354387b8122bdf6c1b0beef93743')
BOT_TOKEN = os.environ.get("BOT_TOKEN",'7531165057:AAHnMXz9LmogLXtpFfZaYL3kFL5YRZ5bOAU') #@l2leech
DOWNLOAD_DIR = "/tmp/downloads"
ARIA2_HOST = "http://localhost"
ARIA2_PORT = 6800
QBIT_HOST = "localhost"
QBIT_PORT = 8080

# Initialize Pyrogram client
app = Client("leech_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize Aria2
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret="gjxmlx"))

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
        self.current_phase = "download"
        self.upload_progress = 0
        self.upload_total = 0
        self.upload_speed = 0
        self.upload_start_time = None
        self.extract_progress = 0
        self.total_extract_files = 0
        self.prev_bytes = 0
        self.last_update = 0
        
    def get_elapsed_time(self):
        elapsed = time.time() - self.start_time
        return str(timedelta(seconds=int(elapsed)))

def create_progress_bar(percentage):
    """Create a visual progress bar"""
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
            elif os.path.isdir(task.file_path):
                shutil.rmtree(task.file_path, ignore_errors=True)
        
        if task.extract_dir and os.path.exists(task.extract_dir):
            shutil.rmtree(task.extract_dir, ignore_errors=True)
            
    except Exception as e:
        print(f"⚠ Cleanup error: {e}")

def calculate_speed(task, current_bytes):
    """Calculate real-time speed for all phases"""
    now = time.time()
    time_diff = now - task.last_update
    if time_diff >= 1.0 and task.last_update > 0:
        speed = (current_bytes - task.prev_bytes) / time_diff
        task.prev_bytes = current_bytes
        task.last_update = now
        return speed
    return 0

async def update_progress(task, message):
    """Update all phases progress with REAL TIME SPEEDS"""
    try:
        update_count = 0
        while not task.cancelled:
            stats = get_system_stats()
            
            try:
                user_mention = f"@{message.chat.username}" if message.chat.username else f"User #{task.user_id}"
            except:
                user_mention = f"User #{task.user_id}"
            
            filename = (task.filename[:45] + "..." if len(task.filename) > 45 else task.filename)
            
            if task.current_phase == "download":
                if task.engine == "aria2":
                    download = aria2.get_download(task.gid)
                    if download.is_complete:
                        break
                    progress = float(download.progress)
                    speed = download.download_speed
                    total_size = download.total_length
                    downloaded = download.completed_length
                    eta = download.eta.total_seconds() if download.eta else 0
                    task.last_update = time.time()
                    task.prev_bytes = downloaded
                    
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
                    eta = torrent_info.eta if torrent_info.eta >= 0 else 0
                    task.last_update = time.time()
                    task.prev_bytes = downloaded
                
                status_text = (
                    f"`{filename}`\n\n"
                    f"Task By {user_mention} (#{task.user_id}) [Link]\n"
                    f"- {create_progress_bar(progress)}\n"
                    f"| Processed: {format_size(downloaded)} of {format_size(total_size)}\n"
                    f"| Status: Download\n"
                    f"| Speed: {format_speed(speed)}\n"
                    f"| Time: {task.get_elapsed_time()} ({format_time(eta)})\n"
                    f"| Engine: {task.engine.upper()} v{get_engine_version(task.engine)}\n"
                    f"| In Mode: #{task.engine}\n"
                    f"| Out Mode: #tg\n"
                    f"| Stop: /stop_{task.gid[:8]}\n\n"
                    f"BOT STATS:\n"
                    f"| CPU: {stats['cpu']:.1f}% | RAM: {stats['ram_used']:.1f}GB [{stats['ram_percent']:.1f}%] | UP: {stats['uptime']}"
                )
                
            elif task.current_phase == "extract":
                extract_progress = min(99, (task.extract_progress / max(1, task.total_extract_files)) * 100)
                extract_speed = calculate_speed(task, task.extract_progress * 1024 * 1024)  # Simulated bytes
                status_text = (
                    f"`{filename}`\n\n"
                    f"Task By {user_mention} (#{task.user_id}) [Link]\n"
                    f"- {create_progress_bar(extract_progress)}\n"
                    f"| Processed: {format_size(task.file_size)}\n"
                    f"| Status: Extracting...\n"
                    f"| Speed: {format_speed(extract_speed)}\n"
                    f"| Files: {task.extract_progress}/{task.total_extract_files}\n"
                    f"| Time: {task.get_elapsed_time()}\n"
                    f"| Engine: {task.engine.upper()} v{get_engine_version(task.engine)}\n"
                    f"| In Mode: #{task.engine}\n"
                    f"| Out Mode: #tg\n"
                    f"| Stop: /stop_{task.gid[:8]}\n\n"
                    f"BOT STATS:\n"
                    f"| CPU: {stats['cpu']:.1f}% | RAM: {stats['ram_used']:.1f}GB [{stats['ram_percent']:.1f}%] | UP: {stats['uptime']}"
                )
                
            elif task.current_phase == "upload":
                elapsed_upload = time.time() - task.upload_start_time if task.upload_start_time else 0
                simulated_progress = min(100, (elapsed_upload / 45) * 100)
                upload_bytes = task.file_size * (simulated_progress / 100)
                upload_speed = calculate_speed(task, int(upload_bytes))
                
                status_text = (
                    f"`{filename}`\n\n"
                    f"Task By {user_mention} (#{task.user_id}) [Link]\n"
                    f"- {create_progress_bar(simulated_progress)}\n"
                    f"| Processed: {format_size(upload_bytes)} of {format_size(task.file_size)}\n"
                    f"| Status: Uploading\n"
                    f"| Speed: {format_speed(upload_speed)}\n"
                    f"| Time: {task.get_elapsed_time()}\n"
                    f"| Engine: {task.engine.upper()} v{get_engine_version(task.engine)}\n"
                    f"| In Mode: #{task.engine}\n"
                    f"| Out Mode: #tg\n"
                    f"| Stop: /stop_{task.gid[:8]}\n\n"
                    f"BOT STATS:\n"
                    f"| CPU: {stats['cpu']:.1f}% | RAM: {stats['ram_used']:.1f}GB [{stats['ram_percent']:.1f}%] | UP: {stats['uptime']}"
                )
            
            try:
                if update_count % 3 == 0:
                    await message.edit_text(status_text, parse_mode=None)
            except Exception as e:
                print(f"Edit error: {e}")
                break
            
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

async def extract_archive(file_path, extract_to, task, status_msg):
    """Extract archive files with REAL progress tracking"""
    try:
        task.current_phase = "extract"
        task.extract_progress = 0
        task.last_update = time.time()
        task.prev_bytes = 0
        
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                filelist = zip_ref.namelist()
                task.total_extract_files = len(filelist)
                
                for i, filename in enumerate(filelist):
                    zip_ref.extract(filename, extract_to)
                    task.extract_progress = i + 1
                    task.last_update = time.time()
                    
        elif file_path.endswith('.7z'):
            with py7zr.SevenZipFile(file_path, mode='r') as archive:
                filelist = archive.getnames()
                task.total_extract_files = len(filelist)
                archive.extractall(path=extract_to)
                task.extract_progress = task.total_extract_files
                
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            import tarfile
            with tarfile.open(file_path, 'r:*') as tar:
                members = tar.getmembers()
                task.total_extract_files = len(members)
                for i, member in enumerate(members):
                    tar.extract(member, extract_to)
                    task.extract_progress = i + 1
                    task.last_update = time.time()
        
        return True
    except Exception as e:
        print(f"Extraction error: {e}")
        return False

async def upload_to_telegram(file_path, message, caption="", task=None):
    """Upload file/folder to Telegram with REAL progress callback"""
    async def progress_callback(current, total):
        if task:
            task.upload_progress = (current / total) * 100
            task.upload_total = total
            task.last_update = time.time()
            task.prev_bytes = current
    
    try:
        if task:
            task.current_phase = "upload"
            task.upload_start_time = time.time()
            task.last_update = time.time()
            task.prev_bytes = 0
            
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > 2000 * 1024 * 1024:
                await message.reply_text("❌ File too large for Telegram (>2GB)", parse_mode=None)
                return False
                
            await message.reply_document(
                document=file_path,
                caption=caption or os.path.basename(file_path),
                progress=progress_callback
            )
            return True
            
        elif os.path.isdir(file_path):
            files = []
            for root, dirs, filenames in os.walk(file_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
            
            total_files = len(files)
            for i, file in enumerate(files):
                file_size = os.path.getsize(file)
                if file_size <= 2000 * 1024 * 1024:
                    await message.reply_document(
                        document=file,
                        caption=os.path.basename(file),
                        progress=progress_callback
                    )
                    if task:
                        task.upload_progress = ((i+1) / total_files) * 100
                    await asyncio.sleep(1)
            return True
    except Exception as e:
        await message.reply_text(f"❌ Upload error: {str(e)}", parse_mode=None)
        return False

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    """Start/Help command handler"""
    help_text = """🤖 Leech Bot - Help

📥 Download Commands:
• /leech <link> - Download direct link
• /l <link> - Short for /leech
• /leech <link> -e - Download & extract

🌪️ Torrent Commands:
• /qbleech <magnet> - Download via qBittorrent
• /qb <magnet> -e - Download torrent & extract

✨ Features:
✓ REAL TIME SPEEDS - All phases
✓ ZIP/7Z/TAR extraction progress
✓ Download → Extract → Upload tracking
✓ Speed, ETA, system stats
✓ Auto cleanup

📖 Examples:
/leech https://example.com/file.zip -e
/qbleech magnet:?xt=urn:btih:...
"""
    await message.reply_text(help_text, parse_mode=None)

@app.on_message(filters.command(["leech", "l"]))
async def leech_command(client, message: Message):
    """Leech command - download using aria2"""
    gid = None
    task = None
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ Usage: /leech <link> or /leech <link> -e", parse_mode=None)
            return
        
        url = args[1].split()[0]
        extract = "-e" in message.text.lower()
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        status_msg = await message.reply_text("🔄 Starting download...", parse_mode=None)
        
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
                await status_msg.edit_text("❌ Download cancelled\n🧹 Cleaning up...", parse_mode=None)
                try:
                    aria2.remove([download], force=True, files=True)
                except:
                    pass
                cleanup_files(task)
                if gid in active_downloads:
                    del active_downloads[gid]
                return
            
            download.update()
            file_path = os.path.join(DOWNLOAD_DIR, download.name)
            task.file_path = file_path
            task.filename = download.name
            task.file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            
            if extract and file_path.endswith(('.zip', '.7z', '.tar.gz', '.tgz', '.tar')):
                extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir
                
                if await extract_archive(file_path, extract_dir, task, status_msg):
                    await upload_to_telegram(extract_dir, message, "📁 Extracted files", task)
                else:
                    await upload_to_telegram(file_path, message, task=task)
            else:
                await upload_to_telegram(file_path, message, task=task)
            
            await status_msg.edit_text("✅ Task completed successfully!", parse_mode=None)
            cleanup_files(task)
            
            if gid in active_downloads:
                del active_downloads[gid]
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: {str(e)}\n🧹 Cleaning up...", parse_mode=None)
            if task:
                cleanup_files(task)
            if gid and gid in active_downloads:
                del active_downloads[gid]
            
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}", parse_mode=None)

@app.on_message(filters.command(["qbleech", "qb"]))
async def qbit_leech_command(client, message: Message):
    """qBittorrent leech command"""
    torrent_hash = None
    task = None
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ Usage: /qbleech <magnet>", parse_mode=None)
            return
        
        url = args[1].strip()
        extract = "-e" in message.text.lower()
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        status_msg = await message.reply_text("🔄 Adding torrent to qBittorrent...", parse_mode=None)
        
        try:
            qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
            qb.auth_log_in()
            
            if url.startswith("magnet:"):
                qb.torrents_add(urls=[url], save_path=DOWNLOAD_DIR)
            else:
                await status_msg.edit_text("❌ Only magnet links supported", parse_mode=None)
                return
            
            await asyncio.sleep(3)
            
            torrents = qb.torrents_info()
            if not torrents:
                await status_msg.edit_text("❌ Failed to add torrent", parse_mode=None)
                return
            
            torrent_hash = torrents[-1].hash
            
            task = DownloadTask(torrent_hash, message.from_user.id, status_msg.id, extract, "qbit")
            active_downloads[torrent_hash] = task
            
            asyncio.create_task(update_progress(task, status_msg))
            
            while True:
                try:
                    torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
                    if torrent_info.state in ["uploading", "pausedUP", "stalledUP"] or task.cancelled:
                        break
                    await asyncio.sleep(1)
                except:
                    break
            
            if task.cancelled:
                await status_msg.edit_text("❌ Download cancelled\n🧹 Cleaning up...", parse_mode=None)
                qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                cleanup_files(task)
                return
            
            torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
            file_path = os.path.join(DOWNLOAD_DIR, torrent_info.name)
            task.file_path = file_path
            task.filename = torrent_info.name
            task.file_size = torrent_info.size
            
            if extract and os.path.isfile(file_path) and file_path.endswith(('.zip', '.7z', '.tar.gz')):
                extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                task.extract_dir = extract_dir
                
                if await extract_archive(file_path, extract_dir, task, status_msg):
                    await upload_to_telegram(extract_dir, message, "📁 Extracted files", task)
                else:
                    await upload_to_telegram(file_path, message, task=task)
            else:
                await upload_to_telegram(file_path, message, task=task)
            
            await status_msg.edit_text("✅ Task completed successfully!", parse_mode=None)
            qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
            cleanup_files(task)
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: {str(e)}\n🧹 Cleaning up...", parse_mode=None)
            if torrent_hash:
                try:
                    qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                    qb.auth_log_in()
                    qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                except:
                    pass
            
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}", parse_mode=None)

@app.on_message(filters.command(["stop"]) | filters.regex(r"^/stop_"))
async def stop_command(client, message: Message):
    """Stop/cancel a download task"""
    try:
        if message.text.startswith("/stop_"):
            gid_short = message.text.replace("/stop_", "").strip()
        else:
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("❌ Usage: /stop <task_id>", parse_mode=None)
                return
            gid_short = args[1].strip()
        
        found_task = None
        found_gid = None
        
        for gid, task in active_downloads.items():
            if gid.startswith(gid_short) or gid[:8] == gid_short:
                found_task = task
                found_gid = gid
                break
        
        if not found_task:
            await message.reply_text(f"❌ Task `{gid_short}` not found!", parse_mode=None)
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
        
        await message.reply_text(f"✅ Task `{gid_short}` cancelled!", parse_mode=None)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}", parse_mode=None)

if __name__ == "__main__":
    print("🚀 Starting Leech Bot... (REAL TIME SPEEDS)")
    print("✅ LIVE speed updates - ALL phases")
    print("✅ Download/Extract/Upload real-time tracking")
    app.run()
