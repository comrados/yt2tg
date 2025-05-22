import json
from typing import Set

CONFIG_FILE: str = "config.json"

# Загружаем весь JSON
with open(CONFIG_FILE, encoding="utf-8") as f:
    config = json.load(f)

# Telegram
telegram_cfg = config.get("telegram", {})
BOT_TOKEN: str = telegram_cfg["bot_token"]
ALLOWED_USERS: Set[int] = set(telegram_cfg["allowed_users"])
TARGET_CHANNEL: int = telegram_cfg["target_channel"]

# Gemeni
gemeni_cfg = config.get("gemeni", {})
GEMINI_API_KEY: str = gemeni_cfg["gemini_api_key"]
CHOSEN_MODEL_NAME: str = gemeni_cfg["chosen_model"]
