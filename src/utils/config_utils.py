import json
from typing import Set

CONFIG_FILE: str = "config.json"

# Load configuration from JSON
with open(CONFIG_FILE) as f:
    config = json.load(f)

BOT_TOKEN: str = config["bot_token"]
ALLOWED_USERS: Set[int] = set(config["allowed_users"])
TARGET_CHANNEL: str = config["target_channel"]
