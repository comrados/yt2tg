import os
import logging
import yt_dlp
from yt_dlp.utils import DownloadError

COOKIES_FILE: str = "cookies.txt"

def init_cookies(cookies_path: str = COOKIES_FILE) -> bool:
    """
    Validate presence and sufficiency of YouTube cookies for age-restricted content.

    :param cookies_path: Path to the cookies.txt file.
    :return: True if cookies are found and valid; False otherwise.
    """
    if not os.path.exists(cookies_path):
        logging.warning("[COOKIES] cookies.txt not found.")
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

        logging.info("[COOKIES] Cookies are valid ✅")
        return True
    except DownloadError as e:
        logging.warning(f"[COOKIES] Invalid or insufficient cookies: {e}")
    except Exception as e:
        logging.error(f"[COOKIES] Failed to validate cookies: {e}", exc_info=True)

    return False

# Validate cookies at import time
cookies_available: bool = init_cookies()
