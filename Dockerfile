# Use a lightweight Python version
FROM python:3.10-slim-buster

# Install system dependencies (Aria2 is required for the bot to work)
RUN apt-get update && apt-get install -y \
    aria2 \
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
CMD ["bash", "start.sh"]
