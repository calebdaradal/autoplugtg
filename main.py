"""
AutoPlugTG with Telegram control bot: run this on Waifly for admin commands, logs, and scheduling.

Requires BOT_TOKEN and ADMIN_USER_ID (or ADMIN_USER_IDS) in the environment.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import warnings


def _ensure_event_loop_for_imports() -> None:
    """Pyrogram imports asyncio.get_event_loop(); ensure a loop exists before loading bot_app."""
    if sys.platform == "win32":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            el = asyncio.get_event_loop()
        except RuntimeError:
            el = None
        if el is None or el.is_closed():
            asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_event_loop_for_imports()

from bot_app import build_application
from telegram_log_handler import TelegramLogHandler


def main() -> None:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        print("Set BOT_TOKEN (BotFather) and ADMIN_USER_ID in .env", file=sys.stderr)
        sys.exit(1)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(sh)

    tg_handler = TelegramLogHandler()
    tg_handler.setFormatter(fmt)
    root.addHandler(tg_handler)

    for name in ("pyrogram", "pyrogram.session", "pyrogram.connection", "telegram", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.INFO)
    # Every sendMessage/info request at DEBUG/INFO would echo into TelegramLogHandler → feedback spam.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    application = build_application(token, tg_log_handler=tg_handler)

    log = logging.getLogger("AutoPlugTG")
    log.info("Starting control bot (python-telegram-bot)…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
