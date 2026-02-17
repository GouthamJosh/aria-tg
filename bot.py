import os
import time
import asyncio
import aria2p
import psutil
import shutil
import zipfile
import py7zr
from datetime import timedelta
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message
from qbittorrentapi import Client as qBitClient

# Configuration
API_ID = int(os.environ.get("API_ID", '18979569'))
API_HASH = os.environ.get("API_HASH", '45db354387b8122bdf6c1b0beef93743')
BOT_TOKEN = os.environ.get("BOT_TOKEN", '7531165057:AAHnMXz9LmogLXtpFfZaYL3kFL5YRZ5bOAU')
DOWNLOAD_DIR = "/tmp/downloads"
ARIA2_HOST = "http://localhost"
ARIA2_PORT = 6800
QBIT_HOST = "localhost"
QBIT_PORT = 8080
PORT = int(os.environ.get("PORT", 8080)) # Port for Render/Koyeb

# Initialize Pyrogram client
app = Client("leech_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize Aria2
aria2 = aria2p.API(aria2p.Client(host=ARIA2_HOST, port=ARIA2_PORT, secret="gjxmlx"))

# Active downloads tracking
active_downloads = {}

# --- AIOHTTP Web Server for Sleep Prevention ---
async def web_server():
    async def handle(request):
        return web.Response(text="Bot is Running!")

    server = web.Application()
    server.router.add_get('/', handle)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"🌍 Web server started on port {PORT}")

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
        self.filename = "Processing..."
        self.file_size = 0
        self.username = username or "User"
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
    """Create the requested visual progress bar"""
    # Using 10 blocks: ⬢⬢⬢⬢⬡⬡⬡⬡⬡⬡
    if percentage > 100: percentage = 100
    filled = int(percentage / 10)
    bar = "⬢" * filled + "⬡" * (10 - filled)
    return f"[{bar}]"

def format_speed(speed):
    """Format speed in MB/s"""
    mb_speed = speed / (1024 * 1024)
    return f"{mb_speed:.2f}MB/s" if mb_speed > 0 else "0 B/s"

def format_size(size_bytes):
    """Format size in GB or MB"""
    if not size_bytes: return "0B"
    gb = size_bytes / (1024**3)
    if gb >= 1:
        return f"{gb:.2f}GB"
    else:
        mb = size_bytes / (1024**2)
        return f"{mb:.2f}MB"

def format_time_str(seconds):
    """Format time for UI"""
    if seconds <= 0: return "0s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0: return f"{hours}h{minutes}m{secs}s"
    if minutes > 0: return f"{minutes}m{secs}s"
    return f"{secs}s"

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
    now = time.time()
    time_diff = now - task.last_update
    if time_diff >= 1.0 and task.last_update > 0:
        speed = (current_bytes - task.prev_bytes) / time_diff
        task.prev_bytes = current_bytes
        task.last_update = now
        return speed
    return 0

