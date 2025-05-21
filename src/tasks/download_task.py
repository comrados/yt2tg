import os
import re
import math
import tempfile
import subprocess
import asyncio
from datetime import datetime
from typing import List, Tuple, Optional

import sqlite3  # only for existence checks; actual DB writes via db_utils
import yt_dlp
from yt_dlp.utils import DownloadError
from telegram import Message, Update
from telegram.error import RetryAfter, TimedOut
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from .task import Task
from ..utils.logging_utils import log
from ..utils.cookies_utils import cookies_available, COOKIES_FILE
from ..utils.utils import get_video_id
from ..utils.config_utils import TARGET_CHANNEL
from ..utils.db_utils import mark_as_processed


class DownloadTask(Task):
    """
    Represents a single video-download-and-send job.
    """

    def __init__(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        status_msg: Message,
        url: str,
        original_message_id: int
    ) -> None:
        """
        Initialize a DownloadTask.

        :param update: Telegram Update object.
        :param context: Callback context.
        :param url: YouTube URL to download.
        :param status_msg: Message object to edit status updates.
        :param original_message_id: ID of the triggering message.
        """
        super().__init__(update, context, status_msg)
        self.update: Update = update
        self.context: ContextTypes.DEFAULT_TYPE = context
        self.url: str = url
        self.status_msg: Message = status_msg
        self.video_id: Optional[str] = get_video_id(url)
        self.filename: str = f"video_{self.video_id}.mp4"
        self.temp_files: List[str] = []
        self.temp_dirs: List[str] = []
        self.created_at: datetime = datetime.now()
        self.user_id: int = update.effective_user.id
        self.original_message_id: int = original_message_id

    def __hash__(self) -> int:
        return hash((self.update.effective_chat.id, self.video_id))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DownloadTask) and
            other.update.effective_chat.id == self.update.effective_chat.id and
            other.video_id == self.video_id
        )

    async def run(self) -> None:
        """
        Execute the full download → send → cleanup workflow.
        """
        try:
            log.info(f"[TASK] Started for user {self.user_id} in chat {self.update.effective_chat.id} | URL: {self.url}")
            await asyncio.wait_for(self._process(), timeout=600)
        except asyncio.TimeoutError:
            log.warning(f"[TASK] Timeout for video {self.video_id}")
            await self._safe_edit_status("❌ Task timed out after 10 minutes.")
            if self.video_id:
                mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "failed")
        except DownloadError as e:
            log.error(f"[TASK] DownloadError for {self.video_id}: {e}")
            await self._handle_download_error(str(e))
        except Exception as e:
            log.error(f"[TASK] General error for {self.video_id}: {e}", exc_info=True)
            await self._safe_edit_status("❌ Error processing video.")
            if self.video_id:
                mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "failed")
        finally:
            await self.cleanup()
            # running_tasks is managed in handlers; removal happens there

    async def _process(self) -> None:
        """
        Internal: perform download, splitting if needed, sending to Telegram.
        """
        # remove stale file
        if os.path.exists(self.filename):
            os.remove(self.filename)

        # prepare cookie options
        cookie_opts = {'cookiefile': COOKIES_FILE, 'no_write_cookie_file': False} if cookies_available else {}

        # fetch metadata
        info_opts = {'quiet': True, 'skip_download': True, **cookie_opts}
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)

        title = info.get("title", "Untitled")
        await self._safe_edit_status("⏬ Downloading video (360p)...")

        # download file
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

        # verify file
        if not os.path.exists(self.filename) or os.path.getsize(self.filename) < 1024:
            raise Exception("Downloaded file is missing or too small.")

        size_mb = os.path.getsize(self.filename) / (1024 * 1024)
        target_chat_id = (
            self.update.effective_chat.id
            if self.update.effective_chat.type == "private"
            else int(TARGET_CHANNEL)
        )

        if size_mb > 50:
            # split into parts
            await self._safe_edit_status(f"📦 Downloaded ({size_mb:.1f} MB). Splitting...")
            parts, tmp = self.split_video(self.filename)
            self.temp_dirs.append(tmp)
            self.temp_files.extend(parts)
            # send each part
            for idx, part in enumerate(parts, start=1):
                raw_caption = f"🎬 {title} ({idx}/{len(parts)})"
                caption = escape_markdown(raw_caption, version=2)
                await self._safe_edit_status(f"📤 Sending part {idx}/{len(parts)}...")
                success, msg_id = await self._send_video_with_retry(target_chat_id, part, caption)
                if not success:
                    raise Exception(f"Failed to send part {idx}/{len(parts)}")
        else:
            # send single file
            raw_caption = f"🎬 {title}"
            caption = escape_markdown(raw_caption, version=2)
            success, msg_id = await self._send_video_with_retry(target_chat_id, self.filename, caption)
            if not success:
                raise Exception("Failed to send the video.")

        # mark success
        if self.video_id:
            mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "success")
            await self._safe_edit_status("✅ Sent to Telegram")

    async def _send_video_with_retry(
        self,
        chat_id: int,
        file_path: str,
        caption: str
    ) -> Tuple[bool, Optional[int]]:
        """
        Attempt to send a video file, retrying on flood/timeouts.

        :param chat_id: Target Telegram chat ID.
        :param file_path: Path to the video file to send.
        :param caption: Markdown-escaped caption.
        :return: (success_flag, message_id or None)
        """
        max_retries = 5
        file_name = os.path.basename(file_path)

        for attempt in range(1, max_retries + 1):
            try:
                with open(file_path, 'rb') as f:
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
                wait = int(e.retry_after) + 1
                log.warning(f"[RETRY] Flood control ({attempt}/{max_retries}), waiting {wait}s for '{file_name}'")
                await asyncio.sleep(wait)
            except TimedOut as e:
                log.warning(f"[RETRY] Timed out ({attempt}/{max_retries}) for '{file_name}': {e}")
                await asyncio.sleep(10)
            except Exception as e:
                log.warning(f"[RETRY] Error ({attempt}/{max_retries}) sending '{file_name}': {e}")
                await asyncio.sleep(5)

        log.error(f"[FAIL] Giving up after {max_retries} retries on '{file_name}'")
        return False, None

    async def _safe_edit_status(self, text: str) -> None:
        """
        Safely edit the status message; swallow errors.

        :param text: New status text (Markdown).
        """
        try:
            await self.status_msg.edit_text(text, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"[EDIT_FAIL] Could not edit message: {e}")

    async def _handle_download_error(self, error_message: str) -> None:
        """
        Map YoutubeDL errors to user-friendly messages and update DB.

        :param error_message: Raw exception message.
        """
        if "Sign in to confirm your age" in error_message:
            if cookies_available:
                msg = "❌ Age restricted video failed. Cookies might be invalid/expired."
            else:
                msg = f"❌ Age restricted video failed. Add a valid '{COOKIES_FILE}'."
        elif "unavailable" in error_message.lower():
            msg = "❌ Video is unavailable."
        else:
            msg = f"❌ Download error: {error_message[:100]}"

        await self._safe_edit_status(msg)
        if self.video_id:
            mark_as_processed(self.update.effective_chat.id, self.video_id, self.original_message_id, "failed")

    def split_video(
        self,
        input_path: str,
        max_size_mb: int = 40,
        overlap_sec: int = 5
    ) -> Tuple[List[str], str]:
        """
        Split a large video into overlapping chunks under max_size_mb.

        :param input_path: Path to the input video file.
        :param max_size_mb: Maximum size per chunk (in MB).
        :param overlap_sec: Seconds of overlap between chunks.
        :return: (list of chunk file paths, temp directory path)
        """
        temp_dir = tempfile.mkdtemp()
        # get duration via ffmpeg
        result = subprocess.run(
            ['ffmpeg', '-i', input_path],
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True
        )
        match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', result.stderr)
        h, m, s = map(float, match.groups())
        total_dur = int(h*3600 + m*60 + s)

        file_size = os.path.getsize(input_path) / (1024*1024)
        chunks = math.ceil(file_size / max_size_mb)
        base_dur = total_dur / chunks

        paths: List[str] = []
        for i in range(chunks):
            start = max(i*base_dur - overlap_sec*i, 0)
            dur = base_dur + (overlap_sec if i < chunks-1 else 0)
            out = os.path.join(temp_dir, f"part_{i+1}_{self.video_id}.mp4")
            subprocess.run(
                ['ffmpeg', '-y', '-ss', str(start), '-i', input_path,
                 '-t', str(dur), '-c', 'copy', out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            paths.append(out)

        return paths, temp_dir

    async def cleanup(self) -> None:
        """
        Remove downloaded and split files and temporary directories.
        """
        for f in self.temp_files + [self.filename]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                    log.info(f"[CLEANUP] Removed file: {f}")
                except Exception as e:
                    log.warning(f"[CLEANUP] Could not remove {f}: {e}")

        for d in self.temp_dirs:
            if os.path.exists(d):
                try:
                    os.rmdir(d)
                    log.info(f"[CLEANUP] Removed directory: {d}")
                except Exception as e:
                    log.warning(f"[CLEANUP] Could not remove {d}: {e}")
