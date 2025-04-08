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

from telegram import Update, Message
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

def split_video_ffmpeg_by_size(input_path: str, max_size_mb: int = 45, overlap_sec: int = 5) -> list[str]:
    temp_dir = tempfile.mkdtemp()
    output_paths = []

    log.info(f"[SPLIT] Analyzing video: {input_path}")

    # Get total video duration
    result = subprocess.run(
        ['ffmpeg', '-i', input_path],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True
    )
    match = re.search(r'Duration: (\d+):(\d+):(\d+.\d+)', result.stderr)
    if not match:
        raise Exception("Could not determine video duration")

    h, m, s = map(float, match.groups())
    total_duration = int(h * 3600 + m * 60 + s)
    log.info(f"[SPLIT] Video duration: {total_duration} seconds")

    start_time = 0
    part_index = 1
    base_chunk_duration = total_duration // math.ceil(os.path.getsize(input_path) / (max_size_mb * 1024 * 1024))
    min_duration = 30  # don't go below this to avoid micro-chunks

    while start_time < total_duration:
        current_duration = min(base_chunk_duration + overlap_sec, total_duration - start_time)
        output_file = os.path.join(temp_dir, f"part_{part_index}.mp4")

        while current_duration >= min_duration:
            # Try splitting this chunk
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start_time),
                '-i', input_path,
                '-t', str(current_duration),
                '-c', 'copy',
                output_file
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if not os.path.exists(output_file):
                log.warning(f"[SPLIT] Failed to create part {part_index}")
                break

            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            if size_mb <= max_size_mb:
                log.info(f"[SPLIT] Part {part_index}: {size_mb:.2f} MB, {current_duration}s")
                output_paths.append(output_file)
                break
            else:
                log.warning(f"[SPLIT] Part {part_index} too large ({size_mb:.2f} MB > {max_size_mb} MB), reducing duration")
                os.remove(output_file)
                current_duration -= 60  # reduce by 60s and try again

        if current_duration < min_duration:
            log.error(f"[SPLIT] Could not fit a chunk under {max_size_mb} MB even at min duration.")
            break

        # Update next chunk start time (preserving overlap)
        start_time += current_duration - overlap_sec
        part_index += 1

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
        elif '/shorts/' in parsed.path:
            video_id = parsed.path.split('/shorts/')[-1].split('/')[0]
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
    if update.message:
        status_message = await update.message.reply_text("✅ Added to the queue...", parse_mode="Markdown")
    else:
        log.warning("[WARN] No message object in update — fallback to sending manually")
        status_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Added to the queue...",
            parse_mode="Markdown"
        )
    await task_queue.put((update, context, clean_url, status_message))

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

async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, status_message: Message) -> None:
    filename: str = "video.mp4"

    try:
        if os.path.exists(filename):
            os.remove(filename)

        ydl_opts_info = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        title: str = info.get("title", "Untitled")

        # Download the video in 360p
        await status_message.edit_text("⏬ Downloading video (360p)...", parse_mode="Markdown")
        ydl_opts = {
            'format': 'best[height<=360][ext=mp4][tbr<=600]/best[height<=360][ext=mp4]',
            'outtmpl': filename,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(filename):
            await status_message.edit_text("❌ Download failed: file not found.", parse_mode="Markdown")
            return

        size_bytes: int = os.path.getsize(filename)
        size_mb: float = round(size_bytes / (1024 * 1024), 1)

        # Determine target destination
        target_chat_id: int = (
            update.effective_chat.id
            if update.effective_chat.type == "private"
            else int(TARGET_CHANNEL)
        )
        log.info(f"[SEND] Sending to chat_id={target_chat_id}")

        if size_bytes > 50 * 1024 * 1024:
            await status_message.edit_text(f"📦 Downloaded ({size_mb} MB). Splitting...", parse_mode="Markdown")

            parts: list[str] = split_video_ffmpeg_by_size(filename, max_size_mb=49, overlap_sec=5)
            total: int = len(parts)

            for idx, part_path in enumerate(parts, start=1):
                await status_message.edit_text(f"📤 Sending part {idx}/{total}...", parse_mode="Markdown")
                caption_part = f"🎬 *{title}* ({idx}/{total})"
                with open(part_path, 'rb') as part_file:
                    await context.bot.send_video(
                        chat_id=target_chat_id,
                        video=part_file,
                        caption=caption_part,
                        parse_mode='Markdown',
                        supports_streaming=True
                    )
                os.remove(part_path)
                log.info(f"[SEND] Sent part {idx}/{total}")
                log.info(f"[CLEANUP] Removed part: {idx}/{total}")

            await status_message.edit_text("✅ All parts sent.", parse_mode="Markdown")
        else:
            await status_message.edit_text(f"📦 Downloaded ({size_mb} MB). Sending...", parse_mode="Markdown")

            caption: str = f"🎬 *{title}*"
            with open(filename, 'rb') as f:
                await context.bot.send_video(
                    chat_id=target_chat_id,
                    video=f,
                    caption=caption,
                    parse_mode='Markdown',
                    supports_streaming=True,
                    write_timeout=60,
                    read_timeout=60,
                    connect_timeout=30
                )

            await status_message.edit_text("✅ Sent to channel as video (360p)", parse_mode="Markdown")

    except Exception as e:
        log.error(f"[ERROR] Failed to process video: {e}")
        log.debug(traceback.format_exc())
        await status_message.edit_text(f"❌ Error: {e}", parse_mode="Markdown")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
            log.info(f"[CLEANUP] Removed original: {filename}")

# -------------------------------
# Worker
# -------------------------------

async def worker_loop(app: object) -> None:
    while True:
        update, context, url, status_message = await task_queue.get()
        try:
            await process_video(update, context, url, status_message)
        except Exception as e:
            log.error(f"[ERROR] Worker exception: {e}")
            log.debug(traceback.format_exc())
            await status_message.edit_text(f"❌ Error: {e}", parse_mode="Markdown")
        finally:
            task_queue.task_done()

async def start_worker(app: object) -> None:
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