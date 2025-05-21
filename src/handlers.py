import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
import asyncio

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ContextTypes, Application

from .tasks.task import Task
from .tasks.download_task import DownloadTask
from .utils.logging_utils import log
from .utils.config_utils import ALLOWED_USERS, TARGET_CHANNEL
from .utils.db_utils import init_db, mark_as_processed
from .utils.cookies_utils import cookies_available, COOKIES_FILE
from .utils.utils import clean_youtube_url, parse_log_time, get_video_id

# Initialize DB on import
init_db()

# In-memory queues & active tasks
task_queue: asyncio.Queue[Task] = asyncio.Queue()
running_tasks: set[Task] = set()

def is_allowed(update: Update) -> bool:
    """
    Check whether the user and chat are permitted.
    
    :param update: Telegram Update object.
    :return: True if user in ALLOWED_USERS and chat is the user or the target channel.
    """
    uid = update.effective_user.id
    cid = update.effective_chat.id
    return uid in ALLOWED_USERS and (cid == uid or cid == TARGET_CHANNEL)

def is_task_queued_or_running(chat_id: int, video_id: str) -> bool:
    """
    Determine if a given video task is already queued or actively running.
    
    :param chat_id: Telegram chat ID.
    :param video_id: YouTube video ID.
    :return: True if found in running_tasks or task_queue.
    """
    # We need to check if the task is a DownloadTask before accessing video_id
    return any(
        isinstance(t, DownloadTask) and 
        t.video_id == video_id and 
        t.update.effective_chat.id == chat_id
        for t in running_tasks.union({item for item in task_queue._queue})
    )


async def check_cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /checkcookies: validate and report cookie status.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    uid = update.effective_user.id
    if uid not in ALLOWED_USERS:
        log.warning(f"[BLOCKED] Unauthorized user {uid} tried /checkcookies")
        return

    status_msg = await update.message.reply_text("🔍 Checking cookies...")
    valid = cookies_available

    # Gather expiry info for key cookies
    important = {
        "LOGIN_INFO", "SAPISID", "HSID", "SSID",
        "__Secure-3PAPISID", "__Secure-3PSID", "__Secure-3PSIDCC"
    }
    expiry_info = "❓ Unable to determine expiry of important cookies."
    try:
        soon_exp = float("inf")
        soon_name = None
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 6 and parts[5] in important:
                    try:
                        exp = int(parts[4])
                        if exp < soon_exp:
                            soon_exp, soon_name = exp, parts[5]
                    except ValueError:
                        continue

        if soon_name:
            dt = datetime.utcfromtimestamp(soon_exp)
            delta = dt - datetime.utcnow()
            if delta.total_seconds() > 0:
                expiry_info = (
                    f"🕒 Soonest important cookie expiry:\n"
                    f"• `{soon_name}` in {str(delta).split('.')[0]}\n"
                    f"(UTC: {dt.strftime('%Y-%m-%d %H:%M:%S')})\n\n"
                    "✅ Other cookies may still work after this, but age-restricted content might fail."
                )
            else:
                expiry_info = (
                    f"⚠️ Important cookie `{soon_name}` already expired (UTC {dt}).\n"
                    "Bot may fail on age-restricted videos."
                )
    except Exception as e:
        log.error(f"[COOKIES] Failed to parse expiry: {e}", exc_info=True)

    final = (
        f"✅ Cookies are valid and working.\n\n{expiry_info}"
        if valid else
        f"❌ Cookies are missing or invalid.\n\n{expiry_info}"
    )

    try:
        await status_msg.edit_text(final, parse_mode='Markdown')
    except Exception:
        await update.message.reply_text(final, parse_mode='Markdown')


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /id: report user & chat IDs.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    log.info(f"[COMMAND] /id from user {update.effective_user.id} in chat {update.effective_chat.id}")
    await update.message.reply_text(
        f"👤 User ID: `{update.effective_user.id}`\n"
        f"💬 Chat ID: `{update.effective_chat.id}`",
        parse_mode='Markdown'
    )


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /download: enqueue a YouTube download task.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    message = update.message or update.channel_post  # type: Message
    if not message:
        log.warning("[COMMAND] No message found in download_command")
        return

    uid = update.effective_user.id
    cid = update.effective_chat.id
    log.info(f"[COMMAND] /download from user {uid} in chat {cid}")

    if not is_allowed(update):
        log.warning(f"[BLOCKED] Unauthorized user {uid}")
        await message.reply_text("🚫 Not authorized.")
        return

    if not context.args:
        await message.reply_text("📎 Please provide a YouTube link.")
        return

    url = clean_youtube_url(context.args[0])
    if not url:
        await message.reply_text("❌ Invalid YouTube URL.")
        return

    vid = get_video_id(url)
    if not vid:
        await message.reply_text("❌ Could not extract video ID.")
        return

    # DB status check
    with closing(sqlite3.connect(os.getenv('DB_PATH', 'data/bot.db'))) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT status FROM processed_videos WHERE chat_id = ? AND video_id = ?",
            (cid, vid)
        )
        row = c.fetchone()
        status = row[0] if row else None
        log.info(f"[DB] Video {vid} in chat {cid} status: {status}")

    if status == "processing" and is_task_queued_or_running(cid, vid):
        await message.reply_text("⏳ This video is currently being processed.", parse_mode="Markdown")
        return
    if status == "success":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔁 Download Again", callback_data=f"retry|{vid}|{url}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{vid}")
        ]])
        await message.reply_text(
            "✅ This video was already processed.\nDownload again?",
            reply_markup=keyboard
        )
        return

    status_msg = await message.reply_text("✅ Queued...", parse_mode="Markdown")
    mark_as_processed(cid, vid, message.message_id, "processing")

    task = DownloadTask(update, context, status_msg, url, message.message_id)
    running_tasks.add(task)
    await task_queue.put(task)


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /logs: show last 60 minutes of logs.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    if not is_allowed(update):
        return

    log.info(f"[COMMAND] /logs from user {update.effective_user.id}")
    cutoff = datetime.now() - timedelta(hours=1)
    with open(log.handlers[0].baseFilename) as f:  # assume file handler first
        lines = [ln for ln in f if parse_log_time(ln) >= cutoff]

    if not lines:
        return await update.message.reply_text("✅ No logs in the last 60 minutes.")

    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) < 3900:
            cur += ln
        else:
            chunks.append(cur)
            cur = ln
    if cur:
        chunks.append(cur)

    for chunk in chunks:
        await update.message.reply_text(f"```\n{chunk}```", parse_mode='Markdown')


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /tasks: list currently running tasks.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    if not is_allowed(update):
        return

    log.info(f"[COMMAND] /tasks from user {update.effective_user.id}")
    if not running_tasks:
        return await update.message.reply_text("✅ No tasks running.")

    lines = [
        f"👤 {t.user_id}, ⏱️ {t.created_at.strftime('%H:%M:%S')}, 🔗 {t.url}"
        for t in running_tasks
    ]
    await update.message.reply_text("\n".join(lines))


