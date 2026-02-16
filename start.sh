#!/bin/bash

echo "🚀 Starting Telegram Leech Bot..."

# Check if aria2 is installed
if ! command -v aria2c &> /dev/null; then
    echo "❌ aria2 is not installed!"
    echo "Run: bash install.sh"
    exit 1
fi

# Check if qBittorrent is installed
if ! command -v qbittorrent-nox &> /dev/null; then
    echo "❌ qBittorrent is not installed!"
    echo "Run: bash install.sh"
    exit 1
fi

# Kill existing instances
echo "🧹 Cleaning up existing processes..."
pkill -f aria2c 2>/dev/null
pkill -f qbittorrent-nox 2>/dev/null
sleep 2

# Start aria2 RPC server
echo "📡 Starting aria2 RPC server..."
aria2c --enable-rpc \
    --rpc-listen-all=true \
    --rpc-allow-origin-all \
    --rpc-listen-port=6800 \
    --max-concurrent-downloads=5 \
    --max-connection-per-server=10 \
    --min-split-size=10M \
    --split=10 \
    --dir=/tmp/downloads \
    --continue=true \
    --daemon=true

# Wait for aria2 to start
sleep 2

# Check if aria2 is running
if ! pgrep -x aria2c > /dev/null; then
    echo "❌ Failed to start aria2!"
    exit 1
fi
echo "✅ aria2 started successfully"

# Start qBittorrent
echo "📡 Starting qBittorrent..."
qbittorrent-nox \
    --webui-port=8080 \
    --profile=/tmp/qbittorrent \
    &

# Wait for qBittorrent to start
sleep 3

# Check if qBittorrent is running
if ! pgrep -f qbittorrent-nox > /dev/null; then
    echo "❌ Failed to start qBittorrent!"
    exit 1
fi
echo "✅ qBittorrent started successfully"

# Create downloads directory
mkdir -p /tmp/downloads

echo ""
echo "🎉 All services started!"
echo "📡 aria2 RPC: http://localhost:6800"
echo "📡 qBittorrent WebUI: http://localhost:8080"
echo ""
echo "🤖 Starting bot..."
echo ""

# Run the bot
python3 bot.py
