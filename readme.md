# 🤖 Leech Bot

A Telegram bot that downloads files via direct links using **Aria2**, then uploads them straight to Telegram — with live progress tracking on every phase.

---

## ✨ Features

- **Direct link downloads** — HTTP, HTTPS, FTP
- **Auto extraction** — `.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`
- **Live progress UI** on all three phases:
  - 📥 Download — filename, speed, ETA, progress bar
  - 📦 Extract — per-file counter, processed size, speed
  - 📤 Upload — concurrent multi-file uploads with shared bandwidth
- **Concurrent uploads** — multiple files upload simultaneously, not one by one
- **Cancel anytime** — `/stop` cleans up files instantly
- **System stats** — CPU %, RAM usage, and bot uptime on every status message
- **Auto cleanup** — all temporary files deleted after upload completes

---

## 📋 Commands

| Command | Description |
|---|---|
| `/leech <url>` | Download a direct link and upload to Telegram |
| `/l <url>` | Shorthand for `/leech` |
| `/leech <url> -e` | Download and extract archive before uploading |
| `/l <url> -e` | Shorthand for extract mode |
| `/stop <task_id>` | Cancel a running task and clean up files |
| `/stop_<task_id>` | Inline cancel (shown in progress message) |
| `/start` or `/help` | Show help message |

---

## 📦 Requirements

### System Dependencies

- **Python 3.10+**
- **Aria2** — download engine

```bash
# Ubuntu / Debian
sudo apt install aria2

# Start aria2 as RPC daemon
aria2c --enable-rpc --rpc-listen-all=false --rpc-listen-port=6800 --daemon
```

### Python Dependencies

```bash
pip install -r requirements.txt
```

**`requirements.txt`**
```
pyrogram
tgcrypto
aria2p
py7zr
psutil
```

---

## ⚙️ Configuration

Set the following environment variables before running:

| Variable | Description | Where to get |
|---|---|---|
| `API_ID` | Telegram API ID | [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Telegram API Hash | [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | Bot token | [@BotFather](https://t.me/BotFather) |

### Set environment variables

```bash
export API_ID=your_api_id
export API_HASH=your_api_hash
export BOT_TOKEN=your_bot_token
```

Or create a `.env` file and load it:

```env
API_ID=123456
API_HASH=abcdef1234567890abcdef1234567890
BOT_TOKEN=123456789:AABBCCDDEEFFaabbccddeeff
```

---

## 🚀 Running the Bot

### 1. Start Aria2 RPC daemon

```bash
aria2c --enable-rpc \
       --rpc-listen-all=false \
       --rpc-listen-port=6800 \
       --rpc-secret="" \
       --dir=/tmp/downloads \
       --daemon
```

### 2. Run the bot

```bash
python3 leech_bot.py
```

### Run with systemd (optional)

Create `/etc/systemd/system/leechbot.service`:

```ini
[Unit]
Description=Leech Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/leechbot
EnvironmentFile=/home/ubuntu/leechbot/.env
ExecStartPre=aria2c --enable-rpc --rpc-listen-port=6800 --daemon
ExecStart=python3 leech_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable leechbot
sudo systemctl start leechbot
```

---

## 📊 Progress UI Examples

### Downloading
```
Task By @username ( #123456789 ) [Link]
├ File → big_file.zip
├ [●●●●●○○○○○] 51.3%
├ Processed → 1.23GB of 2.40GB
├ Status → Download
├ Speed → 12.50MB/s
├ Time → 0:01:42 ( 1m38s )
├ Engine → ARIA2 v2.2.18
├ In Mode → #aria2
├ Out Mode → #Leech
└ Stop → /stop_a1b2c3d4

📊 Bot Stats
├ CPU → 8.20% | RAM → 1.40GB [52.3%]
└ UP → 3h12m5s
```

### Extracting
```
Task By @username ( #123456789 ) [Link]
├ File → chapter_01.cbz
├ Files → 14/47
├ [●●●○○○○○○○] 29.8%
├ Processed → 320.00MB of 1.07GB
├ Status → Extracting
├ Speed → 280.00MB/s
├ Time → 1s ( 2s )
├ Archive → big_file.zip
└ Archive Size → 1.20GB
```

### Uploading (concurrent)
```
Task By @username ( #123456789 ) [Link]
├ Overall [●●●●●○○○○○] 48.2%
├ Processed → 1.10GB of 2.28GB
├ Status → Uploading (3 files simultaneously)
├ `movie.mkv`  900MB/1.50GB  [●●●●●●○○○○] 60.0%
├ `subs.zip`   180MB/360MB   [●●●●●○○○○○] 50.0%
├ `info.nfo`   4MB/4MB       [●●●●●●●●●●] 100%

📊 Bot Stats
├ CPU → 15.10% | RAM → 1.60GB [59.8%]
└ UP → 3h14m22s
```

---

## 📁 Project Structure

```
aria-tg/
├── bot.py      # Main bot file
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── .env              # Environment variables (never commit this)
```

---

## ⚠️ Limitations

- Max file size: **2GB** (Telegram Bot API limit)
- Files larger than 2GB are skipped with an error message
- Aria2 must be running as an RPC daemon before starting the bot
- The bot stores temporary files in `/tmp/downloads` — ensure enough disk space

---

## 📝 License

MIT License — free to use, modify, and distribute.
