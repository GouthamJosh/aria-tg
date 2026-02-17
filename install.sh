#!/bin/bash

echo "🔧 Installing aria2 (static binary)..."

# Download aria2 static build
ARIA2_VERSION=1.37.0
wget https://github.com/aria2/aria2/releases/download/release-${ARIA2_VERSION}/aria2-${ARIA2_VERSION}-linux-gnu-64bit-build1.tar.bz2

tar -xjf aria2-${ARIA2_VERSION}-linux-gnu-64bit-build1.tar.bz2

# Move aria2c to project root
mv aria2-*/aria2c ./aria2c
chmod +x aria2c

echo "✅ aria2 installed locally"

echo "⚠️ qBittorrent cannot be installed on Koyeb native."
echo "Use aria2 only OR switch to Docker."
