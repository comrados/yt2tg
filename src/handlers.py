import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
import asyncio

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ContextTypes, Application

from .tasks.task import Task
from .tasks.download_task import DownloadTask
from .tasks.transcript_task import TranscriptTask

from .utils.logging_utils import log
from .utils.config_utils import ALLOWED_USERS, TARGET_CHANNEL
from .utils.db_utils import (
    init_db, mark_as_processed, is_transcript_processed, 
    mark_transcript_processed, get_user_language_preference, 
    set_user_language_preference
)
from .utils.language_utils import (
    get_language_name, normalize_language, is_valid_language,
    get_popular_languages, get_language_suggestions
)
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

async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /setlang: set user's preferred transcript language."""
    uid = update.effective_user.id
    if uid not in ALLOWED_USERS:
        log.warning(f"[BLOCKED] Unauthorized user {uid} tried /setlang")
        return

    if not context.args:
        # Show current preference and available languages
        current_pref = get_user_language_preference(uid)
        current_display = get_language_name(current_pref) if current_pref else "Not set"
        
        popular = get_popular_languages()
        lang_list = ", ".join([f"{name} ({code})" for code, name in popular[:12]])
        
        await update.message.reply_text(
            f"🌐 **Current language preference:** {current_display}\n\n"
            f"**Popular languages:** {lang_list}\n\n"
            f"**Usage:** `/setlang <language>`\n"
            f"You can use either language name or language code.\n\n"
            f"**Examples:**\n"
            f"• `/setlang english`\n"
            f"• `/setlang en`\n"
            f"• `/setlang russian`\n"
            f"• `/setlang ru`",
            parse_mode='Markdown'
        )
        return

    lang_input = " ".join(context.args).strip()
    
    # Try to normalize the language first
    lang_code, lang_name = normalize_language(lang_input)
    
    if not lang_code:  # Only check this after normalize_language
        # Try to provide suggestions
        suggestions = get_language_suggestions(lang_input, 5)
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\n**Did you mean:**\n" + "\n".join([
                f"• {name} (`{code}`)" for code, name in suggestions
            ])
        
        await update.message.reply_text(
            f"❌ Unknown language: **{lang_input}**\n"
            f"Use `/setlang` without arguments to see available languages.{suggestion_text}",
            parse_mode='Markdown'
        )
        return
    
    set_user_language_preference(uid, lang_code)
    
    await update.message.reply_text(
        f"✅ Language preference set to: **{lang_name}** (`{lang_code}`)\n\n"
        f"Now you can use `/transcript` to get transcripts in {lang_name}.",
        parse_mode='Markdown'
    )

