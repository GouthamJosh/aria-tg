import os

# ─────────────────────────────────────────────────────────────────────────────
#  Telegram API Credentials
#  Get from https://my.telegram.org/apps
# ─────────────────────────────────────────────────────────────────────────────
API_ID   = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID  = int(os.environ.get("OWNER_ID", "6108995220"))

# ─────────────────────────────────────────────────────────────────────────────
#  Aria2 RPC
# ─────────────────────────────────────────────────────────────────────────────
ARIA2_HOST   = os.environ.get("ARIA2_HOST", "http://localhost")
ARIA2_PORT   = int(os.environ.get("ARIA2_PORT", "6800"))
ARIA2_SECRET = os.environ.get("ARIA2_SECRET", "gjxml")

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/downloads")

# ─────────────────────────────────────────────────────────────────────────────
#  Upload Limits
#  Set OWNER_PREMIUM=true in env if your Telegram account is Premium (4GB limit)
# ─────────────────────────────────────────────────────────────────────────────
OWNER_PREMIUM    = os.environ.get("OWNER_PREMIUM", "false").lower() == "true"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024 if OWNER_PREMIUM else 2 * 1024 * 1024 * 1024
MAX_UPLOAD_LABEL = "4GB" if OWNER_PREMIUM else "2GB"

TASKS_PER_PAGE = int(os.environ.get("TASKS_PER_PAGE", "4"))

PORT = int(os.environ.get("PORT", "8000"))

# ─────────────────────────────────────────────────────────────────────────────
#  Engine Labels (display only)
# ─────────────────────────────────────────────────────────────────────────────
ENGINE_DL      = "ARIA2 v1.36.0"
ENGINE_UL      = "Pyro v2.2.18"
ENGINE_EXTRACT = "py7zr / zipfile"

# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard / FloodWait Settings
#
#  DASHBOARD_REFRESH_INTERVAL — how often (seconds) the shared dashboard
#    message auto-updates.  Keep >= 15 to stay safely under Telegram's
#    edit-rate limit.
#
#  MIN_EDIT_GAP — hard minimum seconds between any two edits to the same
#    dashboard message. Acts as a second safety gate.
#
#  These two settings together make FloodWait physically impossible.
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_REFRESH_INTERVAL = int(os.environ.get("DASHBOARD_REFRESH_INTERVAL", "15"))
MIN_EDIT_GAP               = int(os.environ.get("MIN_EDIT_GAP", "5"))

# ─────────────────────────────────────────────────────────────────────────────
#  Pyrogram Client Settings
# ─────────────────────────────────────────────────────────────────────────────
WORKERS                    = int(os.environ.get("WORKERS", "400"))
MAX_CONCURRENT_TRANSMISSIONS = int(os.environ.get("MAX_CONCURRENT_TRANSMISSIONS", "20"))

# ─────────────────────────────────────────────────────────────────────────────
#  Aria2 Torrent / BitTorrent Options
# ─────────────────────────────────────────────────────────────────────────────
TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce,"
    "udp://tracker.openbittorrent.com:6969/announce,"
    "http://tracker.openbittorrent.com:80/announce,"
    "udp://tracker.torrent.eu.org:451/announce,"
    "udp://exodus.desync.com:6969/announce,"
    "udp://tracker.cyberia.is:6969/announce,"
    "udp://open.demonii.com:1337/announce,"
    "udp://9.rarbg.com:2810/announce,"
    "udp://tracker.moeking.me:6969/announce,"
    "udp://tracker.lelux.fi:6969/announce,"
    "udp://retracker.lanta-net.ru:2710/announce,"
    "udp://opentor.net:2710/announce,"
    "udp://tracker.dler.org:6969/announce,"
    "udp://tracker.tiny-vps.com:6969/announce,"
    "https://tracker.tamersunion.org:443/announce,"
    "https://tracker.loligirl.cn:443/announce,"
    "udp://tracker.theoks.net:6969/announce,"
    "udp://tracker1.bt.moack.co.kr:80/announce,"
    "udp://open.stealth.si:80/announce,"
    "udp://tracker.zemoj.com:6969/announce"
)

BT_OPTIONS = {
    "dir": DOWNLOAD_DIR,
    "seed-time": "0",
    "disk-cache": "64M",
    "file-allocation": "none",
    "bt-max-peers": "200",
    "bt-request-peer-speed-limit": "50M",
    "max-connection-per-server": "16",
    "split": "16",
    "min-split-size": "1M",
    "enable-dht": "true",
    "enable-dht6": "true",
    "enable-peer-exchange": "true",
    "bt-enable-lpd": "true",
    "bt-prioritize-piece": "head=2M,tail=2M",
    "bt-remove-unselected-file": "true",
    "peer-agent": "aria2/1.36.0",
    "max-overall-download-limit": "0",
    "max-overall-upload-limit": "1K",
    "bt-tracker": TRACKERS,
}

DIRECT_OPTIONS = {
    "dir": DOWNLOAD_DIR,
    "disk-cache": "64M",
    "file-allocation": "none",
    "max-connection-per-server": "16",
    "split": "16",
    "min-split-size": "1M",
    "max-overall-download-limit": "0",
}
