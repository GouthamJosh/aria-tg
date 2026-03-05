#!/bin/sh
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖  Leech Bot — Universal Startup Script"
echo "  Supports: Koyeb · Render · Railway · JRMA"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Create download directory ─────────────────────────────────────────────────
mkdir -p /tmp/downloads

# ── Detect environment ───────────────────────────────────────────────────────
ARCH=$(uname -m)
OS=$(uname -s)
echo "🖥️  Architecture: $ARCH | OS: $OS"

# ── Portable command check ───────────────────────────────────────────────────
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# ── Install aria2c (multi-method fallback) ───────────────────────────────────
install_aria2() {
    echo "⚠️  aria2c not found. Installing..."

    # Method 1: apt-get
    if command_exists apt-get; then
        echo "📦 Trying apt-get..."
        apt-get update -qq 2>/dev/null && apt-get install -y -qq aria2 2>/dev/null && {
            echo "✅ Installed via apt-get"
            return 0
        }
    fi

    # Method 2: apk
    if command_exists apk; then
        echo "📦 Trying apk..."
        apk add --no-cache aria2 2>/dev/null && {
            echo "✅ Installed via apk"
            return 0
        }
    fi

    # Method 3: yum/dnf
    if command_exists yum; then
        echo "📦 Trying yum..."
        yum install -y aria2 2>/dev/null && {
            echo "✅ Installed via yum"
            return 0
        }
    fi

    if command_exists dnf; then
        echo "📦 Trying dnf..."
        dnf install -y aria2 2>/dev/null && {
            echo "✅ Installed via dnf"
            return 0
        }
    fi

    # Method 4: pacman
    if command_exists pacman; then
        echo "📦 Trying pacman..."
        pacman -Sy --noconfirm aria2 2>/dev/null && {
            echo "✅ Installed via pacman"
            return 0
        }
    fi

    # Method 5: Static binary fallback
    echo "📦 Trying static binary..."
    ARIA2_VER="1.37.0"
    mkdir -p /tmp/aria2

    case "$ARCH" in
        x86_64|amd64)
            URL="https://github.com/q3aql/aria2-static-builds/releases/download/v${ARIA2_VER}/aria2-${ARIA2_VER}-linux-gnu-64bit-build1.tar.bz2"
            ;;
        aarch64|arm64|armv7l|armhf)
            URL="https://github.com/q3aql/aria2-static-builds/releases/download/v${ARIA2_VER}/aria2-${ARIA2_VER}-linux-gnu-arm-rbpi-build1.tar.bz2"
            ;;
        i386|i686)
            URL="https://github.com/q3aql/aria2-static-builds/releases/download/v${ARIA2_VER}/aria2-${ARIA2_VER}-linux-gnu-32bit-build1.tar.bz2"
            ;;
        *)
            echo "❌ Unsupported architecture: $ARCH"
            return 1
            ;;
    esac

    if command_exists curl; then
        curl -fsSL "$URL" -o /tmp/aria2.tar.bz2 2>/dev/null
    elif command_exists wget; then
        wget -q "$URL" -O /tmp/aria2.tar.bz2 2>/dev/null
    else
        echo "❌ No curl or wget available"
        return 1
    fi

    if [ -f /tmp/aria2.tar.bz2 ]; then
        tar -xjf /tmp/aria2.tar.bz2 -C /tmp/aria2 2>/dev/null
        BINARY=$(find /tmp/aria2 -name "aria2c" -type f 2>/dev/null | head -n1)

        if [ -n "$BINARY" ]; then
            if [ -w /usr/local/bin ]; then
                cp "$BINARY" /usr/local/bin/aria2c
                chmod +x /usr/local/bin/aria2c
            else
                cp "$BINARY" /tmp/aria2c
                chmod +x /tmp/aria2c
                export PATH="/tmp:$PATH"
            fi
            rm -rf /tmp/aria2 /tmp/aria2.tar.bz2
            echo "✅ Installed static binary"
            return 0
        fi
    fi

    echo "❌ All install methods failed for $ARCH"
    return 1
}

