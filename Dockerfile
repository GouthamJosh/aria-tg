FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    aria2 \
    qbittorrent-nox \
    p7zip-full \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /app

# Copy files
COPY requirements.txt .
COPY bot.py .
COPY start.sh .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create directories
RUN mkdir -p /tmp/downloads/

# Make start script executable
RUN chmod +x start.sh

# Expose ports
EXPOSE 6800 8080

# Run startup script
CMD ["./start.sh"]
