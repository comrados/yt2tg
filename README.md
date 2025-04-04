# YouTube-to-Telegram Bot

A Telegram bot that downloads YouTube videos and forwards them to a specified Telegram channel. The bot automatically handles video splitting for larger files, manages downloads through a task queue, and restricts access to authorized users.

## Features

- Downloads YouTube videos in 360p quality (optimized for Telegram)
- Automatically splits videos larger than 50MB with 5-second overlaps
- Sends videos to a designated Telegram channel
- Queue-based processing system (one download at a time)
- Access control via user ID whitelist
- Containerized with Docker for easy deployment
- Works in private chats and group conversations
- No database required (configuration via simple JSON file)

## Project Structure

```
.
├── bot.py               # Main bot application code
├── config.json          # Configuration (bot token, allowed users, target channel)
├── Dockerfile           # Docker container definition
├── docker-compose.yml   # Docker Compose configuration
└── requirements.txt     # Python dependencies
```

## Prerequisites

- A GCP e2-micro instance running Debian 12
- Docker and Docker Compose installed
- Telegram bot token (from @BotFather)
- Your Telegram user ID
- Target Telegram channel ID

## Setup Instructions

### 1. GCP e2-micro Instance with Debian 12 Preparation

Update your system and create a swap file for better performance on the small e2-micro instance:

```bash
# Update system
sudo apt update
sudo apt upgrade -y

# Create 2GB swap file (essential for e2-micro instances)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make swap permanent
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 2. Install Docker and Docker Compose

```bash
# Install Docker
sudo apt install -y docker.io

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version
```

### 3. Create Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot` command
3. Copy the API token provided by BotFather

### 4. Get Required IDs

1. Start a conversation with your bot
2. Send the command `/id` to get your user ID
3. Create a channel where videos will be posted
4. Add your bot as an administrator to the channel (with posting permissions)
5. Forward any message from your channel to the bot to get the channel ID (will be shown when you use `/id`)

### 5. Configure the Bot

Edit the `config.json` file:

```json
{
    "bot_token": "YOUR_BOT_TOKEN",
    "allowed_users": [
        YOUR_USER_ID,
        ANOTHER_ALLOWED_USER_ID
    ],
    "target_channel": YOUR_CHANNEL_ID
}
```

### 6. Deploy the Bot

```bash
# Clone the repository or upload your files
git clone https://github.com/comrados/yt2tg.git
cd yt2tg

# Start the bot
docker-compose up --build -d

# Check logs if needed
docker logs -f yt2tg
```

## Bot Usage

- `/id` - Get your Telegram user ID and current chat ID
- `/download <YouTube URL>` - Download a video and send it to the configured channel
- Forward a message from your channel to get its ID

## Technical Details

### Video Processing

- Videos are downloaded using yt-dlp in 360p MP4 format
- Files larger than 50MB are automatically split to comply with Telegram limits
- Each split has a 5-second overlap for better viewing experience
- FFmpeg is used for video processing (installed in the Docker container)

### Resource Management

- The Docker container is limited to 512MB RAM with 1GB swap
- Perfect for GCP e2-micro instances with limited resources
- Single-worker queue system prevents memory exhaustion
- Videos are removed after processing to save disk space

## Troubleshooting

If the bot doesn't respond:
1. Check Docker container status: `docker-compose ps`
2. View logs: `docker-compose logs -f`
3. Verify your bot token and channel ID in `config.json`
4. Ensure the bot has proper permissions in the target channel
5. For GCP e2-micro instances, verify swap is properly configured with `free -h`

## License

MIT © 2025 comrados

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.