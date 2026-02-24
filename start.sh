#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖  Leech Bot — Starting Up"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Create download directory ──────────────────────────────────────────────────
mkdir -p /tmp/downloads

# ── Start Aria2c RPC daemon ────────────────────────────────────────────────────
echo "🚀 Starting Aria2c RPC daemon..."
aria2c \
    --enable-rpc \
    --rpc-listen-all=false \
    --rpc-listen-port=6800 \
    --rpc-secret="${ARIA2_SECRET:-gjxml}" \
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
    --daemon=true

# ── Wait for RPC to be ready ───────────────────────────────────────────────────
echo "⏳ Waiting for Aria2c RPC..."
for i in $(seq 1 10); do
    if curl -sf http://localhost:6800/jsonrpc > /dev/null 2>&1; then
        echo "✅ Aria2c RPC is ready!"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "❌ Aria2c failed to start after 10 attempts. Exiting."
        exit 1
    fi
    echo "   Attempt $i/10..."
    sleep 1
done

# ── Start the bot ──────────────────────────────────────────────────────────────
echo "🤖 Starting Leech Bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exec python3 bot.py