# ── Ensure aria2c exists ─────────────────────────────────────────────────────
if ! command_exists aria2c; then
    install_aria2 || exit 1
fi

echo "🔍 Verifying aria2c..."
if ! aria2c --version >/dev/null 2>&1; then
    echo "❌ aria2c broken, reinstalling..."
    rm -f /usr/local/bin/aria2c /tmp/aria2c
    install_aria2 || exit 1
fi

echo "✅ aria2c: $(aria2c --version 2>/dev/null | head -n1 | awk '{print $3}')"

# ── Install Python Requirements ──────────────────────────────────────────────
echo "📦 Installing Python requirements..."

install_requirements() {
    if ! command_exists python3; then
        echo "❌ python3 not found"
        exit 1
    fi

    if command_exists pip3; then
        PIP_CMD="pip3"
    elif command_exists pip; then
        PIP_CMD="pip"
    else
        echo "⚠️ pip not found, attempting ensurepip..."
        python3 -m ensurepip --upgrade 2>/dev/null || true
        PIP_CMD="python3 -m pip"
    fi

    $PIP_CMD install --upgrade pip setuptools wheel --quiet 2>/dev/null || true

    if [ -f requirements.txt ]; then
        $PIP_CMD install -r requirements.txt --no-cache-dir --quiet || {
            echo "❌ Failed to install requirements"
            exit 1
        }
        echo "✅ Requirements installed"
    else
        echo "⚠️ requirements.txt not found, skipping..."
    fi
}

install_requirements

# ── Build tracker list ───────────────────────────────────────────────────────
# Tier 1 — high-reliability UDP
TRACKERS="udp://tracker.opentrackr.org:1337/announce"
TRACKERS="$TRACKERS,udp://open.tracker.cl:1337/announce"
TRACKERS="$TRACKERS,udp://open.demonii.com:1337/announce"
TRACKERS="$TRACKERS,udp://exodus.desync.com:6969/announce"
TRACKERS="$TRACKERS,udp://open.stealth.si:80/announce"
TRACKERS="$TRACKERS,udp://tracker.torrent.eu.org:451/announce"
TRACKERS="$TRACKERS,udp://tracker.openbittorrent.com:6969/announce"
TRACKERS="$TRACKERS,http://tracker.openbittorrent.com:80/announce"
TRACKERS="$TRACKERS,udp://tracker.moeking.me:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.dler.org:6969/announce"
# Tier 2 — solid secondary UDP
TRACKERS="$TRACKERS,udp://9.rarbg.com:2810/announce"
TRACKERS="$TRACKERS,udp://tracker.tiny-vps.com:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.cyberia.is:6969/announce"
TRACKERS="$TRACKERS,udp://opentor.net:2710/announce"
TRACKERS="$TRACKERS,udp://tracker.theoks.net:6969/announce"
TRACKERS="$TRACKERS,udp://tracker1.bt.moack.co.kr:80/announce"
TRACKERS="$TRACKERS,udp://tracker.zemoj.com:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.lelux.fi:6969/announce"
TRACKERS="$TRACKERS,udp://retracker.lanta-net.ru:2710/announce"
TRACKERS="$TRACKERS,udp://bt1.archive.org:6969/announce"
TRACKERS="$TRACKERS,udp://bt2.archive.org:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.uw0.xyz:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.coppersurfer.tk:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.leechers-paradise.org:6969/announce"
TRACKERS="$TRACKERS,udp://tracker.pirateparty.gr:6969/announce"
TRACKERS="$TRACKERS,udp://ipv4.tracker.harry.lu:80/announce"
TRACKERS="$TRACKERS,udp://tracker.internetwarriors.net:1337/announce"
TRACKERS="$TRACKERS,udp://tracker.zer0day.to:1337/announce"
TRACKERS="$TRACKERS,udp://tracker.mg64.net:6969/announce"
TRACKERS="$TRACKERS,udp://peerfect.org:6969/announce"
# Tier 3 — HTTPS
TRACKERS="$TRACKERS,https://tracker.tamersunion.org:443/announce"
TRACKERS="$TRACKERS,https://tracker.loligirl.cn:443/announce"
TRACKERS="$TRACKERS,https://tracker.gbitt.info:443/announce"
TRACKERS="$TRACKERS,https://1337.abcvg.info:443/announce"
TRACKERS="$TRACKERS,https://tr.burnbit.com:443/announce"
TRACKERS="$TRACKERS,http://tracker.gbitt.info:80/announce"
TRACKERS="$TRACKERS,http://open.acgnxtracker.com:80/announce"
TRACKERS="$TRACKERS,http://tracker.bt4g.com:2095/announce"

