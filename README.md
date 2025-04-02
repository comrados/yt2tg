# YouTube-to-Telegram Bot

A Telegram bot that downloads YouTube videos (360p or lower if needed) and sends them to a specified channel. Supports task queue, access control, Docker deployment, and usage in group chats.

## Features

* Accepts YouTube links from allowed users
* Queue-based video downloading (processed one at a time)
* Automatically lowers quality to 240p or 144p if file > 2GB
* Sends videos to a Telegram channel as the bot
* Access restricted to specific user IDs (defined in config)
* Supports command use in groups
* Ready to run with Docker or Docker Compose
* Config stored in config.json (no database needed)

## Project Structure

```
.
├── bot.py               # Main bot code
├── config.json          # Bot token, allowed user IDs, and channel ID
├── Dockerfile           # Docker image definition
├── requirements.txt     # Python dependencies
└── docker-compose.yml   # For quick deployment
```

## Setup

1. Create your bot via @BotFather
   * Save the API token
   * Add the bot to your target channel as admin (no signature)
2. Get your Telegram user ID and channel ID
   * Send `/id` to the bot in private or group chat
   * Forward any message from your channel to the bot to get its ID
3. Configure config.json
```json
{
  "bot_token": "123456789:ABCDEF...",
  "allowed_users": [111111111, 222222222],
  "target_channel": -1001234567890
}
```

## Running with Docker Compose

First time:
```bash
docker compose up --build -d
```

To restart after changes:
```bash
docker compose restart
```

## Bot Usage

* `/id` - Get your Telegram user ID and chat ID
* `/download <link>` - Download and send a YouTube video to the configured channel

Only users listed in `allowed_users` can use the bot.

## GCP Quickstart (Ubuntu)

```bash
sudo apt update
sudo apt install -y docker.io
git clone https://github.com/comrados/yt2tg.git
cd yt2tg-bot
docker compose up --build -d
```

## License

MIT © 2025 comrados

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.