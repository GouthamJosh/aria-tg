# Use lightweight Python base
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system packages (no build tools needed for pycryptodome!)
RUN apt-get update -qq && \
    apt-get install -y -qq \
        aria2 curl ffmpeg wget ca-certificates netcat-openbsd procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CRITICAL: Install pycryptodome BEFORE mega.py to prevent pycrypto installation
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app
RUN chmod +x start.sh

EXPOSE 6800
ENTRYPOINT ["./start.sh"]
