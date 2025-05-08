import os
import re
import json
import math
import asyncio
import logging
import tempfile
import subprocess
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse, parse_qs

import sqlite3
from contextlib import closing

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.error import RetryAfter, TimedOut
from yt_dlp.utils import DownloadError
from telegram.helpers import escape_markdown
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, Application, filters, CallbackQueryHandler
)
import yt_dlp

# --- Logging Configuration ---
LOG_FILE = "logs/bot.log"

def init_logging(log_path: str = LOG_FILE, overwrite: bool = True):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode='w' if overwrite else 'a'),
            logging.StreamHandler()
        ]
    )

    log = logging.getLogger()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    return log

log = init_logging()

# --- DB Configuration ---
DB_PATH = "data/bot.db"

def init_db(db_path: str = DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS processed_videos (
                chat_id INTEGER,
                video_id TEXT,
                message_id INTEGER,
                status TEXT,
                PRIMARY KEY (chat_id, video_id)
            )
        """)
        conn.commit()

init_db()

# --- Cookies Configuration ---
COOKIES_FILE = "cookies.txt"

def init_cookies(cookies_path: str = COOKIES_FILE) -> bool:
    if not os.path.exists(cookies_path):
        log.warning("[COOKIES] cookies.txt not found.")
        return False

    try:
        test_url = "https://youtu.be/nddkvl_qqBk"
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'cookiefile': cookies_path,
            'no_write_cookie_file': False
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(test_url, download=False)

        log.info("[COOKIES] Cookies are valid ✅")
        return True
    except DownloadError as e:
        log.warning(f"[COOKIES] Invalid or insufficient cookies: {e}")
    except Exception as e:
        log.error(f"[COOKIES] Failed to validate cookies: {e}", exc_info=True)

    return False


cookies_available = init_cookies()

# --- Config Loading ---
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN: str = config["bot_token"]
ALLOWED_USERS: set[int] = set(config["allowed_users"])
TARGET_CHANNEL: str = config["target_channel"]

# --- Utility Functions ---
def is_allowed(update: Update) -> bool:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    return user_id in ALLOWED_USERS and (chat_id == user_id or chat_id == TARGET_CHANNEL)

def is_task_queued_or_running(chat_id: int, video_id: str) -> bool:
    return any(
        t.video_id == video_id and t.update.effective_chat.id == chat_id
        for t in running_tasks.union(task_queue._queue)
    )

def get_video_id(url: str) -> Optional[str]:
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

        if video_id and re.match(r'^[\w-]{11}$', video_id):
            return video_id
        return None
    except Exception:
        return None

def clean_youtube_url(url: str) -> Optional[str]:
    video_id = get_video_id(url)
    return f"https://youtu.be/{video_id}" if video_id else None

def is_already_processed(chat_id: int, video_id: str) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("SELECT status FROM processed_videos WHERE chat_id = ? AND video_id = ?", (chat_id, video_id))
        result = c.fetchone()
        log.info(f"[DB] Checked if video {video_id} in chat {chat_id} is already processed: {result}")
        return result and result[0] == "success"


def mark_as_processed(chat_id: int, video_id: str, message_id: int, status: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO processed_videos (chat_id, video_id, message_id, status) VALUES (?, ?, ?, ?)",
            (chat_id, video_id, message_id, status)
        )
        conn.commit()
    log.info(f"[DB] Marked video {video_id} in chat {chat_id} as '{status}'")

def parse_log_time(line: str) -> datetime:
    try:
        timestamp = line.split()[0] + " " + line.split()[1]
        return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f")
    except Exception:
        return datetime.min

# --- Download task ---
class DownloadTask:
    def __init__(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, status_msg: Message, original_message_id: int):
        self.update = update
        self.context = context
        self.url = url
        self.status_msg = status_msg
        self.video_id = get_video_id(url)
        self.filename = f"video_{self.video_id}.mp4"
        self.temp_files: list[str] = []
        self.temp_dirs: list[str] = []
        self.created_at = datetime.now()
        self.user_id = update.effective_user.id
        self.original_message_id = original_message_id # Store it

    def __hash__(self):
        return hash((self.update.effective_chat.id, self.video_id))

    def __eq__(self, other):
        return (
            isinstance(other, DownloadTask) and
            self.update.effective_chat.id == other.update.effective_chat.id and
            self.video_id == other.video_id
        )

    async def run(self):
        try:
            log.info(f"[TASK] Started for user {self.user_id} in chat {self.update.effective_chat.id} | URL: {self.url}")
            await asyncio.wait_for(self._process(), timeout=600)

        except asyncio.TimeoutError:
            log.warning(f"[TASK] Timeout for user {self.user_id} in chat {self.update.effective_chat.id} | Video: {self.video_id}")
            await self._safe_edit_status("❌ Task timed out after 10 minutes.")
            if self.video_id:
                mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "failed")

        except DownloadError as e:
            log.error(f"[TASK] DownloadError processing video {self.video_id} for user {self.user_id}: {e}")
            error_message = str(e)
            user_message = "❌ Download error."
            if "Sign in to confirm your age" in error_message:
                if cookies_available:
                    user_message = f"❌ Age restricted video failed. Cookies might be invalid/expired."
                else:
                    user_message = f"❌ Age restricted video failed. Add a valid '{COOKIES_FILE}'."
            elif "unavailable" in error_message.lower():
                user_message = "❌ Video is unavailable."
            else:
                user_message = f"❌ Download error: {error_message[:100]}"

            await self._safe_edit_status(user_message)
            if self.video_id:
                mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "failed")

        except Exception as e:
            log.error(f"[TASK] Error processing video {self.video_id} for user {self.user_id}: {e}", exc_info=True)
            await self._safe_edit_status(f"❌ Error processing video.")
            if self.video_id:
                mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "failed")
        finally:
            await self.cleanup()
            if self in running_tasks:
                running_tasks.discard(self)

    async def _process(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)

        # Handle cookies: Copy to tmp if available
        if cookies_available:
            cookie_opts = {
                'cookiefile': COOKIES_FILE,
                'no_write_cookie_file': False
            }
        else:
            cookie_opts = {}

        info_ydl_opts = {
            'quiet': True,
            'skip_download': True,
            **cookie_opts
        }

        with yt_dlp.YoutubeDL(info_ydl_opts) as ydl:
            try:
                info = ydl.extract_info(self.url, download=False)
            except DownloadError as e:
                log.error(f"[TASK] Failed to extract info for {self.video_id}: {e}")
                error_message = str(e)
                if "confirm your age" in error_message and not cookies_available:
                    await self._safe_edit_status(f"❌ Age restricted video. Add '{COOKIES_FILE}'.")
                elif "confirm your age" in error_message:
                    await self._safe_edit_status(f"❌ Age restricted video. Cookies invalid?")
                else:
                    await self._safe_edit_status(f"❌ Failed to get video info.")
                raise e

        title = info.get("title", "Untitled")
        log.info(f"[SEND] Sending video titled: {title}")

        await self._safe_edit_status("⏬ Downloading video (360p)...")
        ydl_opts = {
            'format': 'best[height<=360][ext=mp4][tbr<=600]/best[ext=mp4]/best',
            'outtmpl': self.filename,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
            **cookie_opts
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.url])

        await asyncio.sleep(1)

        if not os.path.exists(self.filename) or os.path.getsize(self.filename) < 1024:
            raise Exception("Downloaded file is missing or too small.")

        log.info(f"[CHECK] Downloaded file size: {os.path.getsize(self.filename)} bytes")

        size_mb = os.path.getsize(self.filename) / (1024 * 1024)
        target_chat_id = self.update.effective_chat.id if self.update.effective_chat.type == "private" else int(TARGET_CHANNEL)

        self.sent_parts = {}  # part_index -> message_id

        if size_mb > 50:
            await self._safe_edit_status(f"📦 Downloaded ({size_mb:.1f} MB). Splitting...")
            paths, temp_dir = self.split_video(self.filename)
            await asyncio.sleep(2)  # Allow OS to flush writes from FFmpeg
            self.temp_dirs.append(temp_dir)
            self.temp_files.extend(paths)

            for idx, part in enumerate(paths, 1):
                raw_caption = f"🎬 {title} ({idx}/{len(paths)})"
                caption = escape_markdown(raw_caption, version=2)
                await self._safe_edit_status(f"📤 Sending part {idx}/{len(paths)}...")
                success, msg_id = await self._send_video_with_retry(target_chat_id, part, caption)
                if success:
                    self.sent_parts[idx] = msg_id
                    await self._safe_edit_status(f"✅ Sent part {idx}/{len(paths)}")
                else:
                    raise Exception(f"Failed to send part {idx}/{len(paths)}")
        else:
            raw_caption = f"🎬 {title}"
            caption = escape_markdown(raw_caption, version=2)
            success, msg_id = await self._send_video_with_retry(target_chat_id, self.filename, caption)
            if not success:
                raise Exception("Failed to send the video.")

        # Only mark as success after ALL parts sent successfully
        if self.video_id:
            mark_as_processed(self.update.effective_chat.id, self.video_id, self.update.effective_message.message_id, "success")
            await self._safe_edit_status("✅ Sent to Telegram")

    async def _send_video_with_retry(self, chat_id: int, file_path: str, caption: str) -> tuple[bool, Optional[int]]:
        max_retries = 5
        file_name = os.path.basename(file_path)

        with open(file_path, 'rb') as f:
            for attempt in range(1, max_retries + 1):
                try:
                    msg = await self.context.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=caption,
                        parse_mode='MarkdownV2',
                        supports_streaming=True,
                        disable_notification=True
                    )
                    log.info(f"[SEND] Sent file '{file_name}' to chat {chat_id}")
                    return True, msg.message_id

                except RetryAfter as e:
                    wait_time = int(e.retry_after) + 1
                    log.warning(f"[RETRY] Flood control. Waiting {wait_time}s (attempt {attempt}/{max_retries}) for file '{file_name}'...")
                    await asyncio.sleep(wait_time)
                    f.seek(0)

                except TimedOut as e:
                    log.warning(f"[RETRY] TimedOut on attempt {attempt}/{max_retries} for '{file_name}': {e}")
                    await asyncio.sleep(10)
                    f.seek(0)

                except Exception as e:
                    log.warning(f"[RETRY] Exception on attempt {attempt}/{max_retries} for '{file_name}': {e}")
                    await asyncio.sleep(5)
                    f.seek(0)

            log.error(f"[FAIL] Giving up after {max_retries} retries for file '{file_name}'")
            return False, None

    async def _safe_edit_status(self, text: str):
        try:
            await self.status_msg.edit_text(text, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"[EDIT_FAIL] Could not edit message: {e}")

    def split_video(self, input_path: str, max_size_mb: int = 40, overlap_sec: int = 5) -> tuple[list[str], str]:
        temp_dir = tempfile.mkdtemp()
        paths = []

        result = subprocess.run(['ffmpeg', '-i', input_path], stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
        match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', result.stderr)
        h, m, s = map(float, match.groups())
        total_duration = int(h * 3600 + m * 60 + s)

        file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
        chunks_count = math.ceil(file_size_mb / max_size_mb)
        base_duration = total_duration / chunks_count

        for i in range(chunks_count):
            start = max(i * base_duration - overlap_sec * i, 0)
            duration = base_duration + (overlap_sec if i < chunks_count - 1 else 0)
            out_file = os.path.join(temp_dir, f"part_{i+1}_{self.video_id}.mp4")
            cmd = ['ffmpeg', '-y', '-ss', str(start), '-i', input_path, '-t', str(duration), '-c', 'copy', out_file]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            paths.append(out_file)

        return paths, temp_dir

    async def cleanup(self):
        # Clean up video parts and main file
        for file in self.temp_files + [self.filename]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                    log.info(f"[CLEANUP] Removed file: {file}")
                except Exception as e:
                    log.warning(f"[CLEANUP] Could not remove {file}: {e}")
        
        # Clean up temporary split directories
        for d in self.temp_dirs:
            if os.path.exists(d):
                try:
                    os.rmdir(d)
                    log.info(f"[CLEANUP] Removed directory: {d}")
                except Exception as e:
                    log.warning(f"[CLEANUP] Could not remove {d}: {e}")

# --- Telegram Handlers ---
async def check_cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        log.warning(f"[BLOCKED] Unauthorized user {user_id} tried /checkcookies")
        return

    status_msg = await update.message.reply_text("🔍 Checking cookies...")

    result = init_cookies()

    # --- Expiry info for important cookies only ---
    important_cookies = {
        "LOGIN_INFO", "SAPISID", "HSID", "SSID",
        "__Secure-3PAPISID", "__Secure-3PSID", "__Secure-3PSIDCC"
    }

    expiry_info = "❓ Unable to determine expiry of important cookies."
    try:
        soonest_expiry = float("inf")
        soonest_name = None

        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 6:
                    name = parts[5]
                    if name in important_cookies:
                        try:
                            expiry = int(parts[4])
                            if expiry < soonest_expiry:
                                soonest_expiry = expiry
                                soonest_name = name
                        except ValueError:
                            continue

        if soonest_expiry != float("inf"):
            expiry_dt = datetime.utcfromtimestamp(soonest_expiry)
            time_left = expiry_dt - datetime.utcnow()
            if time_left.total_seconds() > 0:
                human_readable = str(time_left).split('.')[0]
                expiry_info = (
                    f"🕒 Soonest important cookie expiry:\n"
                    f"• `{soonest_name}` expires in: {human_readable}\n"
                    f"(UTC: {expiry_dt.strftime('%Y-%m-%d %H:%M:%S')})\n\n"
                    f"✅ Other cookies may still work after this, "
                    f"but age-restricted content might fail."
                )
                log.info(f"[COOKIES] Soonest important cookie '{soonest_name}' expires in {human_readable} (UTC: {expiry_dt})")
            else:
                expiry_info = f"⚠️ Important cookie `{soonest_name}` already expired.\nBot may fail on age-restricted videos."
                log.warning(f"[COOKIES] Important cookie '{soonest_name}' expired at {expiry_dt} UTC")
        else:
            expiry_info = "⚠️ No important auth cookies found in cookies.txt."
            log.warning("[COOKIES] No important cookies like LOGIN_INFO or SAPISID found")
    except Exception as e:
        log.error(f"[COOKIES] Failed to parse cookie expiry: {e}", exc_info=True)

    final_msg = (
        f"✅ Cookies are valid and working.\n\n{expiry_info}"
        if result else
        f"❌ Cookies are missing or invalid.\n\n{expiry_info}"
    )

    try:
        await status_msg.edit_text(final_msg, parse_mode='Markdown')
    except Exception as e:
        log.warning(f"[EDIT_FAIL] Could not edit cookie status message: {e}")
        await update.message.reply_text(final_msg, parse_mode='Markdown')

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info(f"[COMMAND] /id from user {update.effective_user.id} in chat {update.effective_chat.id}")
    await update.message.reply_text(f"👤 User ID: `{update.effective_user.id}`\n💬 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')

async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message or update.channel_post
    
    if not message:
         log.warning("Received update without message or channel_post in download_command")
         return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log.info(f"[COMMAND] /download from user {user_id} in chat {chat_id}")

    if not is_allowed(update):
        log.warning(f"[BLOCKED] Unauthorized user {user_id}")
        await message.reply_text("🚫 Not authorized.")
        return

    if not context.args:
        await message.reply_text("📎 Please provide a YouTube link.")
        return

    url = clean_youtube_url(context.args[0])
    if not url:
        await message.reply_text("❌ Invalid YouTube URL.")
        return

    video_id = get_video_id(url)
    if not video_id:
        await message.reply_text("❌ Could not extract video ID.")
        return

    # --- Check for existing/processing tasks ---
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("SELECT status FROM processed_videos WHERE chat_id = ? AND video_id = ?", (chat_id, video_id))
        result = c.fetchone()
        status = result[0] if result else None
        log.info(f"[DB] Checked video {video_id} in chat {chat_id}. Status: {status}")

    if status == "processing":
        # Check if it's REALLY running or just stuck in DB
        if is_task_queued_or_running(chat_id, video_id):
             log.info(f"[SKIP] Task for video {video_id} actively processing/queued in chat {chat_id}")
             await message.reply_text("⏳ This video is currently being processed.", parse_mode="Markdown")
             return
        else:
            # Status is 'processing' but no active task found - maybe it crashed? Treat as retryable.
            log.warning(f"[STALE] Video {video_id} in chat {chat_id} marked 'processing' but no active task found. Allowing retry.")
            status = "failed"

    if status == "success":
        log.info(f"[RETRY] Already processed video {video_id} in chat {chat_id}")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔁 Download Again", callback_data=f"retry|{video_id}|{url}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{video_id}")
            ]
        ])
        await message.reply_text(
            "✅ This video was already processed in this chat.\nDo you want to download it again?",
            reply_markup=keyboard
        )
        return

    # --- Proceed with new download (status is None or failed/stale 'processing') ---
    status_msg = await message.reply_text("✅ Queued...", parse_mode="Markdown")
    message_id = message.message_id

    mark_as_processed(chat_id, video_id, message_id, "processing")

    task = DownloadTask(update, context, url, status_msg, message_id)
    running_tasks.add(task)
    task_queue.put_nowait(task)

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    log.info(f"[COMMAND] /logs from user {update.effective_user.id} in chat {update.effective_chat.id}")
    one_hour_ago = datetime.now() - timedelta(minutes=60)
    with open(LOG_FILE) as f:
        lines = [line for line in f if parse_log_time(line) >= one_hour_ago]

    if not lines:
        await update.message.reply_text("✅ No logs in the last 60 minutes.")
        return

    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) < 3900:
            current += line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)

    for chunk in chunks:
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode='Markdown')

async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    log.info(f"[COMMAND] /tasks from user {update.effective_user.id} in chat {update.effective_chat.id}")
    if not running_tasks:
        await update.message.reply_text("✅ No tasks running.")
        return

    lines = [f"👤 User: {t.user_id}, ⏱️ Started: {t.created_at.strftime('%H:%M:%S')}, 🔗 URL: {t.url}" for t in running_tasks]
    await update.message.reply_text("\n".join(lines))

async def message_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text if update.message else ""
    chat = update.effective_chat.id
    log.info(f"[MSG] From user {user.id} in chat {chat}: {text}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query: CallbackQuery = update.callback_query
    await query.answer()

    data = query.data.split("|")
    action = data[0]
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if action == "cancel":
        video_id = data[1] if len(data) > 1 else "unknown"
        log.info(f"[CANCEL] User {user_id} canceled re-download for video {video_id} in chat {chat_id}")
        await query.edit_message_text("❌ Re-download canceled")
        return

    if action == "retry" and len(data) == 3:
        video_id, url = data[1], data[2]
        log.info(f"[RETRY] User {user_id} requested re-download of video {video_id} in chat {chat_id}")
        try:
            await query.edit_message_text("🔁 Re-downloading...")
            original_retry_message_id = query.message.message_id

            mark_as_processed(chat_id, video_id, original_retry_message_id, "processing")

            task = DownloadTask(update, context, url, query.message, original_retry_message_id)
            running_tasks.add(task)
            task_queue.put_nowait(task)

        except Exception as e:
            log.error(f"[RETRY_FAIL] Could not start retry for {video_id}: {e}", exc_info=True)
            await query.edit_message_text("❌ Failed to start retry.")
    else:
        await query.edit_message_text("❌ Invalid action.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error(f"Update {update} caused error {context.error}", exc_info=context.error)

# --- Queues & Worker ---
task_queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
running_tasks: set[DownloadTask] = set()

async def worker_loop():
    while True:
        task = await task_queue.get()
        await task.run()
        task_queue.task_done()

async def start_worker(app: Application):
    asyncio.create_task(worker_loop())

# --- Launch ---
if __name__ == "__main__":
    log.info("✅ Logger initialized.")
    app: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(start_worker)
        .connect_timeout(10)  # Timeout for establishing a connection (seconds)
        .read_timeout(60)     # Timeout for reading data from the server (seconds)
        .write_timeout(60)    # Timeout for writing data to the server (seconds)
        .build()
    )

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("download", download_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("checkcookies", check_cookies_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_logger))

    log.warning("✅ Bot started.")
    app.run_polling()
