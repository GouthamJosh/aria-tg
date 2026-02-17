# 🤖 Leech Bot

A Telegram bot that downloads files via direct links using **Aria2**, then uploads them straight to Telegram — with live progress tracking on every phase.

> **Author:** [GouthamSER](https://github.com/GouthamSER)

---

## ✨ Features

- **Direct link downloads** — HTTP, HTTPS, FTP
- **Auto extraction** — `.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`
- **Live progress UI** on all three phases:
  - 📥 Download — filename, speed, ETA, progress bar
  - 📦 Extract — per-file counter, processed size, speed
  - 📤 Upload — concurrent multi-file uploads with shared bandwidth
- **Concurrent uploads** — multiple files upload simultaneously, not one by one
- **Telegram Premium support** — 4 GB upload limit if owner has Premium, 2 GB otherwise
- **Cancel anytime** — `/stop` cleans up files instantly
- **System stats** — CPU %, RAM usage, disk free space, and bot uptime on every message
- **Auto cleanup** — all temporary files deleted after upload completes
- **Koyeb ready** — built-in aiohttp keep-alive web server so Koyeb never shuts the service down

---

## 📋 Commands

| Command | Description |
|---|---|
| `/leech <url>` | Download a direct link and upload to Telegram |
| `/l <url>` | Shorthand for `/leech` |
| `/leech <url> -e` | Download and extract archive before uploading |
| `/l <url> -e` | Shorthand for extract mode |
| `/stop <task_id>` | Cancel a running task and clean up files |
| `/stop_<task_id>` | Inline cancel (shown in the progress message) |
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
aiohttp
```

---

## ⚙️ Configuration

Set the following environment variables before running:

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | Telegram API ID — [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | Telegram API Hash — [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | ✅ | Bot token — [@BotFather](https://t.me/BotFather) |
| `OWNER_ID` | ✅ | Your Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot)) |
| `OWNER_PREMIUM` | ⚙️ | Set to `true` if you have Telegram Premium → enables 4 GB uploads (default: `false` = 2 GB) |
| `PORT` | ⚙️ | Port for the keep-alive web server (default: `8000`, Koyeb sets this automatically) |

### `.env` example

```env
API_ID=123456
API_HASH=abcdef1234567890abcdef1234567890
BOT_TOKEN=123456789:AABBCCDDEEFFaabbccddeeff
OWNER_ID=987654321
OWNER_PREMIUM=true
PORT=8000
```

---

## 📤 Upload Size Limit

The bot automatically picks the right limit based on the `OWNER_PREMIUM` flag:

| `OWNER_PREMIUM` | Max file size |
|---|---|
| `false` (default) | **2 GB** — standard Telegram Bot API limit |
| `true` | **4 GB** — Telegram Premium limit |

> ⚠️ The **bot account itself does not need Premium** — only the owner/admin receiving the files needs a Premium account for 4 GB uploads to work.

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

---

## ☁️ Deploying on Koyeb

The bot includes a built-in **aiohttp web server** that runs alongside the bot. Koyeb requires every service to expose an HTTP endpoint — this server satisfies that requirement and prevents the service from being killed.

### Health check endpoints

| Endpoint | Response |
|---|---|
| `GET /` | Bot status, active downloads, upload limit |
| `GET /health` | Same as above |

### Steps

1. Push your code to GitHub
2. Create a new **Koyeb** service → select your repo
3. Set **Run command**: `python3 leech_bot.py`
4. Set **Port**: `8000` (or leave blank — Koyeb injects `$PORT` automatically)
5. Add all environment variables in the Koyeb dashboard
6. Add a **Health check** pointing to `/health`
7. Deploy 🚀

### `Procfile` (optional)

```
web: aria2c --enable-rpc --rpc-listen-port=6800 --daemon && python3 leech_bot.py
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
├ Disk → 42.50GB free of 100.00GB [57.5% used]
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

📊 Bot Stats
├ CPU → 22.10% | RAM → 1.80GB [66.2%]
├ Disk → 38.20GB free of 100.00GB [61.8% used]
└ UP → 3h13m44s
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
├ Disk → 40.00GB free of 100.00GB [60.0% used]
└ UP → 3h14m22s
```

---

## 📁 Project Structure

```
leechbot/
├── leech_bot.py      # Main bot file
├── requirements.txt  # Python dependencies
├── Procfile          # Koyeb / Heroku process file (optional)
├── README.md         # This file
└── .env              # Environment variables (never commit this)
```

---

## ⚠️ Limitations

- Files larger than the configured limit (2 GB / 4 GB) are skipped with an error message
- Aria2 must be running as an RPC daemon **before** starting the bot
- Bot stores temporary files in `/tmp/downloads` — ensure enough disk space for your downloads

---

## 📝 License

MIT License — free to use, modify, and distribute.

---

<div align="center">
  Made with ❤️ by <a href="https://github.com/GouthamSER">GouthamSER</a>
</div>
