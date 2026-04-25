"""
List all chats/channels/groups your account can see, with their IDs.
Use these IDs in config.yaml for source_channel and destination_chats.
Run: python list_chats.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
try:
    asyncio.get_running_loop()
except RuntimeError:
    try:
        _el = asyncio.get_event_loop()
    except RuntimeError:
        _el = None
    if _el is None or _el.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.enums import ParseMode

from settings import load_settings

SESSION_DIR = Path(__file__).resolve().parent
SESSION_NAME = "autoplugtg_session"


async def main() -> None:
    cfg = load_settings()
    app = Client(
        SESSION_NAME,
        api_id=cfg["api_id"],
        api_hash=cfg["api_hash"],
        workdir=str(SESSION_DIR),
        parse_mode=ParseMode.DEFAULT,
    )
    async with app:
        print("Chats you have access to (use these IDs in config.yaml):\n")
        async for d in app.get_dialogs():
            chat = d.chat
            cid = chat.id
            title = getattr(chat, "title", None) or getattr(chat, "first_name", "") or "?"
            typ = chat.type.name if hasattr(chat.type, "name") else str(chat.type)
            print(f"  ID: {cid}  |  {typ:12}  |  {title}")


if __name__ == "__main__":
    asyncio.run(main())
