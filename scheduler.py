"""Forward scheduler: absolute next_run_at, pause/resume, interval locked at cycle end."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from forwarder import run_forward_cycle
from runtime_state import RuntimeState, load_state, save_state
from settings import is_forwarding_setup_complete, load_settings
from timefmt import format_dt_local

logger = logging.getLogger("AutoPlugTG.scheduler")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _sleep_while_respecting_pause(next_at: datetime) -> None:
    """Sleep until next_at, polling pause; do not move next_at."""
    while True:
        state = load_state()
        if state.paused:
            while load_state().paused:
                await asyncio.sleep(0.4)
        now = _utcnow()
        if now >= next_at:
            return
        remaining = (next_at - now).total_seconds()
        await asyncio.sleep(min(1.0, max(0.05, remaining)))


async def scheduler_loop(user_client, stop_event: asyncio.Event) -> None:
    """
    user_client: started Pyrogram Client.
    stop_event: when set, loop exits after current wait.
    """
    from pyrogram import Client

    if not isinstance(user_client, Client):
        raise TypeError("user_client must be pyrogram.Client")

    while not stop_event.is_set():
        try:
            cfg = load_settings(strict=False)
        except Exception as e:
            logger.error("Scheduler config load failed: %s", e)
            await asyncio.sleep(5)
            continue

        if not is_forwarding_setup_complete(cfg):
            await asyncio.sleep(5)
            continue

        state = load_state()
        if state.paused:
            while load_state().paused and not stop_event.is_set():
                await asyncio.sleep(0.4)
            if stop_event.is_set():
                break

        state = load_state()
        target = state.next_run_datetime()

        if target is not None:
            await _sleep_while_respecting_pause(target)
        if stop_event.is_set():
            break

        # Re-check pause after wait (user may have paused during sleep)
        state = load_state()
        if state.paused:
            continue

        try:
            logger.info("Starting scheduled forward cycle.")
            await run_forward_cycle(user_client)
        except Exception as e:
            logger.exception("Scheduled forward cycle failed: %s", e)
        finally:
            try:
                cfg = load_settings(strict=False)
                interval_min = max(1, int(cfg.get("interval_minutes", 60)))
            except Exception:
                interval_min = 60
            now = _utcnow()
            nxt = now + timedelta(minutes=interval_min)
            st = load_state()
            st.set_next_run(nxt)
            save_state(st)
            logger.info("Next run scheduled at:\n%s", format_dt_local(nxt))

        await asyncio.sleep(0.05)
