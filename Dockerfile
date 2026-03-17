# Use lightweight Python base
FROM python:3.11-slim

# Prevent Python from buffering logs
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install required system packages + BUILD TOOLS for pycrypto
RUN apt-get update -qq && \
    apt-get install -y -qq \
        aria2 \
        curl \
        ffmpeg \
        wget \
        ca-certificates \
        netcat-openbsd \
        procps \
        gcc \
        libc6-dev \
        libffi-dev \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file FIRST (this leverages Docker layer caching for faster rebuilds)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . /app

# Make startup script executable
RUN chmod +x start.sh

# Expose Aria2 RPC port
EXPOSE 6800

# Start the universal startup script
ENTRYPOINT ["./start.sh"]
