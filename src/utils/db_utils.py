import os
import sqlite3
from contextlib import closing
import logging

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
