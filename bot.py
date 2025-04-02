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

# Load config
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN = config["bot_token"]
ALLOWED_USERS = set(config["allowed_users"])
TARGET_CHANNEL = config["target_channel"]

quality_list = [360, 240, 144]
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
    print(f"[ID] /id from user {user_id} in chat {chat_id}")
    await update.message.reply_text(
        f"👤 Your Telegram user ID: `{user_id}`\n💬 Chat ID: `{chat_id}`",
        parse_mode='Markdown'
    )

# /download <link> — add to queue
async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        print(f"[BLOCKED] Unauthorized user {update.effective_user.id}")
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

    print(f"[QUEUE] New task from user {update.effective_user.id} — URL: {clean_url}")
    await update.message.reply_text("✅ Added to the queue.")
    await task_queue.put((update, context, clean_url))

# Forwards (optional): get chat/channel ID
async def debug_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.forward_from_chat:
        chat_id = update.message.forward_from_chat.id
        print(f"[FORWARD] Forwarded from chat ID: {chat_id}")
        await update.message.reply_text(f"📣 Channel ID: `{chat_id}`", parse_mode='Markdown')

# Log any message to console
async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message else None
    print(f"[MSG] From user {user.id} in chat {chat.id}: {text}")

# Process the video: download, check size, send
async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    msg = await update.message.reply_text("⏳ Downloading video...")

    for quality in quality_list:
        try:
            filename = "video.mp4"
            if os.path.exists(filename):
                os.remove(filename)

            ydl_opts = {
                'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
                'outtmpl': filename,
                'quiet': True,
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'skip_download': False,
                'no_warnings': True
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            if os.path.exists(filename):
                size = os.path.getsize(filename)
                if size <= 2 * 1024 * 1024 * 1024:
                    title = info.get("title", "Untitled")
                    description = info.get("description", "")
                    caption = f"🎬 *{title}*\n\n{description[:1024]}"  # Telegram max caption length = 1024
                    print(f"[SEND] Sending video to channel {TARGET_CHANNEL} from user {update.effective_user.id} ({quality}p)")
                    await context.bot.send_video(
                        chat_id=TARGET_CHANNEL,
                        video=open(filename, 'rb'),
                        caption=caption,
                        parse_mode='Markdown'
                    )
                    await msg.edit_text(f"✅ Sent to channel in {quality}p")
                    os.remove(filename)
                    return
        except Exception as e:
            print(f"[ERROR] Download error at {quality}p: {e}")
            await update.message.reply_text(f"⚠️ Error at {quality}p: {e}")

    await msg.edit_text("❌ Couldn't reduce the video size below 2GB.")
    print(f"[FAIL] Video too big even at 144p — user {update.effective_user.id}")

# Worker loop: one-by-one video handling
async def worker_loop(app):
    while True:
        update, context, url = await task_queue.get()
        try:
            await process_video(update, context, url)
        except Exception as e:
            print(f"[ERROR] Worker exception: {e}")
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

    print("✅ Bot started and polling...")
    app.run_polling()
