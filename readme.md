# 🤖 Telegram Leech Bot

Advanced Telegram bot for downloading files, torrents, and auto-uploading to Telegram with detailed progress tracking.

## ✨ Features

### 📥 Download Features
- **Direct Downloads** via aria2 (HTTP/HTTPS/FTP)
- **Torrent Downloads** via qBittorrent (magnets & .torrent files)
- **Auto Extraction** (.zip, .7z, .tar.gz, .tgz, .tar)
- **Progress Tracking** with detailed UI (similar to professional leech bots)
- **Speed Monitoring** in MB/s
- **ETA Calculation** with time remaining
- **Elapsed Time** tracking

### 📊 Status Display
```
Task By @username ( #123456789 ) [Link]
├ [●●●●●●●●○○] 81.99%
├ Processed → 11.72GB of 14.29GB
├ Status → Download
├ Speed → 3.31MB/s
├ Time → 13m17s ( 1h27m27s )
├ Engine → ARIA2 v2.2.18
├ In Mode → #aria2
├ Out Mode → #Leech (Zip)
└ Stop → /stop_abc12345

📊 Bot Stats
├ CPU → 99.8% | RAM → 232.89GB [66.3%]
└ UP → 3h19m28s
```

### 🎯 Features
- **No Cancel Button** - Uses `/stop` command for cleaner UI
- **Auto Cleanup** - Automatically deletes local files after upload
- **System Monitoring** - Real-time CPU/RAM usage
- **Professional UI** - Clean status display similar to premium bots

## 🎯 Commands

### Download Commands
```bash
/leech <link>              # Download direct link
/l <link>                  # Short for /leech
/leech <link> -e           # Download and extract archive
```

### Torrent Commands
```bash
/qbleech <magnet/torrent>  # Download torrent
/qb <link>                 # Short for /qbleech
/qb <link> -e              # Download torrent and extract
```

### Control Commands
```bash
/stop <task_id>            # Stop/cancel download
/stop_abc12345             # Stop using short ID
```

### Info Commands
```bash
/start                     # Start message
/help                      # Show help
```

## 🚀 Deployment

### Prerequisites
1. Telegram Bot Token from [@BotFather](https://t.me/BotFather)
2. Telegram API ID & Hash from [my.telegram.org](https://my.telegram.org)
3. Koyeb or Render account

### Environment Variables
```env
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

### Deploy to Koyeb

1. Fork this repository
2. Create new service on Koyeb → Deploy from GitHub
3. Add environment variables (API_ID, API_HASH, BOT_TOKEN)
4. Deploy!

### Deploy to Render

1. Create new Web Service → Connect GitHub
2. Environment: Docker, Plan: Free
3. Add environment variables
4. Deploy!

## 📝 Examples

### Download ZIP file
```
/leech https://example.com/file.zip
```

### Download and extract
```
/l https://example.com/archive.7z -e
```

### Download torrent
```
/qbleech magnet:?xt=urn:btih:1234567890abcdef
```

### Stop download
```
/stop abc12345
```

## 📊 File Format Support

- **Direct Downloads**: HTTP/HTTPS/FTP
- **Torrents**: Magnet links, .torrent files
- **Archives**: .zip, .7z, .tar, .tar.gz, .tgz

## 🛡️ Safety Features

- Auto cleanup after upload
- Cleanup on cancel/error
- 2GB file size limit
- Error handling

---

**Made with ❤️ for the Telegram community**
