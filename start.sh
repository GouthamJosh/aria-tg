#!/bin/bash

# Start aria2 RPC server
aria2c --enable-rpc --rpc-listen-all=true --rpc-allow-origin-all --rpc-listen-port=6800 &

# Start qBittorrent
qbittorrent-nox --webui-port=8080 &

# Wait for services to start
sleep 5

# Run the bot
python bot.py
