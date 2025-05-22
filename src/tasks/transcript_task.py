import os
import tempfile
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
from google.genai import Client
import yt_dlp

from telegram import Message, Update
from telegram.ext import ContextTypes

from .task import Task
from ..utils.config_utils import GEMINI_API_KEY, CHOSEN_MODEL_NAME
from ..utils.logging_utils import log
from ..utils.db_utils import mark_transcript_processed
from ..utils.utils import get_video_id

FIRST_PROMPT = """\
This is a YouTube video script. Please correct any grammatical, spelling, or punctuation errors. Remove colloquial language to make it more formal and easier to read. Structure the text by identifying the main ideas and dividing it into clear paragraphs or "chapters."

Each paragraph should begin with a numbered title summarizing its content, followed by the revised and cleaned-up content.

The output format should be:

Title of Paragraph 1
Content of paragraph 1...

Title of Paragraph 2
Content of paragraph 2...

Do not include any timecodes or markdown separators (like "---"). The output should be a clean, structured text version of the script.
"""

SECOND_PROMPT = """\
Please process the structured text that was output from the first prompt. Your task is to shorten the content while preserving the overall structure and paragraph titles.

Keep only the essential information that is directly relevant to the main topic. Remove any filler, repetition, or side details.

Maintain the same format:

Title of Paragraph 1
Concise content of paragraph 1...

Title of Paragraph 2
Concise content of paragraph 2...

Focus on clarity, brevity, and staying on-topic.
"""

class TranscriptTask(Task):
    """
    Represents a single transcript-fetch-and-process job.
    """

    def __init__(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        status_msg: Message,
        url: str
    ) -> None:
        super().__init__(update, context, status_msg)
        self.url = url
        self.video_id = get_video_id(url)
        # fetch title metadata
        ydl_opts = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        self.title = info.get("title", "Untitled")
        self.client = Client(api_key=GEMINI_API_KEY)
        self.temp_dir = tempfile.mkdtemp(prefix="transcript_")
        self.created_at = datetime.now()

    async def run(self) -> None:
        log.info(f"[TRANSCRIPT] Started for {self.video_id} – {self.title}")
        if not self.video_id:
            await self.status_msg.edit_text("❌ Invalid YouTube URL.")
            return
        try:
            await self._process()
            # mark success
            mark_transcript_processed(
                self.update.effective_chat.id,
                self.video_id,
                self.status_msg.message_id,
                "success"
            )
        except NoTranscriptFound:
            log.warning(f"[TRANSCRIPT] Unavailable for {self.video_id}")
            await self.status_msg.edit_text("❌ Transcript not available for this video.")
            mark_transcript_processed(
                self.update.effective_chat.id,
                self.video_id,
                self.status_msg.message_id,
                "failed"
            )
        except Exception as e:
            log.error(f"[TRANSCRIPT] Error for {self.video_id}: {e}", exc_info=True)
            await self.status_msg.edit_text(f"❌ Failed: {e}")
            mark_transcript_processed(
                self.update.effective_chat.id,
                self.video_id,
                self.status_msg.message_id,
                "failed"
            )
        finally:
            await self.cleanup()

    async def _process(self) -> None:
        # 1) fetch raw transcript
        segs = YouTubeTranscriptApi.get_transcript(self.video_id)
        raw_script = "\n".join(seg["text"] for seg in segs)

        await self.status_msg.edit_text("⏳ Cleaning and structuring…")

        # 2) first LLM pass
        response_full = self.client.models.generate_content(
            model=CHOSEN_MODEL_NAME, # Use the chosen model name
            contents=FIRST_PROMPT + "\n\n" + raw_script
        )
        full = response_full.text

        await self.status_msg.edit_text("⏳ Summarizing…")

        # 3) second LLM pass
        response_short = self.client.models.generate_content(
            model=CHOSEN_MODEL_NAME, # Use the chosen model name
            contents=SECOND_PROMPT + "\n\n" + full
        )
        short = response_short.text

        # 4) write out files
        full_path  = os.path.join(self.temp_dir, f"{self.video_id}_full.txt")
        short_path = os.path.join(self.temp_dir, f"{self.video_id}_short.txt")
        with open(full_path,  "w", encoding="utf-8") as f: f.write(full)
        with open(short_path, "w", encoding="utf-8") as f: f.write(short)

        # 5) send both in one message each (with title)
        caption = f"🎙 Transcript for: *{self.title}*"
        await self.context.bot.send_document(
            chat_id=self.update.effective_chat.id,
            document=open(full_path, "rb"),
            filename=os.path.basename(full_path),
            caption=caption,
            parse_mode="Markdown"
        )
        await self.context.bot.send_document(
            chat_id=self.update.effective_chat.id,
            document=open(short_path, "rb"),
            filename=os.path.basename(short_path),
            caption=caption + " (short)",
            parse_mode="Markdown"
        )

        await self.status_msg.edit_text("✅ Transcript delivered.")

    async def cleanup(self) -> None:
        # remove temp files and dir
        try:
            for fn in os.listdir(self.temp_dir):
                os.remove(os.path.join(self.temp_dir, fn))
            os.rmdir(self.temp_dir)
            log.info(f"[TRANSCRIPT CLEANUP] Removed {self.temp_dir}")
        except Exception as e:
            log.warning(f"[TRANSCRIPT CLEANUP] {e}")