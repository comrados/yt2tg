# YouTube-to-Telegram Bot

A Telegram bot that downloads YouTube videos and forwards them to a specified Telegram channel or chat. The bot automatically handles video splitting for larger files, manages downloads through a task queue, and restricts access to authorized users.

## Features

- Downloads YouTube videos in 360p quality (optimized for Telegram)
- Automatically splits videos larger than 50MB with 5-second overlaps
- Sends videos to a designated Telegram channel or chat
- Queue-based processing system (one download at a time)
- Access control via user ID whitelist
- Tracks processed videos in SQLite database to prevent duplicate downloads
- Supports video retry functionality
- Detailed logging for monitoring and troubleshooting
- Containerized with Docker for easy deployment
- Works in private chats and group conversations

## Project Structure

```
.
├── bot.py               # Main bot application code
├── config.json          # Configuration (bot token, allowed users, target channel)
├── Dockerfile           # Docker container definition
├── docker-compose.yml   # Docker Compose configuration
├── requirements.txt     # Python dependencies
├── data/                # Directory for persistent data (SQLite database)
└── logs/                # Directory for log files
```

## Prerequisites

- A GCP e2-micro instance running Debian 12 (or any server with Docker support)
- Docker and Docker Compose installed
- Telegram bot token (from @BotFather)
- Your Telegram user ID
- Target Telegram channel ID (if sending to a channel)

*Note: You might need to run `docker` and `docker-compose` commands with `sudo`. To run Docker commands without `sudo`, add your user to the `docker` group using `sudo usermod -aG docker $USER`. You will need to log out and log back in (or reboot the system) for this change to take effect.*

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

# Install Docker Compose (v2.24.2 tested with this project)
sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version
```

### 3. Create Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot` command
3. Copy the API token provided by BotFather
4. **Important**: In BotFather, use `/mybots` → select your bot → Bot Settings → Group and Channel Settings → ensure "Allow groups?" and "Allow channels?" are both set to "Enabled"

### 4. Get Required IDs

1. Start a conversation with your bot
2. Send the command `/id` to get your user ID
3. If you want to send videos to a channel:
   - Create a channel where videos will be posted
   - Add your bot as an administrator to the channel (with posting permissions)
   - Forward any message from your channel to your bot and use `/id` to get the channel ID

### 5. Configure the Bot

Create or edit the `config.json` file:

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

Note: If you want videos to be sent to the same chat where the command is issued, the `target_channel` will be ignored for private chats.

### 6. Deploy the Bot

```bash
# Clone the repository or upload your files
git clone https://github.com/comrados/yt2tg.git
cd yt2tg

# Create required directories
mkdir -p data logs

# Build the Docker image and start the bot in detached mode
docker-compose up --build -d

# Check logs if needed
docker logs -f yt2tg
```

## Bot Usage

- `/id` - Get your Telegram user ID and current chat ID
- `/download <YouTube URL>` - Download a video and send it to you or the configured channel
- `/logs` - Get logs from the last 60 minutes (admin only)
- `/tasks` - List currently running tasks (admin only)

## Technical Details

### Video Processing

- Videos are downloaded using yt-dlp in 360p MP4 format with bitrate ≤ 600kbps when available
- Files larger than 50MB are automatically split to comply with Telegram limits
- Each split has a 5-second overlap for better viewing experience
- FFmpeg is used for video splitting
- Downloaded videos are cleaned up after processing

### Database

- The bot uses SQLite to track processed videos
- Each video is identified by chat ID and YouTube video ID
- This prevents duplicate downloads unless explicitly requested

### Resource Management

- The default Docker container limit is set to 512MB RAM with 1GB swap in `docker-compose.yml`. This is suitable for basic testing on resource-constrained environments like GCP e2-micro.
- **Recommendation:** For enhanced stability, especially if encountering network errors (`httpx.ReadError`) during video processing, consider increasing `mem_limit` (e.g., to `1g`) and `memswap_limit` (e.g., to `2g`) in `docker-compose.yml` and rebuilding the container.
- Perfect for GCP e2-micro instances with limited resources when swap is enabled on the host.
- Single-worker queue system prevents memory exhaustion from concurrent downloads.
- Downloaded videos are removed after processing to save disk space.
- Task timeout of 10 minutes to prevent stuck downloads.

## Troubleshooting

If the bot doesn't respond:
1. Check Docker container status: `docker-compose ps`
2. View logs: `docker-compose logs -f`
3. Verify your bot token and channel ID in `config.json`
4. Ensure the bot has proper permissions in the target channel
5. For GCP e2-micro instances, verify swap is properly configured with `free -h`
6. Use the `/logs` command to check recent activity

Common issues:
- If videos fail to download, ensure the URL is a valid YouTube link
- For large videos that time out, try using shorter clips
- If the bot stops responding, restart it with `docker-compose restart`
- If you can't add the bot to a channel, check that it has permission to join channels in BotFather settings

## License

MIT © 2025 comrados

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.