"""Mirror logging output to a Telegram admin chat via the bot API."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Bot

TG_MSG_LIMIT = 4000
QUEUE_MAX = 500


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _telegram_safe_text(text: str) -> str:
    """Telegram rejects empty messages and NUL bytes."""
    s = text.replace("\x00", "").strip()
    return s


class _TelegramMirrorFilter(logging.Filter):
    """Only high-signal lines to Telegram: forward cycles + scheduler + errors — no HTTP client noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name or ""
        if name.startswith(("httpx", "httpcore")):
            return False
        try:
            msg = record.getMessage()
        except Exception:
            msg = ""
        if "api.telegram.org" in msg:
            return False
        # Pyrogram INFO is very chatty (sessions, DC); still surface problems.
        if name.startswith("pyrogram"):
            return record.levelno >= logging.WARNING
        # Our forwarder + scheduler + bot control plane
        if name == "AutoPlugTG" or name.startswith("AutoPlugTG."):
            # During login, mirror only lines that tell the admin what to type (not every Pyrogram line).
            if name == "AutoPlugTG.login" and record.levelno < logging.WARNING:
                ml = msg.lower()
                if not any(
                    k in ml
                    for k in (
                        "confirmation code",
                        "phone number",
                        "bot token",
                        "correct?",
                        "password",
                        "recovery code",
                        "[pyrogram]",
                    )
                ):
                    return False
            return True
        return False


def _get_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            return None


class TelegramLogHandler(logging.Handler):
    """Non-blocking: records are queued; worker task sends via bot."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.addFilter(_TelegramMirrorFilter())
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        self._queue: asyncio.Queue[str] | None = None
        self._bot: Bot | None = None
        self._chat_id: int | None = None
        self._worker: asyncio.Task | None = None
        self._dropped = 0

    def _ensure_queue(self) -> asyncio.Queue[str]:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=QUEUE_MAX)
        return self._queue

    def set_target(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    def start_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def stop_worker(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return
        msg = _telegram_safe_text(msg)
        if not msg:
            return
        loop = _get_loop()
        if loop is None or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(self._enqueue_safe, msg)
        except RuntimeError:
            pass

    def _enqueue_safe(self, msg: str) -> None:
        q = self._ensure_queue()
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            self._dropped += 1

    async def _run_worker(self) -> None:
        q = self._ensure_queue()
        buf: list[str] = []
        while True:
            try:
                timeout = 0.5 if not buf else 0.15
                line = await asyncio.wait_for(q.get(), timeout=timeout)
                buf.append(line)
                while len(buf) < 20:
                    try:
                        buf.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                chunk = "\n".join(buf)
                buf.clear()
                if self._dropped:
                    chunk = f"[log queue dropped {self._dropped} older line(s)]\n" + chunk
                    self._dropped = 0
                chunk = _telegram_safe_text(chunk)
                if self._bot is None or self._chat_id is None:
                    continue
                if not chunk:
                    continue
                for part in _chunks(chunk, TG_MSG_LIMIT):
                    part = _telegram_safe_text(part)
                    if not part:
                        continue
                    try:
                        await self._bot.send_message(chat_id=self._chat_id, text=part)
                    except Exception:
                        pass
                    await asyncio.sleep(0.35)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)
