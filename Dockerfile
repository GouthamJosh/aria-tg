# CHANGE: Use 'bookworm' (Newer Debian 12) instead of 'buster'
FROM python:3.10-slim-bookworm

# Install system dependencies
# Added --no-install-recommends to keep the image small
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of the files
COPY . .

# Make sure the start script is executable
RUN chmod +x run.sh

# Expose the port for Koyeb Health Checks
EXPOSE 8000

# Command to run when the container starts
CMD ["bash", "run.sh"]
