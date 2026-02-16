FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    aria2 \
    qbittorrent-nox \
    p7zip-full \
    wget \
    curl \
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

# Expose ports (optional, for monitoring)
EXPOSE 6800 8080

# Start script
CMD aria2c --enable-rpc \
    --rpc-listen-all=true \
    --rpc-allow-origin-all \
    --rpc-listen-port=6800 \
    --max-concurrent-downloads=5 \
    --max-connection-per-server=10 \
    --split=10 & \
    qbittorrent-nox \
    --webui-port=8080 \
    --profile=/tmp \
    & \
    sleep 5 && \
    python bot.py
