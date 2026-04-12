import os

# ─────────────────────────────────────────────────────────────────────────────
#  Telegram API Credentials
#  Get from https://my.telegram.org/apps
# ─────────────────────────────────────────────────────────────────────────────
API_ID   = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID  = int(os.environ.get("OWNER_ID", "6108995220"))

# ── Database ──────────────────────────────────────────────────────────────────
# MongoDB connection string for storing user dump channels
MONGO_URL = os.environ.get("MONGO_URL", "")

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
ENGINE_DL      = "ARIA2 v1.37.0"
ENGINE_UL      = "Pyrofork"
ENGINE_EXTRACT = "py7zr / zipfile"

# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard / FloodWait Settings
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_REFRESH_INTERVAL = int(os.environ.get("DASHBOARD_REFRESH_INTERVAL", "5"))
MIN_EDIT_GAP               = int(os.environ.get("MIN_EDIT_GAP", "5"))

# ─────────────────────────────────────────────────────────────────────────────
#  Pyrogram Client Settings
# ─────────────────────────────────────────────────────────────────────────────
WORKERS                      = int(os.environ.get("WORKERS", "1000"))
MAX_CONCURRENT_TRANSMISSIONS = int(os.environ.get("MAX_CONCURRENT_TRANSMISSIONS", "50"))

# ─────────────────────────────────────────────────────────────────────────────
#  Upload Speed Tuning
# ─────────────────────────────────────────────────────────────────────────────
# Max files uploaded in parallel when uploading a directory
UPLOAD_PARALLEL_FILES = int(os.environ.get("UPLOAD_PARALLEL_FILES", "8"))

# ─────────────────────────────────────────────────────────────────────────────
#  Trackers — 38 trackers across 3 tiers
# ─────────────────────────────────────────────────────────────────────────────
TRACKERS = (
    # ── Tier 1 — high-reliability UDP ─────────────────────────────────────
    "udp://tracker.opentrackr.org:1337/announce,"
    "udp://open.tracker.cl:1337/announce,"
    "udp://open.demonii.com:1337/announce,"
    "udp://exodus.desync.com:6969/announce,"
    "udp://open.stealth.si:80/announce,"
    "udp://tracker.torrent.eu.org:451/announce,"
    "udp://tracker.openbittorrent.com:6969/announce,"
    "http://tracker.openbittorrent.com:80/announce,"
    "udp://tracker.moeking.me:6969/announce,"
    "udp://tracker.dler.org:6969/announce,"
    # ── Tier 2 — solid secondary UDP ──────────────────────────────────────
    "udp://9.rarbg.com:2810/announce,"
    "udp://tracker.tiny-vps.com:6969/announce,"
    "udp://tracker.cyberia.is:6969/announce,"
    "udp://opentor.net:2710/announce,"
    "udp://tracker.theoks.net:6969/announce,"
    "udp://tracker1.bt.moack.co.kr:80/announce,"
    "udp://tracker.zemoj.com:6969/announce,"
    "udp://tracker.lelux.fi:6969/announce,"
    "udp://retracker.lanta-net.ru:2710/announce,"
    "udp://bt1.archive.org:6969/announce,"
    "udp://bt2.archive.org:6969/announce,"
    "udp://tracker.uw0.xyz:6969/announce,"
    "udp://tracker.coppersurfer.tk:6969/announce,"
    "udp://tracker.leechers-paradise.org:6969/announce,"
    "udp://tracker.pirateparty.gr:6969/announce,"
    "udp://ipv4.tracker.harry.lu:80/announce,"
    "udp://tracker.internetwarriors.net:1337/announce,"
    "udp://tracker.zer0day.to:1337/announce,"
    "udp://tracker.mg64.net:6969/announce,"
    "udp://peerfect.org:6969/announce,"
    # ── Tier 3 — HTTPS trackers ────────────────────────────────────────────
    "https://tracker.tamersunion.org:443/announce,"
    "https://tracker.loligirl.cn:443/announce,"
    "https://tracker.gbitt.info:443/announce,"
    "https://1337.abcvg.info:443/announce,"
    "https://tr.burnbit.com:443/announce,"
    "http://tracker.gbitt.info:80/announce,"
    "http://open.acgnxtracker.com:80/announce,"
    "http://tracker.bt4g.com:2095/announce"
)

# ─────────────────────────────────────────────────────────────────────────────
#  Aria2 BitTorrent / Magnet Options  (tuned for max speed)
# ─────────────────────────────────────────────────────────────────────────────
BT_OPTIONS = {
    "dir":                           DOWNLOAD_DIR,
    # ── Seeding ────────────────────────────────────────────────────────────
    "seed-time":                     "0",
    "seed-ratio":                    "0.0",
    # ── I/O performance ────────────────────────────────────────────────────
    "disk-cache":                    "128M",
    "file-allocation":               "none",
    "async-dns":                     "true",
    # ── Connections ────────────────────────────────────────────────────────
    "max-connection-per-server":     "16",
    "split":                         "16",
    "min-split-size":                "1M",
    "max-concurrent-downloads":      "5",
    # ── BitTorrent peer settings ───────────────────────────────────────────
    "bt-max-peers":                  "500",
    "bt-request-peer-speed-limit":   "100M",
    "bt-prioritize-piece":           "head=4M,tail=4M",
    "bt-remove-unselected-file":     "true",
    "bt-save-metadata":              "true",
    "bt-load-saved-metadata":        "true",
    "bt-hash-check-seed":            "false",
    # ── DHT / peer discovery ───────────────────────────────────────────────
    "enable-dht":                    "true",
    "enable-dht6":                   "true",
    "dht-listen-port":               "6881-6999",
    "enable-peer-exchange":          "true",
    "bt-enable-lpd":                 "true",
    # ── Limits ─────────────────────────────────────────────────────────────
    "max-overall-download-limit":    "0",
    "max-overall-upload-limit":      "1K",
    # ── Peer identity ──────────────────────────────────────────────────────
    "peer-agent":                    "aria2/1.37.0",
    "peer-id-prefix":                "-AR1370-",
    # ── Trackers ───────────────────────────────────────────────────────────
    "bt-tracker":                    TRACKERS,
}

# ─────────────────────────────────────────────────────────────────────────────
#  Aria2 Direct Download Options
# ─────────────────────────────────────────────────────────────────────────────
DIRECT_OPTIONS = {
    "dir":                        DOWNLOAD_DIR,
    "disk-cache":                 "128M",
    "file-allocation":            "none",
    "max-connection-per-server":  "16",
    "split":                      "16",
    "min-split-size":             "1M",
    "max-overall-download-limit": "0",
    "async-dns":                  "true",
}
