import os
import tempfile
import re
from datetime import datetime
from typing import Optional, List
import yt_dlp
from google.genai import Client

from telegram import Message, Update
from telegram.ext import ContextTypes

from .task import Task
from ..utils.config_utils import GEMINI_API_KEY, CHOSEN_MODEL_NAME
from ..utils.cookies_utils import COOKIES_FILE
from ..utils.logging_utils import log
from ..utils.db_utils import mark_transcript_processed
from ..utils.utils import get_video_id
from ..utils.language_utils import get_language_name

FIRST_PROMPT = """\
This is a YouTube video script. Please correct any grammatical, spelling, or punctuation errors. Remove colloquial language to make it more formal and easier to read. Structure the text by identifying the main ideas and dividing it into clear paragraphs or "chapters."

Each paragraph should begin with a numbered title summarizing its content, followed by the revised and cleaned-up content.

The output format should be:

Title of Paragraph 1
Content of paragraph 1...

Title of Paragraph 2
Content of paragraph 2...

Do not include any timecodes or markdown separators (like "---"). The output should be a clean, structured text version of the script.

If the provided text is not in {target_language}, please translate it to {target_language} while performing the above tasks.
"""

SECOND_PROMPT = """\
Please process the structured text that was output from the first prompt. Your task is to shorten the content while preserving the overall structure and paragraph titles.

Keep only the essential information that is directly relevant to the main topic. Remove any filler, repetition, or side details.

Maintain the same format:

Title of Paragraph 1
Concise content of paragraph 1...

Title of Paragraph 2
Concise content of paragraph 2...

Focus on clarity, brevity, and staying on-topic. Ensure the output is in {target_language}.
"""

