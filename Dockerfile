FROM python:3.10-slim-bookworm

# 1. Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Copy your bot files
COPY . .

# 3. Install Python requirements
# (Make sure requirements.txt exists in your repo)
RUN pip3 install --no-cache-dir -r requirements.txt

# 4. FIX: Create run.sh automatically inside the image
# This fixes the "No such file" error by generating the script right here.
RUN echo '#!/bin/bash' > start.sh && \
    echo 'echo "🚀 Starting Aria2c daemon..."' >> start.sh && \
    echo 'aria2c --enable-rpc --rpc-listen-all=false --rpc-listen-port=6800 --rpc-secret=${ARIA2_SECRET:-gjxml} --daemon=true' >> start.sh && \
    echo 'sleep 3' >> start.sh && \
    echo 'echo "🚀 Starting Leech Bot..."' >> start.sh && \
    echo 'python3 bot.py' >> start.sh && \
    chmod +x start.sh

# 5. Run it
EXPOSE 8000
CMD ["bash", "start.sh"]
