# Use lightweight Python base
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system packages (no build tools needed for pycryptodome!)
RUN apt-get update -qq && \
    apt-get install -y -qq \
        aria2 curl ffmpeg wget ca-certificates netcat-openbsd procps \
    && rm -rf /var/lib/apt/lists/*

# Download and install Deno locally for yt-dlp JavaScript challenges
RUN curl -fsSL https://deno.land/install.sh | sh

# Add Deno to the system PATH
ENV PATH="/root/.deno/bin:$PATH"

COPY requirements.txt .

# CRITICAL: Install pycryptodome BEFORE mega.py to prevent pycrypto installation
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app
# Create the downloads directory
RUN mkdir -p downloads
RUN chmod +x start.sh

EXPOSE 6800
ENTRYPOINT ["./start.sh"]