class TranscriptTask(Task):
    """
    Represents a single transcript-fetch-and-process job using yt-dlp.
    """

    def __init__(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        status_msg: Message,
        url: str,
        target_lang_code: str
    ) -> None:
        super().__init__(update, context, status_msg)
        self.url = url
        self.video_id = get_video_id(url)
        self.target_lang_code = target_lang_code
        self.target_language = get_language_name(target_lang_code)
        
        # Add user_id for task tracking
        self.user_id = update.effective_user.id
        
        # fetch title metadata
        ydl_opts = {
            'quiet': True, 
            'skip_download': True,
            'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            self.title = info.get("title", "Untitled")
            # Sanitize title for filename use
            self.safe_title = self._sanitize_filename(self.title)
        except Exception as e:
            log.warning(f"[TRANSCRIPT] Could not fetch video title: {e}")
            self.title = "Untitled"
            self.safe_title = "Untitled"
            
        self.client = Client(api_key=GEMINI_API_KEY)
        self.temp_dir = tempfile.mkdtemp(prefix="transcript_")
        self.created_at = datetime.now()

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename by removing/replacing invalid characters."""
        # Remove or replace invalid characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Limit length to avoid filesystem issues
        return sanitized[:100] if len(sanitized) > 100 else sanitized

    async def _process(self) -> None:
        chat_id = self.update.effective_chat.id
        
        if not self.video_id:
            await self.status_msg.edit_text("❌ Invalid YouTube URL.")
            mark_transcript_processed(chat_id, self.video_id or "unknown", 
                                    self.status_msg.message_id, "failed", self.target_lang_code)
            return

        try:
            # 1) Download all available subtitles using yt-dlp
            await self.status_msg.edit_text("⏳ Downloading subtitles...")
            
            subtitle_file = await self._download_subtitles()
            if not subtitle_file:
                await self.status_msg.edit_text("❌ No subtitles available for this video.")
                mark_transcript_processed(chat_id, self.video_id, 
                                        self.status_msg.message_id, "failed", self.target_lang_code)
                return

            # 2) Parse subtitle file and extract text
            await self.status_msg.edit_text("⏳ Processing subtitles...")
            
            raw_script = self._parse_subtitle_file(subtitle_file)
            if not raw_script.strip():
                await self.status_msg.edit_text("❌ No text content found in subtitles.")
                mark_transcript_processed(chat_id, self.video_id, 
                                        self.status_msg.message_id, "failed", self.target_lang_code)
                return

            await self.status_msg.edit_text("⏳ Cleaning and structuring…")

            # 3) First LLM pass - clean and structure
            first_prompt = FIRST_PROMPT.format(target_language=self.target_language)
            response_full = self.client.models.generate_content(
                model=CHOSEN_MODEL_NAME,
                contents=first_prompt + "\n\n" + raw_script
            )
            full = response_full.text

            await self.status_msg.edit_text("⏳ Summarizing…")

            # 4) Second LLM pass - summarize
            second_prompt = SECOND_PROMPT.format(target_language=self.target_language)
            response_short = self.client.models.generate_content(
                model=CHOSEN_MODEL_NAME,
                contents=second_prompt + "\n\n" + full
            )
            short = response_short.text

            # 5) Create output files with user-friendly names
            lang_suffix = f"_{self.target_lang_code}" if self.target_lang_code != 'en' else ""
            full_filename = f"{self.safe_title}_full{lang_suffix}.txt"
            short_filename = f"{self.safe_title}_short{lang_suffix}.txt"
            
            full_path = os.path.join(self.temp_dir, full_filename)
            short_path = os.path.join(self.temp_dir, short_filename)
            
            with open(full_path, "w", encoding="utf-8") as f: 
                f.write(full)
            with open(short_path, "w", encoding="utf-8") as f: 
                f.write(short)

            # 6) Send both files
            caption = f"🎙 Transcript for: *{self.title}* ({self.target_language})"
            
            with open(full_path, "rb") as f:
                await self.context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=full_filename,
                    caption=caption,
                    parse_mode="Markdown"
                )
            
            with open(short_path, "rb") as f:
                await self.context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=short_filename,
                    caption=caption + " (short)",
                    parse_mode="Markdown"
                )

            await self.status_msg.edit_text("✅ Transcript delivered.")
            mark_transcript_processed(chat_id, self.video_id, 
                                    self.status_msg.message_id, "success", self.target_lang_code)

        except Exception as e:
            log.error(f"[TRANSCRIPT] Failed to process transcript: {e}")
            await self.status_msg.edit_text(f"❌ Failed to generate transcript: {str(e)}")
            mark_transcript_processed(chat_id, self.video_id, 
                                    self.status_msg.message_id, "failed", self.target_lang_code)

    async def _download_subtitles(self) -> Optional[str]:
        """Download subtitles and return path to best matching file."""
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'allsubtitles': True,
            'subtitlesformat': 'vtt',
            'outtmpl': os.path.join(self.temp_dir, '%(id)s.%(ext)s'),
            'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])
            
            # Find all downloaded subtitle files
            subtitle_files = [
                f for f in os.listdir(self.temp_dir) 
                if f.endswith('.vtt') and self.video_id in f
            ]
            
            if not subtitle_files:
                log.warning("[TRANSCRIPT] No subtitle files downloaded")
                return None
            
            # Find best matching subtitle file
            best_file = self._find_best_subtitle_file(subtitle_files)
            if best_file:
                log.info(f"[TRANSCRIPT] Selected subtitle file: {best_file}")
                return os.path.join(self.temp_dir, best_file)
            
            return None
                
        except Exception as e:
            log.error(f"[TRANSCRIPT] Failed to download subtitles: {e}")
            raise Exception(f"Could not download subtitles: {str(e)}")

    def _find_best_subtitle_file(self, subtitle_files: List[str]) -> Optional[str]:
        """Find the best subtitle file based on language preference."""
        # Priority order:
        # 1. Manual subtitles in target language
        # 2. Auto-generated subtitles in target language  
        # 3. Manual subtitles in English
        # 4. Auto-generated subtitles in English
        # 5. Any manual subtitles
        # 6. Any auto-generated subtitles
        
        manual_target = []
        auto_target = []
        manual_en = []
        auto_en = []
        manual_other = []
        auto_other = []
        
        for filename in subtitle_files:
            if f".{self.target_lang_code}." in filename:
                if "auto" in filename:
                    auto_target.append(filename)
                else:
                    manual_target.append(filename)
            elif ".en." in filename:
                if "auto" in filename:
                    auto_en.append(filename)
                else:
                    manual_en.append(filename)
            else:
                if "auto" in filename:
                    auto_other.append(filename)
                else:
                    manual_other.append(filename)
        
        # Return first match in priority order
        for file_list in [manual_target, auto_target, manual_en, auto_en, manual_other, auto_other]:
            if file_list:
                return file_list[0]
        
        # Fallback to any subtitle file
        return subtitle_files[0] if subtitle_files else None

    def _parse_subtitle_file(self, subtitle_path: str) -> str:
        """Parse subtitle file and extract text content."""
        text_lines = []
        
        try:
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Determine subtitle format
            if subtitle_path.endswith('.vtt'):
                return self._parse_vtt_content(content)
            elif subtitle_path.endswith('.srt'):
                return self._parse_srt_content(content)
            else:
                # Try VTT format as default
                return self._parse_vtt_content(content)
            
        except Exception as e:
            log.error(f"[TRANSCRIPT] Error parsing subtitle file: {e}")
            return ""

    def _parse_vtt_content(self, content: str) -> str:
        """Parse VTT subtitle content and extract text."""
        text_lines = []
        lines = content.split('\n')
        
        for line in lines:
            line = line.strip()
            # Skip empty lines, timestamps, and VTT headers
            if (not line or 
                line.startswith('WEBVTT') or 
                line.startswith('NOTE') or
                '-->' in line or
                line.isdigit() or
                re.match(r'^\d+:\d+:\d+', line)):
                continue
            
            # Remove VTT formatting tags
            line = re.sub(r'<[^>]+>', '', line)  # Remove HTML-like tags
            line = re.sub(r'\{[^}]+\}', '', line)  # Remove style tags
            
            if line:
                text_lines.append(line)
        
        return ' '.join(text_lines)

    def _parse_srt_content(self, content: str) -> str:
        """Parse SRT subtitle content and extract text."""
        text_lines = []
        lines = content.split('\n')
        
        for line in lines:
            line = line.strip()
            # Skip empty lines, sequence numbers, and timestamps
            if (not line or 
                line.isdigit() or 
                '-->' in line or
                re.match(r'^\d+:\d+:\d+', line)):
                continue
            
            # Remove SRT formatting tags
            line = re.sub(r'<[^>]+>', '', line)  # Remove HTML-like tags
            
            if line:
                text_lines.append(line)
        
        return ' '.join(text_lines)

    async def cleanup(self) -> None:
        """Clean up temporary files and directory."""
        try:
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                os.remove(file_path)
            os.rmdir(self.temp_dir)
            log.info(f"[TRANSCRIPT CLEANUP] Removed {self.temp_dir}")
        except Exception as e:
            log.warning(f"[TRANSCRIPT CLEANUP] {e}")