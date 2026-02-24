# 🤖 Leech Bot

A Telegram bot that downloads files via **Aria2c RPC**, then uploads them directly to Telegram — with live progress UI for every stage.

---

## ✨ Features

- 📥 Download any direct HTTP/HTTPS/FTP link via Aria2c
- 📤 Upload to Telegram with live speed & progress bar
- 📦 Auto-extract `.zip` `.7z` `.tar.gz` archives
- 🧹 Auto-cleanup after upload
- 📊 Live CPU / RAM / Disk stats in progress messages
- 🚫 Site-name prefix auto-stripped from filenames (e.g. `www.site.com - Movie.mkv` → `Movie.mkv`)
- 🛡️ FloodWait protection & rate-limited message edits
- ⚡ uvloop + TgCrypto for maximum speed
- 🌐 Built-in keep-alive web server (Koyeb / Render / Railway ready)

---

## 📁 File Structure

```
├── bot.py            # Main bot code
├── start.sh          # Universal startup script (installs aria2c if missing)
├── Dockerfile        # Docker image (recommended for all platforms)
├── requirements.txt  # Python dependencies
└── README.md
```

---

## ⚙️ Environment Variables

Set these in your platform's dashboard or `.env` file:

| Variable | Required | Description | Example |
|---|---|---|---|
| `API_ID` | ✅ | Telegram API ID from [my.telegram.org](https://my.telegram.org) | `12345678` |
| `API_HASH` | ✅ | Telegram API Hash | `abc123...` |
| `BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) | `123:ABC...` |
| `OWNER_ID` | ✅ | Your Telegram user ID | `6108995220` |
| `ARIA2_SECRET` | ⚠️ | Aria2c RPC secret (default: `gjxml`) | `mysecret` |
| `OWNER_PREMIUM` | ❌ | Set `true` for 4GB upload limit | `false` |
| `PORT` | ❌ | Keep-alive web server port (default: `8000`) | `8000` |

> Get your user ID from [@userinfobot](https://t.me/userinfobot)
> Get `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org) → API Development Tools

---

## 🚀 Deploy

### 🐳 Docker (Recommended — Koyeb / Render / Railway)

```bash
# Build
docker build -t leech-bot .

# Run
docker run -d \
  -e API_ID=your_api_id \
  -e API_HASH=your_api_hash \
  -e BOT_TOKEN=your_bot_token \
  -e OWNER_ID=your_user_id \
  -e ARIA2_SECRET=gjxml \
  -p 8000:8000 \
  leech-bot
```

---

### ☁️ Koyeb

1. Push this repo to GitHub
2. Go to [koyeb.com](https://koyeb.com) → **Create Service** → **GitHub**
3. Select your repo — Koyeb auto-detects the `Dockerfile`
4. Add environment variables in the **Environment** tab
5. Set **Port** to `8000`
6. Deploy ✅

---

### ☁️ Render

1. Go to [render.com](https://render.com) → **New Web Service**
2. Connect your GitHub repo
3. Set **Runtime** to `Docker`
4. Add environment variables under **Environment**
5. Set **Port** to `8000`
6. Deploy ✅

---

### ☁️ Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Select your repo — Railway auto-detects the `Dockerfile`
3. Go to **Variables** and add all environment variables
4. Deploy ✅

---

### ☁️ JustRunMyApp / No-Docker Platforms

If the platform ignores the Dockerfile and runs `start.sh` on a bare Python runtime, the `start.sh` will auto-install `aria2c` using one of these fallback methods:

1. `apt-get install aria2` (Debian/Ubuntu base)
2. `apk add aria2` (Alpine base)
3. Static binary download (x86_64 / ARM64)
4. `pip install aria2` (bundled binary, no root needed)

Make sure `start.sh` is set as the **Run Command** in your platform settings.

---

## 💬 Bot Commands

| Command | Description |
|---|---|
| `/start` or `/help` | Show help message |
| `/leech <url>` | Download & upload a file |
| `/l <url>` | Shorthand for `/leech` |
| `/leech <url> -e` | Download & extract archive, then upload |
| `/stop_<id>` | Cancel a running task |

### Examples

```
/leech https://example.com/movie.mkv
/l https://example.com/archive.zip
/leech https://example.com/files.7z -e
```

---

## 📦 Dependencies

```
pyrofork       # Telegram client (Pyrogram fork)
TgCrypto       # Fast MTProto crypto (required for speed)
aria2p         # Aria2c RPC interface
aiohttp        # Async HTTP + keep-alive web server
py7zr          # 7z extraction
psutil         # System stats (CPU/RAM/Disk)
uvloop         # Fast async event loop (optional but recommended)
```

Install:
```bash
pip install -r requirements.txt
```

---

## 🔧 Local Development

```bash
# 1. Clone the repo
git clone https://github.com/yourname/leech-bot
cd leech-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start aria2c manually
aria2c --enable-rpc --rpc-secret=gjxml --daemon=true

# 4. Set environment variables
export API_ID=your_api_id
export API_HASH=your_api_hash
export BOT_TOKEN=your_bot_token
export OWNER_ID=your_user_id

# 5. Run the bot
python3 bot.py
```

---

## 📊 Progress UI Preview

**Downloading:**
```
The.Movie.2025.mkv

Task By @username ( #ID123456 ) [Link]
├ [●●●●●●●○○○] 72.3%
├ Processed → 1.96GB of 2.72GB
├ Status → Download
├ Speed → 5.66MB/s
├ Time → 1m56s of 21m2s ( 19m6s )
├ Seeders → 36 | Leechers → 46
├ Engine → ARIA2 v1.36.0
├ In Mode → #ARIA2
├ Out Mode → #Leech
└ Stop → /stop_c2_6dd4

© Bot Stats
├ CPU → 100.0% | F → 245.37GB [69.9%]
└ RAM → 58.4% | UP → 10h44m34s
```

**Uploading:**
```
The.Movie.2025.mkv

Task By @username ( #ID123456 ) [Link]
├ [●●●●●●●●●●] 100.0%
├ Processed → 2.14GB of 2.14GB
├ Status → Upload
├ Speed → 595.40KB/s
├ Time → of 1h3m41s ( 1h3m41s )
├ Engine → Pyro v2.2.18
├ In Mode → #Aria2
├ Out Mode → #Leech
└ Stop → /stop_c1_a0fa

© Bot Stats
├ CPU → 12.0% | F → 245.37GB [69.9%]
└ RAM → 45.2% | UP → 10h44m34s
```

---

## 📝 License

MIT — free to use and modify.
