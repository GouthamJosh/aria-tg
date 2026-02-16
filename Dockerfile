FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    aria2 \
    qbittorrent-nox \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy files
COPY requirements.txt .
COPY bot.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create downloads directory
RUN mkdir -p /tmp/downloads

# Start aria2 and qBittorrent in background, then run bot
CMD aria2c --enable-rpc --rpc-listen-all=true --rpc-allow-origin-all & \
    qbittorrent-nox --webui-port=8080 & \
    sleep 5 && \
    python bot.py
