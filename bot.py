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
        
    def get_elapsed_time(self):
        elapsed = time.time() - self.start_time
        return str(timedelta(seconds=int(elapsed)))

def create_progress_bar(percentage):
    """Create a visual progress bar"""
    filled = int(percentage / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {percentage:.1f}%"

def format_speed(speed):
    """Format speed in human readable format"""
    return f"{humanize.naturalsize(speed)}/s" if speed > 0 else "0 B/s"

def format_eta(eta_seconds):
    """Format ETA in human readable format"""
    return str(timedelta(seconds=int(eta_seconds))) if eta_seconds > 0 else "Unknown"

async def update_progress(task, message):
    """Update download progress in real-time"""
    try:
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
            
            # Create progress message
            progress_bar = create_progress_bar(progress)
            elapsed = task.get_elapsed_time()
            
            status_text = (
                f"📥 **Downloading**\n\n"
                f"**Filename:** `{filename}`\n"
                f"**File Size:** {humanize.naturalsize(total_size)}\n"
                f"**Downloaded:** {humanize.naturalsize(downloaded)}\n"
                f"**Speed:** {format_speed(speed)}\n"
                f"**ETA:** {format_eta(eta)}\n"
                f"**Elapsed:** {elapsed}\n"
                f"**Engine:** {task.engine.upper()}\n\n"
                f"{progress_bar}"
            )
            
            # Create cancel button
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task.gid}")]
            ])
            
            try:
                await message.edit_text(status_text, reply_markup=keyboard)
            except Exception:
                pass
                
            await asyncio.sleep(3)
            
    except Exception as e:
        print(f"Progress update error: {e}")

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
        "✓ Real-time progress bar\n"
        "✓ Speed, ETA, elapsed time tracking\n"
        "✓ Cancel downloads anytime\n"
        "✓ Multiple download engines\n\n"
        "**📖 Examples:**\n"
        "`/leech https://example.com/file.zip`\n"
        "`/l https://example.com/archive.7z -e`\n"
        "`/qbleech magnet:?xt=urn:btih:...`\n\n"
        "**Supported Archives:** .zip, .7z, .tar, .tar.gz, .tgz"
    )
    await message.reply_text(help_text)

