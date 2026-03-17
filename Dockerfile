FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system packages + BUILD TOOLS
RUN apt-get update -qq && \
    apt-get install -y -qq \
        aria2 curl ffmpeg wget ca-certificates netcat-openbsd procps \
        gcc libc6-dev libffi-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Force upgrade tenacity first, then install rest
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir tenacity>=8.2.0 && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app
RUN chmod +x start.sh

EXPOSE 6800
ENTRYPOINT ["./start.sh"]
