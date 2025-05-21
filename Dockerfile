FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    ca-certificates && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /var/lib/apt/lists/*

RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp

COPY . .

CMD ["python", "-m", "src.bot"]
