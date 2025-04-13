import os
import re
import json
import math
import asyncio
import logging
import tempfile
import traceback
import subprocess
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse, parse_qs

from telegram import Update, Message, InputFile
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          ContextTypes, Application)
import yt_dlp

# --- Logging Configuration ---
LOG_FILE = "bot.log"
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("ytbot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.bot").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._application").setLevel(logging.WARNING)

# --- Config Loading ---
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN: str = config["bot_token"]
ALLOWED_USERS: set[int] = set(config["allowed_users"])
TARGET_CHANNEL: str = config["target_channel"]

# --- Utility Functions ---
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


class DownloadTask:
    def __init__(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, status_msg: Message):
        self.update = update
        self.context = context
        self.url = url
        self.status_msg = status_msg
        self.filename = "video.mp4"
        self.temp_files: list[str] = []
        self.temp_dirs: list[str] = []
        self.created_at = datetime.now()
        self.user_id = update.effective_user.id

    async def run(self):
        try:
            await asyncio.wait_for(self._process(), timeout=600)  # 10 min timeout
        except asyncio.TimeoutError:
            log.warning("[TASK] Task timed out")
            await self.status_msg.edit_text("❌ Task timed out after 10 minutes.", parse_mode="Markdown")
        except Exception as e:
            log.error(f"[TASK] Error: {e}")
            log.debug(traceback.format_exc())
            await self.status_msg.edit_text(f"❌ Error: {e}", parse_mode="Markdown")
        finally:
            await self.cleanup()
            running_tasks.discard(self)

    async def _process(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)

        info = yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}).extract_info(self.url, download=False)
        title = info.get("title", "Untitled")

        await self.status_msg.edit_text("⏬ Downloading video (360p)...", parse_mode="Markdown")
        ydl_opts = {
            'format': 'best[height<=360][ext=mp4][tbr<=600]/best[ext=mp4]/best',
            'outtmpl': self.filename,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.url])

        if not os.path.exists(self.filename):
            raise FileNotFoundError("Downloaded file not found")

        size_mb = os.path.getsize(self.filename) / (1024 * 1024)
        target_chat_id = self.update.effective_chat.id if self.update.effective_chat.type == "private" else int(TARGET_CHANNEL)

        if size_mb > 50:
            await self.status_msg.edit_text(f"📦 Downloaded ({size_mb:.1f} MB). Splitting...", parse_mode="Markdown")
            paths, temp_dir = self.split_video(self.filename)
            self.temp_dirs.append(temp_dir)
            self.temp_files.extend(paths)
            for idx, part in enumerate(paths, 1):
                await self.status_msg.edit_text(f"📤 Sending part {idx}/{len(paths)}...", parse_mode="Markdown")
                await self.context.bot.send_video(target_chat_id, video=InputFile(part), caption=f"🎬 *{title}* ({idx}/{len(paths)})", parse_mode='Markdown')
        else:
            await self.context.bot.send_video(target_chat_id, video=InputFile(self.filename), caption=f"🎬 *{title}*", parse_mode='Markdown')
            await self.status_msg.edit_text("✅ Sent to Telegram", parse_mode="Markdown")

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
            out_file = os.path.join(temp_dir, f"part_{i+1}.mp4")

            cmd = ['ffmpeg', '-y', '-ss', str(start), '-i', input_path, '-t', str(duration), '-c', 'copy', out_file]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            paths.append(out_file)

        return paths, temp_dir

    async def cleanup(self):
        for file in self.temp_files + [self.filename]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                    log.info(f"[CLEANUP] Removed file: {file}")
                except Exception as e:
                    log.warning(f"[CLEANUP] Could not remove {file}: {e}")
        for d in self.temp_dirs:
            if os.path.exists(d):
                try:
                    os.rmdir(d)
                    log.info(f"[CLEANUP] Removed directory: {d}")
                except Exception as e:
                    log.warning(f"[CLEANUP] Could not remove {d}: {e}")

# --- Telegram Command Handlers ---
async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👤 User ID: `{update.effective_user.id}`\n💬 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')

async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("🚫 Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("📎 Please provide a YouTube link.")
        return

    url = clean_youtube_url(context.args[0])
    if not url:
        await update.message.reply_text("❌ Invalid YouTube URL.")
        return

    status_msg = await update.message.reply_text("✅ Queued...", parse_mode="Markdown")
    task = DownloadTask(update, context, url, status_msg)
    running_tasks.add(task)
    task_queue.put_nowait(task)

async def send_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    one_hour_ago = datetime.now() - timedelta(minutes=60)
    with open(LOG_FILE) as f:
        lines = [line for line in f if parse_log_time(line) >= one_hour_ago]

    if not lines:
        await update.message.reply_text("✅ No logs in the last 60 minutes.")
        return

    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) < 4000:
            current_chunk += line
        else:
            chunks.append(current_chunk)
            current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode='Markdown')

def parse_log_time(line: str) -> datetime:
    try:
        timestamp = line.split()[0] + " " + line.split()[1]
        return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f")
    except Exception:
        return datetime.min

async def list_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    if not running_tasks:
        await update.message.reply_text("✅ No tasks running.")
        return

    lines = [f"👤 User: {t.user_id}, ⏱️ Started: {t.created_at.strftime('%H:%M:%S')}, 🔗 URL: {t.url}" for t in running_tasks]
    await update.message.reply_text("\n".join(lines))

# --- Task Worker Loop ---
task_queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
running_tasks: set[DownloadTask] = set()

async def worker_loop():
    while True:
        task = await task_queue.get()
        await task.run()
        task_queue.task_done()

async def start_worker(app: Application):
    asyncio.create_task(worker_loop())

# --- Bot Setup ---
if __name__ == "__main__":
    app: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(start_worker)
        .build()
    )

    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("download", download_command))
    app.add_handler(CommandHandler("logs", send_logs_command))
    app.add_handler(CommandHandler("tasks", list_tasks_command))

    log.warning("✅ Bot started.")
    app.run_polling()
