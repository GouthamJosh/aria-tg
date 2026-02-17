FROM python:3.10-slim

# Install required packages
RUN apt-get update && \
    apt-get install -y aria2 qbittorrent-nox p7zip-full && \
    apt-get clean

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD aria2c --enable-rpc --rpc-listen-port=6800 --daemon && python3 bot.py