# ── Start Aria2c RPC ─────────────────────────────────────────────────────────
echo "🚀 Starting Aria2c RPC daemon..."

pkill -f "aria2c.*rpc-listen-port=6800" 2>/dev/null || true
sleep 1

ARIA2_SECRET="${ARIA2_SECRET:-gjxml}"
RPC_PORT="${ARIA2_PORT:-6800}"

aria2c \
    --enable-rpc \
    --rpc-listen-all=false \
    --rpc-listen-port="$RPC_PORT" \
    --rpc-secret="$ARIA2_SECRET" \
    --rpc-max-request-size=16M \
    --rpc-allow-origin-all=true \
    --dir=/tmp/downloads \
    \
    `# ── General Download ────────────────────────────────` \
    --max-concurrent-downloads=5 \
    --max-connection-per-server=16 \
    --min-split-size=1M \
    --split=16 \
    --continue=true \
    --auto-file-renaming=false \
    --allow-overwrite=true \
    --async-dns=true \
    --disk-cache=128M \
    --file-allocation=none \
    --max-overall-download-limit=0 \
    --max-overall-upload-limit=1K \
    \
    `# ── Torrent / Magnet ────────────────────────────────` \
    --enable-dht=true \
    --enable-dht6=true \
    --dht-listen-port=6881-6999 \
    --enable-peer-exchange=true \
    --bt-enable-lpd=true \
    --bt-max-peers=500 \
    --bt-request-peer-speed-limit=100M \
    --bt-save-metadata=true \
    --bt-load-saved-metadata=true \
    --bt-hash-check-seed=false \
    --bt-seed-unverified=true \
    --bt-prioritize-piece=head=4M,tail=4M \
    --bt-remove-unselected-file=true \
    --seed-time=0 \
    --seed-ratio=0.0 \
    --follow-torrent=true \
    --peer-agent="aria2/1.37.0" \
    --peer-id-prefix="-AR1370-" \
    --bt-tracker="$TRACKERS" \
    \
    `# ── Logging ─────────────────────────────────────────` \
    --log-level=warn \
    --daemon=true \
    2>/dev/null || true

echo "⏳ Waiting for RPC on port $RPC_PORT..."
sleep 3

# ── Verify RPC is up ─────────────────────────────────────────────────────────
if command_exists curl; then
    ARIA2_STATUS=$(curl -s --max-time 3 \
        -d '{"jsonrpc":"2.0","id":"check","method":"aria2.getVersion","params":["token:'"$ARIA2_SECRET"'"]}' \
        http://localhost:"$RPC_PORT"/jsonrpc 2>/dev/null | grep -o '"version"' || true)
    if [ -n "$ARIA2_STATUS" ]; then
        echo "✅ Aria2c RPC is live on port $RPC_PORT"
    else
        echo "⚠️  Aria2c RPC check inconclusive — proceeding anyway"
    fi
fi

# ── Start Bot ────────────────────────────────────────────────────────────────
echo "🤖 Starting Leech Bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec python3 bot.py