@app.on_message(filters.command(["leech", "l"]))
async def leech_command(client, message: Message):
    """Leech command - download using aria2"""
    try:
        # Parse command
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/leech <link>` or `/leech <link> -e`\n\n"
                                   "**Example:** `/leech https://example.com/file.zip -e`")
            return
        
        url = args[1].split()[0]
        extract = "-e" in message.text.lower()
        
        # Create download directory
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        # Start download
        status_msg = await message.reply_text("🔄 **Starting download...**")
        
        try:
            # Add download to aria2
            download = aria2.add_uris([url], options={"dir": DOWNLOAD_DIR})
            gid = download.gid
            
            # Create task
            task = DownloadTask(gid, message.from_user.id, status_msg.id, extract, "aria2")
            active_downloads[gid] = task
            
            # Update progress in background
            asyncio.create_task(update_progress(task, status_msg))
            
            # Wait for completion
            while not download.is_complete and not task.cancelled:
                await asyncio.sleep(1)
                download.update()
            
            if task.cancelled:
                await status_msg.edit_text("❌ **Download cancelled**")
                if gid in active_downloads:
                    del active_downloads[gid]
                return
            
            await status_msg.edit_text("✅ **Download completed!**\n📤 **Uploading to Telegram...**")
            
            # Get downloaded file
            download.update()
            file_path = os.path.join(DOWNLOAD_DIR, download.name)
            
            # Extract if requested
            if extract and file_path.endswith(('.zip', '.7z', '.tar.gz', '.tgz', '.tar')):
                extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                
                await status_msg.edit_text("📦 **Extracting archive...**")
                
                if await extract_archive(file_path, extract_dir):
                    await status_msg.edit_text("✅ **Extraction completed!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message, "📁 Extracted files")
                    shutil.rmtree(extract_dir, ignore_errors=True)
                else:
                    await status_msg.edit_text("❌ **Extraction failed!** Uploading original file...")
                    await upload_to_telegram(file_path, message)
            else:
                await upload_to_telegram(file_path, message)
            
            # Cleanup
            if os.path.exists(file_path):
                os.remove(file_path)
            
            await status_msg.edit_text("✅ **Upload completed!**")
            
            if gid in active_downloads:
                del active_downloads[gid]
            
        except Exception as e:
            await status_msg.edit_text(f"❌ **Error:** `{str(e)}`")
            if gid in active_downloads:
                del active_downloads[gid]
            
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")

@app.on_message(filters.command(["qbleech", "qb"]))
async def qbit_leech_command(client, message: Message):
    """qBittorrent leech command - download torrents"""
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/qbleech <magnet/torrent>`\n\n"
                                   "**Example:** `/qbleech magnet:?xt=urn:btih:... -e`")
            return
        
        url = args[1].split()[0]
        extract = "-e" in message.text.lower()
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        status_msg = await message.reply_text("🔄 **Adding torrent to qBittorrent...**")
        
        try:
            qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
            qb.auth_log_in()
            
            # Add torrent
            if url.startswith("magnet:"):
                qb.torrents_add(urls=url, save_path=DOWNLOAD_DIR)
            else:
                qb.torrents_add(torrent_files=url, save_path=DOWNLOAD_DIR)
            
            await asyncio.sleep(2)
            
            # Get torrent hash
            torrents = qb.torrents_info()
            if not torrents:
                await status_msg.edit_text("❌ **Failed to add torrent**")
                return
            
            torrent_hash = torrents[-1].hash
            
            # Create task
            task = DownloadTask(torrent_hash, message.from_user.id, status_msg.id, extract, "qbit")
            active_downloads[torrent_hash] = task
            
            # Update progress in background
            asyncio.create_task(update_progress(task, status_msg))
            
            # Wait for completion
            while True:
                torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
                if torrent_info.state in ["uploading", "pausedUP", "stalledUP"] or task.cancelled:
                    break
                await asyncio.sleep(1)
            
            if task.cancelled:
                qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                await status_msg.edit_text("❌ **Download cancelled**")
                if torrent_hash in active_downloads:
                    del active_downloads[torrent_hash]
                return
            
            await status_msg.edit_text("✅ **Download completed!**\n📤 **Uploading to Telegram...**")
            
            # Get file path
            torrent_info = qb.torrents_info(torrent_hashes=torrent_hash)[0]
            file_path = os.path.join(DOWNLOAD_DIR, torrent_info.name)
            
            # Extract if requested
            if extract and os.path.isfile(file_path) and file_path.endswith(('.zip', '.7z', '.tar.gz')):
                extract_dir = os.path.join(DOWNLOAD_DIR, f"extracted_{int(time.time())}")
                os.makedirs(extract_dir, exist_ok=True)
                
                await status_msg.edit_text("📦 **Extracting archive...**")
                
                if await extract_archive(file_path, extract_dir):
                    await status_msg.edit_text("✅ **Extraction completed!**\n📤 **Uploading to Telegram...**")
                    await upload_to_telegram(extract_dir, message, "📁 Extracted files")
                    shutil.rmtree(extract_dir, ignore_errors=True)
                else:
                    await upload_to_telegram(file_path, message)
            else:
                await upload_to_telegram(file_path, message)
            
            # Cleanup
            qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
            await status_msg.edit_text("✅ **Upload completed!**")
            
            if torrent_hash in active_downloads:
                del active_downloads[torrent_hash]
            
        except Exception as e:
            await status_msg.edit_text(f"❌ **Error:** `{str(e)}`")
            if 'torrent_hash' in locals() and torrent_hash in active_downloads:
                del active_downloads[torrent_hash]
            
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_download(client, callback_query):
    """Handle cancel button callback"""
    gid = callback_query.data.split("_", 1)[1]
    
    if gid in active_downloads:
        task = active_downloads[gid]
        task.cancelled = True
        
        try:
            if task.engine == "aria2":
                download = aria2.get_download(gid)
                aria2.remove([download], force=True, files=True)
            elif task.engine == "qbit":
                qb = qBitClient(host=QBIT_HOST, port=QBIT_PORT)
                qb.auth_log_in()
                qb.torrents_delete(delete_files=True, torrent_hashes=gid)
        except Exception as e:
            print(f"Cancel error: {e}")
        
        await callback_query.answer("✅ Download cancelled!", show_alert=True)
    else:
        await callback_query.answer("❌ Download not found!", show_alert=True)

if __name__ == "__main__":
    print("🚀 Starting Leech Bot...")
    print("📋 Commands: /leech, /l, /qbleech, /qb")
    app.run()