async def getlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .utils.db_utils import get_user_language_preference
    uid = update.effective_user.id
    pref = get_user_language_preference(uid)
    if pref:
        await update.message.reply_text(f"🌐 Current preference: `{pref}` ({get_language_name(pref)})", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ No language preference set.", parse_mode='Markdown')

async def transcript_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /transcript: enqueue or retry a transcript task."""
    msg = update.message or update.channel_post
    uid = update.effective_user.id
    cid = update.effective_chat.id
    log.info(f"[COMMAND] /transcript from user {uid} in chat {cid}")

    if uid not in ALLOWED_USERS:
        log.warning(f"[BLOCKED] Unauthorized user {uid} for /transcript")
        return await msg.reply_text("🚫 Not authorized.")

    # Check if user has set preferred language
    user_lang_pref = get_user_language_preference(uid)
    if not user_lang_pref:
        popular = get_popular_languages()
        lang_examples = ", ".join([name for _, name in popular[:5]])
        await msg.reply_text(
            f"🌐 **Please set your preferred language first!**\n\n"
            f"Use: `/setlang <language>`\n\n"
            f"**Examples:**\n"
            f"• `/setlang english`\n"
            f"• `/setlang spanish`\n"
            f"• `/setlang russian`\n\n"
            f"**Popular languages:** {lang_examples}\n\n"
            f"After setting language, run `/transcript <youtube_url>` again.",
            parse_mode='Markdown'
        )
        return

    if not context.args:
        lang_name = get_language_name(user_lang_pref)
        return await msg.reply_text(
            f"📎 **Please provide a YouTube link.**\n\n"
            f"**Usage:** `/transcript <youtube_url>`\n"
            f"Current language: **{lang_name}** (`{user_lang_pref}`)\n\n"
            f"Change language with `/setlang <language>`",
            parse_mode='Markdown'
        )

    url = clean_youtube_url(context.args[0])
    if not url:
        return await msg.reply_text("❌ Invalid YouTube URL.")

    vid = get_video_id(url)
    if not vid:
        return await msg.reply_text("❌ Could not extract video ID.")

    # Check if transcript already processed for this language
    if is_transcript_processed(cid, vid, user_lang_pref):
        lang_name = get_language_name(user_lang_pref)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔁 Retry Transcript", callback_data=f"retry_transcript|{vid}|{url}|{user_lang_pref}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_transcript|{vid}")
        ]])
        return await msg.reply_text(
            f"✅ Transcript in **{lang_name}** was already generated.\n\nRetry?",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    # Check if currently processing
    is_already_running_or_queued = any(
        isinstance(t, TranscriptTask) and 
        t.video_id == vid and 
        t.update.effective_chat.id == cid and
        t.target_lang_code == user_lang_pref
        for t in running_tasks.union({item for item in task_queue._queue})
    )
    if is_already_running_or_queued:
        lang_name = get_language_name(user_lang_pref)
        await msg.reply_text(
            f"⏳ This transcript in **{lang_name}** is currently being processed.", 
            parse_mode="Markdown"
        )
        return

    lang_name = get_language_name(user_lang_pref)
    status_msg = await msg.reply_text(
        f"✅ Queued transcript task for **{lang_name}**…", 
        parse_mode='Markdown'
    )
    mark_transcript_processed(cid, vid, status_msg.message_id, "processing", user_lang_pref)
    task = TranscriptTask(update, context, status_msg, url, user_lang_pref)
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
    if update.effective_user.id not in ALLOWED_USERS:
        log.warning(f"[BLOCKED] Unauthorized user {update.effective_user.id} tried /tasks")
        return

    log.info(f"[COMMAND] /tasks from user {update.effective_user.id}")
    
    # Ensure running_tasks contains actual Task objects
    active_task_details = []
    for t in running_tasks:
        if hasattr(t, 'user_id') and hasattr(t, 'created_at') and hasattr(t, 'url'):
            # Check if created_at is datetime or float (from asyncio.loop.time())
            created_time_str = ""
            if isinstance(t.created_at, datetime):
                created_time_str = t.created_at.strftime('%H:%M:%S')
            elif isinstance(t.created_at, float): # Handle asyncio.loop.time()
                # This won't be a human-readable time directly, maybe just indicate it's running
                 created_time_str = "Running" # Or convert to relative time if needed
            
            task_type = type(t).__name__ # Get class name (e.g., DownloadTask, TranscriptTask)
            task_url_display = t.url if len(t.url) < 50 else t.url[:47] + "..."

            active_task_details.append(
                f"👤 {t.user_id}, ⏱️ {created_time_str}, 🔗 {task_url_display} ({task_type})"
            )
        else: # Fallback for generic Task objects or if attributes are missing
            active_task_details.append(f"Task: {type(t).__name__} (details unavailable)")


    if not active_task_details:
        return await update.message.reply_text("✅ No tasks running.")
        
    await update.message.reply_text("\n".join(active_task_details))


async def message_logger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Log every non-command text message.

    :param update: Telegram Update.
    :param context: Callback context.
    """
    text = update.message.text if update.message else ""
    log.info(f"[MSG] From {update.effective_user.id} in {update.effective_chat.id}: {text}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline-button callbacks for retry/cancel."""
    query: CallbackQuery = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action = parts[0]
    cid = query.message.chat.id
    uid = query.from_user.id

    if action == "cancel":
        vid = parts[1] if len(parts) > 1 else "unknown"
        log.info(f"[CANCEL] User {uid} canceled download {vid}")
        return await query.edit_message_text("❌ Download Canceled")

    if action == "retry" and len(parts) == 3:
        vid, url = parts[1], parts[2]
        log.info(f"[RETRY] User {uid} retry download {vid}")
        await query.edit_message_text("🔁 Re-downloading...")
        mark_as_processed(cid, vid, query.message.message_id, "processing")
        task = DownloadTask(update, context, query.message, url, query.message.message_id)
        running_tasks.add(task)
        await task_queue.put(task)
        return

    if action == "cancel_transcript":
        vid = parts[1] if len(parts) > 1 else "unknown"
        log.info(f"[CANCEL TRANSCRIPT] User {uid} canceled transcript for {vid}")
        return await query.edit_message_text("❌ Transcript canceled")

    if action == "retry_transcript" and len(parts) >= 3:
        vid, url = parts[1], parts[2]
        target_lang_code = parts[3] if len(parts) > 3 else get_user_language_preference(uid) or "en"
        lang_name = get_language_name(target_lang_code)
        log.info(f"[RETRY TRANSCRIPT] User {uid} retry transcript for {vid} in {lang_name}")
        await query.edit_message_text(f"🔁 Re-queuing transcript for **{lang_name}**…", parse_mode='Markdown')
        mark_transcript_processed(cid, vid, query.message.message_id, "processing", target_lang_code)
        task = TranscriptTask(update, context, query.message, url, target_lang_code)
        running_tasks.add(task)
        await task_queue.put(task)
        return
    
    log.warning(f"[BUTTON_HANDLER] Unhandled action: {query.data}")
    await query.edit_message_text("❌ Invalid or unhandled action.")

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
