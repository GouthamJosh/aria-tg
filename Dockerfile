# Use lightweight Python base
FROM python:3.11-slim

# Prevent Python from buffering logs
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install required system packages
RUN apt-get update -qq && \
    apt-get install -y -qq \
        aria2 \
        curl \
        wget \
        ca-certificates \
        netcat-openbsd \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /app

# Make startup script executable
RUN chmod +x start.sh

# Expose Aria2 RPC port
EXPOSE 6800

# Start the universal startup script
ENTRYPOINT ["./start.sh"]