async def message_logger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Log every non-command text message.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    text = update.message.text if update.message else ""
    log.info(f"[MSG] From {update.effective_user.id} in {update.effective_chat.id}: {text}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle inline-button callbacks for retry/cancel.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    query: CallbackQuery = update.callback_query  # type: ignore
    await query.answer()
    parts = query.data.split("|")
    action = parts[0]
    cid = query.message.chat.id
    uid = query.from_user.id

    if action == "cancel":
        vid = parts[1] if len(parts) > 1 else "unknown"
        log.info(f"[CANCEL] User {uid} canceled {vid}")
        return await query.edit_message_text("❌ Canceled")

    if action == "retry" and len(parts) == 3:
        vid, url = parts[1], parts[2]
        log.info(f"[RETRY] User {uid} retry {vid}")
        await query.edit_message_text("🔁 Re-downloading...")
        mark_as_processed(cid, vid, query.message.message_id, "processing")
        task = DownloadTask(update, context, query.message, url, query.message.message_id)
        running_tasks.add(task)
        await task_queue.put(task)
    else:
        await query.edit_message_text("❌ Invalid action.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Global error handler.

    :param update: The update that caused the error.
    :param context: Callback context containing the error.
    """
    log.error(f"[ERROR] Update {update} caused error {context.error}", exc_info=context.error)


async def worker_loop() -> None:
    """
    Background worker pulling tasks off the queue.
    """
    while True:
        task = await task_queue.get()
        await task.run()
        running_tasks.discard(task)
        task_queue.task_done()


async def start_worker(app: Application) -> None:
    """
    Kick off the worker loop after the bot starts.
    """
    asyncio.create_task(worker_loop())
