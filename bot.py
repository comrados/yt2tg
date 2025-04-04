import os
import re
import json
import math
import asyncio
import logging
import tempfile
import traceback
import subprocess

from typing import Optional

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

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Load config
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN: str = config["bot_token"]
ALLOWED_USERS: set[int] = set(config["allowed_users"])
TARGET_CHANNEL: str = config["target_channel"]

task_queue: asyncio.Queue = asyncio.Queue()


# -------------------------------
# Video splitting
# -------------------------------

def split_video_ffmpeg_by_size(input_path: str, max_size_mb: int = 49, overlap_sec: int = 5) -> list[str]:
    temp_dir: str = tempfile.mkdtemp()
    output_paths: list[str] = []

    log.info(f"[SPLIT] Analyzing video: {input_path}")
    result = subprocess.run(
        ['ffmpeg', '-i', input_path],
        stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True
    )
    match = re.search(r'Duration: (\d+):(\d+):(\d+.\d+)', result.stderr)
    if not match:
        raise Exception("Could not determine video duration")

    h, m, s = map(float, match.groups())
    total_duration: int = int(h * 3600 + m * 60 + s)
    log.info(f"[SPLIT] Video duration: {total_duration} seconds")

    file_size: int = os.path.getsize(input_path)
    file_size_mb: float = file_size / (1024 * 1024)
    log.info(f"[SPLIT] File size: {file_size_mb:.2f} MB")

    chunks_count: int = math.ceil(file_size_mb / max_size_mb)
    chunk_duration: int = math.ceil(total_duration / chunks_count)
    log.info(f"[SPLIT] Splitting into {chunks_count} parts, ~{chunk_duration}s each (+{overlap_sec}s overlap)")

    for i in range(chunks_count):
        start_time: int = max(i * chunk_duration - overlap_sec * i, 0)
        output_file: str = os.path.join(temp_dir, f"part_{i + 1}.mp4")

        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_time),
            '-i', input_path,
            '-t', str(chunk_duration + overlap_sec),
            '-c', 'copy',
            output_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        output_paths.append(output_file)
        log.info(f"[SPLIT] Created part {i + 1}/{chunks_count}: {output_file}")

    return output_paths


# -------------------------------
# Utils
# -------------------------------

def is_allowed(update: Update) -> bool:
    return update.effective_user.id in ALLOWED_USERS


def clean_youtube_url(url: str) -> Optional[str]:
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


# -------------------------------
# Handlers
# -------------------------------

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log.info(f"[ID] /id from user {user_id} in chat {chat_id}")
    await update.message.reply_text(
        f"👤 Your Telegram user ID: `{user_id}`\n💬 Chat ID: `{chat_id}`",
        parse_mode='Markdown'
    )


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        log.info(f"[BLOCKED] Unauthorized user {update.effective_user.id}")
        await update.message.reply_text("🚫 You're not allowed to use this bot.")
        return

    if not context.args:
        await update.message.reply_text("📎 Please send a YouTube link.")
        return

    raw_url: str = context.args[0]
    clean_url: Optional[str] = clean_youtube_url(raw_url)
    if not clean_url:
        await update.message.reply_text("❌ Invalid YouTube link.")
        return

    log.info(f"[QUEUE] New task from user {update.effective_user.id} — URL: {clean_url}")
    await update.message.reply_text("✅ Added to the queue.")
    await task_queue.put((update, context, clean_url))


async def debug_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.forward_from_chat:
        chat_id: int = update.message.forward_from_chat.id
        log.info(f"[FORWARD] Forwarded from chat ID: {chat_id}")
        await update.message.reply_text(f"📣 Channel ID: `{chat_id}`", parse_mode='Markdown')


async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message else None
    log.info(f"[MSG] From user {user.id} in chat {chat.id}: {text}")


# -------------------------------
# Core video logic
# -------------------------------

async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    filename: str = "video.mp4"

    try:
        if os.path.exists(filename):
            os.remove(filename)

        # Step 1: Get video metadata
        ydl_opts_info = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        title: str = info.get("title", "Untitled")
        await update.message.reply_text("⏳ Downloading video (360p)...", parse_mode="Markdown")

        # Step 2: Download the video in 360p
        ydl_opts = {
            'format': 'best[height<=360][ext=mp4][tbr<=600]/best[height<=360][ext=mp4]',
            'outtmpl': filename,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Step 3: Verify file exists
        if not os.path.exists(filename):
            await update.message.reply_text("❌ Download failed: file not found.")
            return

        # Step 4: Check file size
        size_bytes: int = os.path.getsize(filename)
        size_mb: float = round(size_bytes / (1024 * 1024), 1)
        await update.message.reply_text(f"📦 File size: *{size_mb} MB*", parse_mode="Markdown")

        # Step 5: Split and send or send directly
        if size_bytes > 50 * 1024 * 1024:
            parts: list[str] = split_video_ffmpeg_by_size(filename, max_size_mb=49, overlap_sec=5)
            total: int = len(parts)
            await update.message.reply_text(f"🔪 Splitting into {total} parts...")

            for idx, part_path in enumerate(parts, start=1):
                caption_part = f"🎬 *{title}* ({idx}/{total})"
                with open(part_path, 'rb') as part_file:
                    await context.bot.send_document(
                        chat_id=TARGET_CHANNEL,
                        document=part_file,
                        caption=caption_part,
                        parse_mode='Markdown'
                    )
                os.remove(part_path)
                log.info(f"[SEND] Sent part {idx}/{total}")

            await update.message.reply_text("✅ All parts sent.")
        else:
            caption: str = f"🎬 *{title}*"
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

    except Exception as e:
        log.error(f"[ERROR] Failed to process video: {e}")
        log.debug(traceback.format_exc())
        await update.message.reply_text(f"⚠️ Error: {e}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
            log.info(f"[CLEANUP] Removed original: {filename}")


# -------------------------------
# Worker
# -------------------------------

async def worker_loop(app) -> None:
    while True:
        update, context, url = await task_queue.get()
        try:
            await process_video(update, context, url)
        except Exception as e:
            log.error(f"[ERROR] Worker exception: {e}")
            log.debug(traceback.format_exc())
            await update.message.reply_text(f"❌ Error: {e}")
        finally:
            task_queue.task_done()


async def start_worker(app) -> None:
    asyncio.create_task(worker_loop(app))


# -------------------------------
# Entry point
# -------------------------------

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
    app.add_handler(MessageHandler(filters.ALL, log_message))

    log.info("✅ Bot started and polling...")
    app.run_polling()
