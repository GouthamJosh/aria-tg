# Use lightweight Python base
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system packages
# Note: 'unzip' is required for the Deno installation script to work
RUN apt-get update -qq && \
    apt-get install -y -qq \
        aria2 \
        curl \
        ffmpeg \
        wget \
        ca-certificates \
        netcat-openbsd \
        procps \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Download and install Deno locally for yt-dlp JavaScript challenges
RUN curl -fsSL https://deno.land/install.sh | sh

# Add Deno to the system PATH
ENV PATH="/root/.deno/bin:$PATH"

COPY requirements.txt .

# CRITICAL: Install pycryptodome BEFORE mega.py to prevent pycrypto installation
# Explicitly installing pycryptodome first to ensure it's present before requirements
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir pycryptodome && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app

# Create the downloads directory and set permissions
RUN mkdir -p downloads && chmod +x start.sh

EXPOSE 6800
ENTRYPOINT ["./start.sh"]
