import re
from typing import Optional
from datetime import datetime
from urllib.parse import urlparse, parse_qs

def get_video_id(url: str) -> Optional[str]:
    """
    Extract a YouTube video ID from a full URL.

    :param url: The YouTube URL (youtu.be or youtube.com).
    :return: 11-character video ID if valid; otherwise None.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {'youtube.com', 'www.youtube.com', 'youtu.be'}:
            return None

        if 'youtu.be' in host:
            vid = parsed.path.strip('/')
        elif '/shorts/' in parsed.path:
            vid = parsed.path.split('/shorts/')[-1].split('/')[0]
        else:
            query = parse_qs(parsed.query)
            vid = query.get('v', [None])[0]

        return vid if vid and re.match(r'^[\w-]{11}$', vid) else None
    except Exception:
        return None

def clean_youtube_url(url: str) -> Optional[str]:
    """
    Normalize a YouTube URL to the youtu.be short form.

    :param url: Any valid YouTube URL.
    :return: Cleaned 'https://youtu.be/{video_id}' or None.
    """
    video_id = get_video_id(url)
    return f"https://youtu.be/{video_id}" if video_id else None

def parse_log_time(line: str) -> datetime:
    """
    Parse the timestamp from a log line starting with 'YYYY-MM-DD HH:MM:SS,mmm'.

    :param line: Single log file line.
    :return: datetime parsed from the line, or datetime.min on failure.
    """
    try:
        ts = " ".join(line.split()[:2])
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S,%f")
    except Exception:
        return datetime.min
