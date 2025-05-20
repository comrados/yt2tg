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

- An Oracle Cloud Always Free `VM.Standard.E2.1.Micro` instance running `Ubuntu 22.04 Minimal`
- Docker and Docker Compose installed
- Telegram bot token (from [@BotFather](https://telegram.me/BotFather))
- Your Telegram user ID
- Target Telegram channel ID (if sending to a channel)

*Note: You might need to run `docker` and `docker-compose` commands with `sudo`. To run Docker commands without `sudo`, add your user to the `docker` group using `sudo usermod -aG docker $USER`. You will need to log out and log back in (or reboot the system) for this change to take effect.*

## Setup Instructions

### 1. Oracle Cloud Always Free VM Instance Preparation

Update your system and create a swap file for better performance on the small `VM.Standard.E2.1.Micro` instance:

```bash
# Update system
sudo apt update
sudo apt upgrade -y

# Install necessary packages
sudo apt install -y curl git nano

# Create 2GB swap file (essential for small instances with only 1GB RAM)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make swap permanent
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Verify swap is active
free -h
```

### 2. Install Docker and Docker Compose

`Ubuntu 22.04` offers multiple ways to install Docker. Here's the recommended approach using the official Docker repository for the latest stable version:

```bash
# Install Docker using the convenience script (simplest method)
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Verify Docker installation
sudo docker --version

# Install Docker Compose
sudo apt update
sudo apt install -y docker-compose-plugin

# Verify Docker Compose installation
docker compose version

# Add your user to the docker group (optional, recommended)
sudo usermod -aG docker $USER
# Note: You'll need to log out and back in for this to take effect
# Until then, you can use 'sudo docker' instead of just 'docker'
```

If you prefer the traditional Docker Compose standalone binary instead of the plugin:

```bash
# Install Docker Compose standalone version
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

Edit the `config.json` file:

```
sudo nano config.json
```

Replace with your values:


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
sudo docker compose up --build -d

# Check logs if needed
sudo docker logs -f yt2tg
```

Use `sudo docker-compose up --build -d`, if standalone Docker Compose was installed.

## Bot Usage

- `/id` - Get your Telegram user ID and current chat ID
- `/download <YouTube URL>` - Download a video and send it to you or the configured channel
- `/logs` - Get logs from the last 60 minutes (admin only)
- `/tasks` - List currently running tasks (admin only)
- `/checkcookies` - Check if YouTube cookies are valid and view cookie expiration information (admin only)

## Technical Details

### Video Processing

- Videos are downloaded using yt-dlp in 360p MP4 format with bitrate ≤ 600kbps when available
- Files larger than 50MB are automatically split to comply with Telegram limits
- Each split has a 5-second overlap for better viewing experience
- FFmpeg is used for video splitting
- Downloaded videos are cleaned up after processing

### YouTube Cookies Configuration

The bot supports YouTube cookies for accessing age-restricted or private videos:

1. Create a `cookies.txt` file in the bot's root directory (same level as `bot.py`):
   ```bash
   touch cookies.txt
   ```

2. Export cookies from your browser using an extension like "Get cookies.txt" or "Cookie-Editor" after logging into your YouTube account.

3. Paste the cookies into the `cookies.txt` file.

4. The bot will automatically detect and use the cookies for YouTube downloads.

5. Use the `/checkcookies` command to verify if your cookies are working and see their expiration dates.

Note: Without valid cookies, age-restricted videos cannot be downloaded.

### Database

- The bot uses SQLite to track processed videos
- Each video is identified by chat ID and YouTube video ID
- This prevents duplicate downloads unless explicitly requested
- **Recommendation:** For enhanced stability, especially if encountering network errors (`httpx.ReadError`) during video processing, consider increasing `mem_limit` (e.g., to `768m`) and `memswap_limit` (e.g., to `1536m`) in `docker-compose.yml` and rebuilding the container.
- Perfect for Oracle Cloud Always Free tier `VM.Standard.E2.1.Micro` instances with limited resources when swap is enabled on the host.
- Single-worker queue system prevents memory exhaustion from concurrent downloads.
- Downloaded videos are removed after processing to save disk space.
- Task timeout of 10 minutes to prevent stuck downloads.

## Oracle Cloud Specific Notes

### Firewall Configuration

Oracle Cloud instances have a built-in firewall. Make sure to open the necessary ports for your application:

1. Navigate to your instance in the Oracle Cloud Console
2. Go to the "Virtual Cloud Network" section
3. Click on your VCN, then "Security Lists"
4. Add an Ingress Rule for your bot's webhook port (usually 443 for HTTPS) if you're using webhooks

### Maintaining Always Free Status

- Oracle Cloud Free Tier provides 1 `VM.Standard.E2.1.Micro` instance with 1 OCPU and 1GB RAM
- Keep resource usage within limits to avoid charges
- Regularly check the Oracle Cloud Console for usage metrics
- The setup in this guide is optimized for the Always Free resources

## Troubleshooting

If the bot doesn't respond:
1. Check Docker container status: `sudo docker-compose ps`
2. View logs: `sudo docker-compose logs -f`
3. Verify your bot token and channel ID in `config.json`
4. Ensure the bot has proper permissions in the target channel
5. Verify swap is properly configured with `free -h`
6. Use the `/logs` command to check recent activity

Common issues:
- If videos fail to download, ensure the URL is a valid YouTube link
- For large videos that time out, try using shorter clips
- If the bot stops responding, restart it with `sudo docker-compose restart`
- If you can't add the bot to a channel, check that it has permission to join channels in BotFather settings
- For Ubuntu-specific issues, check system logs with `sudo journalctl -xe`

## License

MIT © 2025 comrados

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.