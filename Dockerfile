FROM python:3.10-slim-bookworm

# ── System dependencies ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    build-essential \
    libffi-dev \
    libssl-dev \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies first (better layer caching) ───────────────────
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# ── Copy bot files ─────────────────────────────────────────────────────────────
COPY . .

# ── Create startup script ──────────────────────────────────────────────────────
RUN cat > /app/start.sh << 'EOF'
#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖  Leech Bot — Starting Up"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Verify aria2c is installed ─────────────────────────────────────────────────
if ! command -v aria2c &> /dev/null; then
    echo "❌ aria2c not found! Attempting install..."
    apt-get update && apt-get install -y aria2
fi

echo "✅ aria2c: $(aria2c --version | head -n1)"

# ── Create download directory ──────────────────────────────────────────────────
mkdir -p /tmp/downloads

# ── Start Aria2c RPC daemon ────────────────────────────────────────────────────
echo "🚀 Starting Aria2c RPC daemon..."
aria2c \
    --enable-rpc \
    --rpc-listen-all=false \
    --rpc-listen-port=6800 \
    --rpc-secret="${ARIA2_SECRET:-gjxml}" \
    --rpc-max-request-size=16M \
    --max-concurrent-downloads=5 \
    --max-connection-per-server=16 \
    --min-split-size=10M \
    --split=16 \
    --continue=true \
    --auto-file-renaming=false \
    --allow-overwrite=true \
    --disk-cache=64M \
    --file-allocation=none \
    --log-level=warn \
    --daemon=true

# ── Wait for RPC to be ready ───────────────────────────────────────────────────
echo "⏳ Waiting for Aria2c RPC..."
for i in $(seq 1 10); do
    if curl -sf http://localhost:6800/jsonrpc > /dev/null 2>&1; then
        echo "✅ Aria2c RPC is ready!"
        break
    fi
    echo "   Attempt $i/10..."
    sleep 1
done

# ── Start the bot ──────────────────────────────────────────────────────────────
echo "🤖 Starting Leech Bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exec python3 bot.py
EOF

RUN chmod +x /app/start.sh

# ── Expose keep-alive port (Koyeb / Render / Railway require this) ─────────────
EXPOSE 8000

# ── Health check ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${PORT:-8000}/health || exit 1

# ── Entry point ────────────────────────────────────────────────────────────────
CMD ["bash", "/app/start.sh"]
