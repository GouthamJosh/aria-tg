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
    --max-concurrent-downloads=5 \
    --max-connection-per-server=16 \
    --min-split-size=10M \
    --split=16 \
    --continue=true \
    --auto-file-renaming=false \
    --allow-overwrite=true \
    --disk-cache=64M \
    --file-allocation=none \
    --log-level=warn \
    --daemon=true \
    --dir=/tmp/downloads 2>/dev/null || true

echo "⏳ Waiting for RPC on port $RPC_PORT..."
sleep 3

# ── Start Bot ────────────────────────────────────────────────────────────────
echo "🤖 Starting Leech Bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec python3 bot.py
