"""
Telegram forwarder: fetches latest N messages from source channel and forwards to all destinations.
Uses Pyrogram user client (your account) so forwarding works with your admin rights.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Pyrogram imports asyncio.get_event_loop() at import time; ensure a loop exists.
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
from pyrogram.types import Message
from pyrogram.errors import ChatRestricted, ChannelPrivate, RPCError

from settings import load_settings

logger = logging.getLogger("AutoPlugTG")

# Session stored next to this file so we don't re-login every run
SESSION_DIR = Path(__file__).resolve().parent
SESSION_NAME = "autoplugtg_session"


def _get_app(api_id: int, api_hash: str, **kwargs) -> Client:
    return Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
        parse_mode=ParseMode.DEFAULT,
        **kwargs,
    )


async def run_forward_cycle(client: Client) -> None:
    """Load config, get latest messages from source, forward to each destination. Client must be started."""
    try:
        cfg = load_settings(strict=True)
    except ValueError as e:
        logger.warning("Skipping forward cycle: %s", e)
        return

    source = cfg["source_channel"]
    limit = cfg["messages_to_forward"]
    destinations = cfg["destination_chats"]
    delay_dest = cfg["delay_between_destinations_seconds"]
    delay_msg = cfg["delay_between_messages_seconds"]

    if not destinations:
        logger.warning("No destination_chats configured; skipping forward cycle.")
        return

    # Collect last N "units" (newest first): each unit = one message or one media group (album).
    units: list[list[int]] = []
    current_group: tuple[str, list[int]] | None = None
    async for msg in client.get_chat_history(source, limit=limit * 5):
        if not isinstance(msg, Message) or not msg.id:
            continue
        if getattr(msg, "service", False) or getattr(msg, "empty", False):
            continue
        if not (msg.text or msg.caption or msg.photo or msg.video or msg.document or msg.audio or msg.voice or msg.sticker or msg.animation or msg.video_note):
            continue
        mgid = getattr(msg, "media_group_id", None)
        if mgid:
            if current_group and current_group[0] == mgid:
                current_group[1].append(msg.id)
            else:
                if current_group:
                    current_group[1].sort()
                    units.append(current_group[1])
                    if len(units) >= limit:
                        current_group = None
                        break
                current_group = (mgid, [msg.id])
        else:
            if current_group:
                current_group[1].sort()
                units.append(current_group[1])
                current_group = None
                if len(units) >= limit:
                    break
            units.append([msg.id])
            if len(units) >= limit:
                break
    if current_group:
        current_group[1].sort()
        units.append(current_group[1])
    units = units[:limit]
    units.reverse()
    if not units:
        logger.info("No messages found in source channel; nothing to forward.")
        return

    logger.info(
        "Forwarding %s unit(s) (ids=%s) from %s to %s destination(s).",
        len(units), [u[0] if len(u) == 1 else u for u in units], source, len(destinations),
    )

    for dest in destinations:
        try:
            for ids in units:
                await client.forward_messages(dest, source, ids)
                if delay_msg > 0:
                    await asyncio.sleep(delay_msg)
            logger.info("Forwarded to %s ok.", dest)
        except ChatRestricted:
            logger.error("Forwarded to %s Chat restricted (cannot be used).", dest)
        except ChannelPrivate:
            logger.error("Forwarded to %s No permission (channel/group is private).", dest)
        except RPCError as e:
            logger.error("Forwarded to %s Failed (%s).", dest, e)
        except Exception as e:
            logger.exception("Failed to forward to %s: %s", dest, e)
        if delay_dest > 0:
            await asyncio.sleep(delay_dest)


async def main_loop(*, once: bool = False) -> None:
    """Run forward cycle every interval_minutes. Reloads config each cycle. If once=True, run one cycle and exit."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    while True:
        try:
            cfg = load_settings(strict=True)
            interval = cfg["interval_minutes"]
            logger.info("Starting forward cycle (interval=%s min).", interval)
            app = _get_app(cfg["api_id"], cfg["api_hash"])
            async with app:
                await run_forward_cycle(app)
        except FileNotFoundError as e:
            logger.error("%s", e)
            sys.exit(1)
        except ValueError as e:
            logger.error("Config error: %s", e)
            sys.exit(1)
        except Exception as e:
            logger.exception("Forward cycle failed: %s", e)

        if once:
            logger.info("Single run (--once) completed.")
            return

        cfg = load_settings(strict=True)
        interval_sec = max(1, cfg["interval_minutes"] * 60)
        logger.info("Next run in %s minutes.", interval_sec // 60)
        await asyncio.sleep(interval_sec)


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.set_event_loop(asyncio.new_event_loop())

    p = argparse.ArgumentParser(description="AutoPlugTG: forward latest messages from a channel to multiple channels/groups.")
    p.add_argument(
        "--once",
        action="store_true",
        help="Run one forward cycle and exit (useful for testing).",
    )
    args = p.parse_args()
    asyncio.run(main_loop(once=args.once))


if __name__ == "__main__":
    main()
