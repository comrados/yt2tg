import os
import re
import json
import asyncio
from urllib.parse import urlparse, parse_qs

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import yt_dlp

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Load config
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN = config["bot_token"]
ALLOWED_USERS = set(config["allowed_users"])
TARGET_CHANNEL = config["target_channel"]

task_queue = asyncio.Queue()

# Access check
def is_allowed(update: Update) -> bool:
    return update.effective_user.id in ALLOWED_USERS

# Clean and validate YouTube URL
def clean_youtube_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname.lower() if parsed.hostname else ''
        if host not in {'youtube.com', 'www.youtube.com', 'youtu.be'}:
            return None

        if 'youtu.be' in host:
            video_id = parsed.path.strip('/')
        else:
            query = parse_qs(parsed.query)
            video_id = query.get('v', [None])[0]

        if not video_id or not re.match(r'^[\w-]{11}$', video_id):
            return None

        return f"https://youtu.be/{video_id}"
    except Exception:
        return None

# /id — show user and chat ID
async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log.info(f"[ID] /id from user {user_id} in chat {chat_id}")
    await update.message.reply_text(
        f"👤 Your Telegram user ID: `{user_id}`\n💬 Chat ID: `{chat_id}`",
        parse_mode='Markdown'
    )

# /download <link> — add to queue
async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        log.info(f"[BLOCKED] Unauthorized user {update.effective_user.id}")
        await update.message.reply_text("🚫 You're not allowed to use this bot.")
        return

    if not context.args:
        await update.message.reply_text("📎 Please send a YouTube link.")
        return

    raw_url = context.args[0]
    clean_url = clean_youtube_url(raw_url)
    if not clean_url:
        await update.message.reply_text("❌ Invalid YouTube link.")
        return

    log.info(f"[QUEUE] New task from user {update.effective_user.id} — URL: {clean_url}")
    await update.message.reply_text("✅ Added to the queue.")
    await task_queue.put((update, context, clean_url))

# Forwards (optional): get chat/channel ID
async def debug_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.forward_from_chat:
        chat_id = update.message.forward_from_chat.id
        log.info(f"[FORWARD] Forwarded from chat ID: {chat_id}")
        await update.message.reply_text(f"📣 Channel ID: `{chat_id}`", parse_mode='Markdown')

# Log any message to console
async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message else None
    log.info(f"[MSG] From user {user.id} in chat {chat.id}: {text}")

# Process the video: get size, download in 360p with bitrate filter, and send as document only
async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    filename = "video.mp4"

    try:
        if os.path.exists(filename):
            os.remove(filename)

        # Step 1: get info without download
        ydl_opts_info = {
            'quiet': True,
            'skip_download': True
        }

        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title", "Untitled")
        formats = info.get("formats", [])
        filesize_bytes = None

        # Estimate size for 360p mp4
        for f in formats:
            if (
                f.get("height") == 360 and
                f.get("ext") == "mp4" and
                not f.get("format_note", "").startswith("DASH")
            ):
                filesize_bytes = f.get("filesize") or f.get("filesize_approx")
                if filesize_bytes:
                    break

        if not filesize_bytes:
            filesize_bytes = info.get("filesize") or info.get("filesize_approx")

        size_mb = round((filesize_bytes or 0) / (1024 ** 2), 1)
        await update.message.reply_text(
            f"⏳ Downloading video (360p)... *{size_mb} MB*",
            parse_mode="Markdown"
        )

        # Step 2: download
        ydl_opts = {
            'format': 'best[height<=360][ext=mp4][tbr<=600]/best[height<=360][ext=mp4]',
            'outtmpl': filename,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Step 3: send as document
        if os.path.exists(filename):
            size = os.path.getsize(filename)
            log.info(f"[CHECK] File exists: {filename}, size: {size} bytes")
            caption = f"🎬 *{title}*"

            if size <= 4 * 1024 * 1024 * 1024:
                log.info(f"[SEND] Sending as document to {TARGET_CHANNEL} ({size // 1024**2} MB)")
                try:
                    with open(filename, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=TARGET_CHANNEL,
                            document=f,
                            caption=caption,
                            parse_mode='Markdown',
                            api_kwargs={"stream": True},
                            write_timeout=60,
                            read_timeout=60,
                            connect_timeout=30
                        )
                    await update.message.reply_text("✅ Sent to channel as document (360p)")
                    log.info(f"[DONE] Sent as document ({size // 1024**2} MB)")
                except Exception as e:
                    log.error(f"[ERROR] Failed to send document: {e}")
                    await update.message.reply_text("❌ Failed to send document.")
            else:
                await update.message.reply_text("❌ Video is larger than 4GB. Cannot upload.")
                log.error(f"[FAIL] File too large even for document — user {update.effective_user.id}")
        else:
            await update.message.reply_text("❌ Download failed: file not found.")
            log.error("[ERROR] File was expected but not found on disk.")

    except Exception as e:
        log.error(f"[ERROR] Failed to download/send: {e}")
        await update.message.reply_text(f"⚠️ Error: {e}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
            log.info(f"[CLEANUP] Removed file: {filename}")

# Worker loop: one-by-one video handling
async def worker_loop(app):
    while True:
        update, context, url = await task_queue.get()
        try:
            await process_video(update, context, url)
        except Exception as e:
            log.error(f"[ERROR] Worker exception: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
        finally:
            task_queue.task_done()

# Start queue worker
async def start_worker(app):
    asyncio.create_task(worker_loop(app))

# Entry point
if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(start_worker)
        .build()
    )

    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("download", download_command, block=False))
    app.add_handler(MessageHandler(filters.FORWARDED, debug_forward))
    app.add_handler(MessageHandler(filters.ALL, log_message))  # for logging everything

    log.info("✅ Bot started and polling...")
    app.run_polling()
