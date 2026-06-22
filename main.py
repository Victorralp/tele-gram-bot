"""
Entry point — starts Flask dashboard + Telegram bot in one process.
Flask runs in a background thread; the bot owns the main asyncio loop.
"""
import logging
import os
import threading

from db import init_db
from bot import setup_bot
from dashboard import create_app

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def run_flask():
    port = int(os.getenv("PORT", 8080))
    flask_app = create_app()
    log.info(f"🌐 Dashboard → http://0.0.0.0:{port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    init_db()
    log.info("✅ Database initialised")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    bot_app = setup_bot()
    log.info("🤖 Bot starting…")
    bot_app.run_polling(drop_pending_updates=True)
