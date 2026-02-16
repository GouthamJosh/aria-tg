#!/bin/bash

echo "🔧 Installing aria2 and qBittorrent..."

# Update package list
sudo apt-get update

# Install aria2
echo "📥 Installing aria2..."
sudo apt-get install -y aria2

# Install qBittorrent-nox
echo "📥 Installing qBittorrent..."
sudo apt-get install -y qbittorrent-nox

# Install 7zip
echo "📥 Installing 7zip..."
sudo apt-get install -y p7zip-full

echo "✅ Installation complete!"
echo ""
echo "Now run: bash start.sh"
