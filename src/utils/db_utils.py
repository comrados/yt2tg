import os
import sqlite3
from contextlib import closing
import logging
from typing import Optional

DB_PATH: str = "data/bot.db"

def init_db(db_path: str = DB_PATH) -> None:
    """
    Initialize the SQLite database, creating necessary directories and tables.

    :param db_path: Path to the SQLite database file.
    """
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS processed_transcripts (
                chat_id    INTEGER,
                video_id   TEXT,
                message_id INTEGER,
                status     TEXT,
                transcript_lang TEXT,
                PRIMARY KEY (chat_id, video_id, transcript_lang)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id        INTEGER PRIMARY KEY,
                preferred_language TEXT
            )
        """)
        conn.commit()

def is_already_processed(chat_id: int, video_id: str, db_path: str = DB_PATH) -> bool:
    """
    Check whether a given video in a chat has already been processed successfully.

    :param chat_id: Telegram chat ID.
    :param video_id: YouTube video ID.
    :param db_path: Path to the SQLite database file.
    :return: True if status == "success", False otherwise.
    """
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT status FROM processed_videos WHERE chat_id = ? AND video_id = ?",
            (chat_id, video_id)
        )
        result = c.fetchone()
        logging.info(f"[DB] Checked if video {video_id} in chat {chat_id} is already processed: {result}")
        return bool(result and result[0] == "success")

def mark_as_processed(
    chat_id: int,
    video_id: str,
    message_id: int,
    status: str,
    db_path: str = DB_PATH
) -> None:
    """
    Insert or update the processing status of a video in the database.

    :param chat_id: Telegram chat ID.
    :param video_id: YouTube video ID.
    :param message_id: ID of the Telegram message that triggered processing.
    :param status: One of "processing", "success", "failed".
    :param db_path: Path to the SQLite database file.
    """
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO processed_videos (chat_id, video_id, message_id, status) VALUES (?, ?, ?, ?)",
            (chat_id, video_id, message_id, status)
        )
        conn.commit()
    logging.info(f"[DB] Marked video {video_id} in chat {chat_id} as '{status}'")

def set_user_language_preference(user_id: int, language: str, db_path: str = DB_PATH) -> None:
    """Set user's preferred language for transcripts."""
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO user_preferences (user_id, preferred_language) VALUES (?, ?)",
            (user_id, language)
        )
        conn.commit()
    logging.info(f"[DB] Set language preference for user {user_id}: {language}")

def get_user_language_preference(user_id: int, db_path: str = DB_PATH) -> Optional[str]:
    """Get user's preferred language, return None if not set."""
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT preferred_language FROM user_preferences WHERE user_id = ?",
            (user_id,)
        )
        result = c.fetchone()
        return result[0] if result else None

def mark_transcript_processed(
    chat_id: int,
    video_id: str,
    message_id: int,
    status: str,
    transcript_lang: str = "en",
    db_path: str = DB_PATH
) -> None:
    """Insert or update the processing status of a transcript in the database."""
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO processed_transcripts (chat_id, video_id, message_id, status, transcript_lang) VALUES (?, ?, ?, ?, ?)",
            (chat_id, video_id, message_id, status, transcript_lang)
        )
        conn.commit()
    logging.info(f"[DB] Marked transcript {video_id} in chat {chat_id} as '{status}' (lang: {transcript_lang})")

def is_transcript_processed(chat_id: int, video_id: str, transcript_lang: str = "en", db_path: str = DB_PATH) -> bool:
    """Check whether a transcript has been processed for specific language."""
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT status FROM processed_transcripts WHERE chat_id = ? AND video_id = ? AND transcript_lang = ?",
            (chat_id, video_id, transcript_lang)
        )
        result = c.fetchone()
        logging.info(f"[DB] Checked if transcript {video_id} in chat {chat_id} (lang: {transcript_lang}) is processed: {result}")
        return bool(result and result[0] == "success")