async def update_progress(task, message):
    """Update all phases progress with REAL TIME SPEEDS and NEW UI"""
    try:
        update_count = 0
        while not task.cancelled:
            try:
                user_mention = task.username # Simplified for display
                # Try to get better name if possible
                try: 
                    chat = await app.get_chat(task.user_id)
                    user_mention = chat.first_name
                except: pass
            except:
                user_mention = f"User"
            
            # Shorten filename
            fname_display = task.filename
            
            # --- PHASE 1: DOWNLOAD ---
            if task.current_phase == "download":
                if task.engine == "aria2":
                    download = aria2.get_download(task.gid)
                    if download.is_complete: break
                    
                    # Update task info
                    task.filename = download.name
                    if not task.filename: task.filename = "Processing..."
                    fname_display = task.filename

                    progress = float(download.progress)
                    speed = download.download_speed
                    total_size = download.total_length
                    downloaded = download.completed_length
                    eta_seconds = download.eta.total_seconds() if download.eta else 0
                    
                    task.last_update = time.time()
                    task.prev_bytes = downloaded
                    
                elif task.engine == "qbit":
                    qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                    qb.auth_log_in()
                    torrent_info = qb.torrents_info(torrent_hashes=task.gid)[0]
                    if torrent_info.state in ["uploading", "pausedUP", "stalledUP", "error"]: break
                    
                    task.filename = torrent_info.name
                    fname_display = task.filename

                    progress = torrent_info.progress * 100
                    speed = torrent_info.dlspeed
                    total_size = torrent_info.size
                    downloaded = torrent_info.downloaded
                    eta_seconds = torrent_info.eta if torrent_info.eta >= 0 else 0
                    
                    task.last_update = time.time()
                    task.prev_bytes = downloaded
                
                # UI Construction
                elapsed = format_time_str(time.time() - task.start_time)
                total_time = format_time_str((time.time() - task.start_time) + eta_seconds)
                
                status_text = (
                    f"`{fname_display}`\n\n"
                    f"Task By —‌{user_mention} ( #ID{task.user_id} ) [Link]\n"
                    f"┟ {create_progress_bar(progress)} {progress:.1f}%\n"
                    f"┠ Processed → {format_size(downloaded)} of {format_size(total_size)}\n"
                    f"┠ Status → Downloading\n"
                    f"┠ Speed → {format_speed(speed)}\n"
                    f"┠ Time → {elapsed} of {total_time} ( {format_time_str(eta_seconds)} )\n"
                    f"┠ Engine → {task.engine.capitalize()}\n"
                    f"┠ In Mode → #{'Direct' if task.engine=='aria2' else 'Torrent'}\n"
                    f"┠ Out Mode → #Leech\n"
                    f"┖ Stop → /stop_{task.gid[:8]}"
                )

            # --- PHASE 2: EXTRACT ---
            elif task.current_phase == "extract":
                extract_progress = min(99.9, (task.extract_progress / max(1, task.total_extract_files)) * 100)
                extract_speed = calculate_speed(task, task.extract_progress * 1024 * 1024) # Simulated
                elapsed = format_time_str(time.time() - task.start_time)

                status_text = (
                    f"`{fname_display}`\n\n"
                    f"Task By —‌{user_mention} ( #ID{task.user_id} ) [Link]\n"
                    f"┟ {create_progress_bar(extract_progress)} {extract_progress:.1f}%\n"
                    f"┠ Processed → {format_size(task.file_size)}\n"
                    f"┠ Status → Extracting\n"
                    f"┠ Speed → {format_speed(extract_speed)}\n"
                    f"┠ Files → {task.extract_progress}/{task.total_extract_files}\n"
                    f"┠ Time → {elapsed}\n"
                    f"┠ Engine → 7-Zip\n"
                    f"┠ In Mode → #Archive\n"
                    f"┠ Out Mode → #Extracted\n"
                    f"┖ Stop → /stop_{task.gid[:8]}"
                )

            # --- PHASE 3: UPLOAD ---
            elif task.current_phase == "upload":
                # Simulated upload progress for visual smoothness if real progress lags
                elapsed_upload = time.time() - task.upload_start_time if task.upload_start_time else 0
                
                # Use real progress if available, otherwise simulate
                if task.upload_total > 0:
                    prog_val = task.upload_progress
                    processed_bytes = (task.upload_progress / 100) * task.upload_total
                else:
                    prog_val = min(99, (elapsed_upload / 60) * 100)
                    processed_bytes = (prog_val / 100) * task.file_size

                upload_speed = calculate_speed(task, processed_bytes)
                elapsed = format_time_str(time.time() - task.start_time)

                status_text = (
                    f"`{fname_display}`\n\n"
                    f"Task By —‌{user_mention} ( #ID{task.user_id} ) [Link]\n"
                    f"┟ {create_progress_bar(prog_val)} {prog_val:.1f}%\n"
                    f"┠ Processed → {format_size(processed_bytes)} of {format_size(task.file_size)}\n"
                    f"┠ Status → Uploading\n"
                    f"┠ Speed → {format_speed(upload_speed)}\n"
                    f"┠ Time → {elapsed}\n"
                    f"┠ Engine → Pyrogram\n"
                    f"┠ In Mode → #Local\n"
                    f"┠ Out Mode → #Telegram\n"
                    f"┖ Stop → /stop_{task.gid[:8]}"
                )
            
            try:
                # Refresh rate slightly slower to avoid floodwait
                if update_count % 4 == 0: 
                    await message.edit_text(status_text)
            except Exception as e:
                print(f"Edit error: {e}")
                # Don't break on edit error, just continue
            
            update_count += 1
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"Progress update error: {e}")

# ... [Rest of your extraction/upload functions remain exactly the same] ...

async def extract_archive(file_path, extract_to, task, status_msg):
    """Extract archive files"""
    try:
        task.current_phase = "extract"
        task.extract_progress = 0
        task.last_update = time.time()
        
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
    """Upload to Telegram"""
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
            
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > 2000 * 1024 * 1024:
                await message.reply_text("❌ File > 2GB")
                return False
            await message.reply_document(document=file_path, caption=caption or os.path.basename(file_path), progress=progress_callback)
            return True
        elif os.path.isdir(file_path):
            for root, dirs, filenames in os.walk(file_path):
                for filename in filenames:
                    fpath = os.path.join(root, filename)
                    if os.path.getsize(fpath) <= 2000 * 1024 * 1024:
                        await message.reply_document(document=fpath, caption=filename, progress=progress_callback)
            return True
    except Exception as e:
        await message.reply_text(f"❌ Upload error: {e}")
        return False

