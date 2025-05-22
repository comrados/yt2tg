import os
import logging

LOG_FILE: str = "logs/bot.log"

def init_logging(log_path: str = LOG_FILE, overwrite: bool = True) -> logging.Logger:
    """
    Initialize the root logger to log both to a file (UTF-8) and to the console.
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            # ensure Unicode titles (e.g. non-Latin) can be written
            logging.FileHandler(log_path, mode='w' if overwrite else 'a', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

    log = logging.getLogger()
    # Suppress overly verbose logs from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    return log

# Initialize logger on import
log: logging.Logger = init_logging()
