import os
import logging

LOG_FILE: str = "logs/bot.log"

def init_logging(log_path: str = LOG_FILE, overwrite: bool = True) -> logging.Logger:
    """
    Initialize the root logger to log both to a file and to the console.

    :param log_path: Path to the log file.
    :param overwrite: If True, overwrite existing log file; otherwise append.
    :return: Configured root logger.
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode='w' if overwrite else 'a'),
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