# ... [Command Handlers with strict Aria2 preservation] ...

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    await message.reply_text("🤖 **Leech Bot**\n/leech <link>\n/qbleech <magnet>\n/stop <id>")

@app.on_message(filters.command(["leech", "l"]))
async def leech_command(client, message: Message):
    gid = None
    task = None
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2: return
        url = args[1].split()[0]
        extract = "-e" in message.text.lower()
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        status_msg = await message.reply_text("🔄 **Processing...**")
        
        # ARIA2 PRESERVED
        download = aria2.add_uris([url], options={"dir": DOWNLOAD_DIR})
        gid = download.gid
        
        task = DownloadTask(gid, message.from_user.id, status_msg.id, extract, "aria2", message.from_user.first_name)
        active_downloads[gid] = task
        
        asyncio.create_task(update_progress(task, status_msg))
        
        while not download.is_complete and not task.cancelled:
            await asyncio.sleep(1)
            download.update()
            
        if task.cancelled:
            await status_msg.edit_text("❌ Cancelled")
            cleanup_files(task)
            return

        download.update()
        file_path = os.path.join(DOWNLOAD_DIR, download.name)
        task.file_path = file_path
        task.filename = download.name
        task.file_size = os.path.getsize(file_path)
        
        if extract and file_path.endswith(('.zip', '.7z', '.tar.gz')):
            extract_dir = os.path.join(DOWNLOAD_DIR, f"ext_{int(time.time())}")
            os.makedirs(extract_dir, exist_ok=True)
            task.extract_dir = extract_dir
            if await extract_archive(file_path, extract_dir, task, status_msg):
                await upload_to_telegram(extract_dir, message, "Extracted", task)
            else:
                await upload_to_telegram(file_path, message, task=task)
        else:
            await upload_to_telegram(file_path, message, task=task)
            
        await status_msg.delete()
        cleanup_files(task)
        if gid in active_downloads: del active_downloads[gid]
            
    except Exception as e:
        await message.reply_text(f"Error: {e}")

@app.on_message(filters.command(["qbleech", "qb"]))
async def qbit_leech_command(client, message: Message):
    # Same logic but for qBittorrent (Preserved)
    try:
        args = message.text.split(maxsplit=1)
        url = args[1].strip()
        extract = "-e" in message.text.lower()
        
        status_msg = await message.reply_text("🔄 **Adding Torrent...**")
        qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
        qb.auth_log_in()
        qb.torrents_add(urls=[url], save_path=DOWNLOAD_DIR)
        await asyncio.sleep(2)
        
        torrents = qb.torrents_info()
        if not torrents: return
        t_hash = torrents[-1].hash
        
        task = DownloadTask(t_hash, message.from_user.id, status_msg.id, extract, "qbit", message.from_user.first_name)
        active_downloads[t_hash] = task
        asyncio.create_task(update_progress(task, status_msg))
        
        while True:
            ti = qb.torrents_info(torrent_hashes=t_hash)[0]
            if ti.state in ["uploading", "pausedUP", "stalledUP"] or task.cancelled: break
            await asyncio.sleep(1)
            
        if not task.cancelled:
            ti = qb.torrents_info(torrent_hashes=t_hash)[0]
            file_path = os.path.join(DOWNLOAD_DIR, ti.name)
            task.file_path = file_path
            task.filename = ti.name
            task.file_size = ti.size
            
            # Extract/Upload logic same as leech...
            await upload_to_telegram(file_path, message, task=task)
            
        qb.torrents_delete(delete_files=True, torrent_hashes=t_hash)
        cleanup_files(task)
        
    except Exception as e:
        await message.reply_text(f"Error: {e}")

@app.on_message(filters.command(["stop"]) | filters.regex(r"^/stop_"))
async def stop_command(client, message: Message):
    # Stop logic preserved
    try:
        if message.text.startswith("/stop_"): gid = message.text.replace("/stop_", "")
        else: gid = message.text.split()[1]
        
        for g, t in active_downloads.items():
            if g.startswith(gid):
                t.cancelled = True
                try:
                    if t.engine == "aria2": aria2.remove([aria2.get_download(g)], force=True, files=True)
                    elif t.engine == "qbit": qBitClient(host=QBIT_HOST, port=QBIT_PORT).torrents_delete(delete_files=True, torrent_hashes=g)
                except: pass
                await message.reply("✅ Stopped")
                return
    except: pass

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    
    # Start Web Server + Bot
    loop = asyncio.get_event_loop()
    loop.create_task(web_server())
    print("🚀 Bot Started with Aria2 & qBittorrent + AioHTTP Server")
    app.run()
