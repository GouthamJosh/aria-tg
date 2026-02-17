#!/bin/bash

# 1. Start Aria2c in the background (Daemon mode)
# We use the secret "gjxml" because that is the default in your python script
echo "🚀 Starting Aria2c..."
aria2c --enable-rpc \
       --rpc-listen-all=false \
       --rpc-listen-port=6800 \
       --rpc-secret=${ARIA2_SECRET:-gjxml} \
       --daemon=true

# 2. Wait a moment to ensure Aria2 is ready
sleep 2

# 3. Start the Python Bot
echo "🚀 Starting Leech Bot..."
python3 bot.py
