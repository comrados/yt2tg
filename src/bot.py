import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, CallbackQueryHandler

from .utils.logging_utils import log
from .utils.config_utils import BOT_TOKEN
from .handlers import (
    error_handler, id_command, download_command, logs_command,
    tasks_command, check_cookies_command, message_logger,
    button_handler, start_worker
)

# --- Launch ---
if __name__ == "__main__":
    log.info("✅ Logger initialized.")
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(start_worker)
        .connect_timeout(10)  # seconds
        .read_timeout(300)
        .write_timeout(300)
        .build()
    )

    # Register handlers
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("download", download_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("checkcookies", check_cookies_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_logger))

    log.warning("✅ Bot started.")
    app.run_polling()
