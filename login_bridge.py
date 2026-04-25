"""Feed Pyrogram login prompts from Telegram admin messages."""
from __future__ import annotations

import asyncio
import logging
from enum import Enum, auto

logger = logging.getLogger("AutoPlugTG.login")


class LoginStep(Enum):
    IDLE = auto()
    WANT_PHONE = auto()
    WANT_CODE = auto()
    WANT_PASSWORD = auto()


class LoginBridge:
    """Queues for phone / code / 2FA password during Pyrogram first login."""

    def __init__(self) -> None:
        self.step = LoginStep.IDLE
        self._phone_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
        self._confirm_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
        self._code_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
        self._password_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
        self._phone_value: str | None = None

    def reset(self) -> None:
        self.step = LoginStep.IDLE
        self._phone_value = None
        for q in (self._phone_queue, self._confirm_queue, self._code_queue, self._password_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

    async def provide_phone(self, phone: str) -> None:
        self._phone_value = phone.strip()
        await self._phone_queue.put(self._phone_value)

    async def provide_confirm(self, answer: str) -> None:
        await self._confirm_queue.put(answer.strip())

    async def provide_code(self, code: str) -> None:
        await self._code_queue.put(code.strip())

    async def provide_password(self, pwd: str) -> None:
        await self._password_queue.put(pwd.strip())

    def phone_number(self) -> str:
        if not self._phone_value:
            raise RuntimeError("phone not set")
        return self._phone_value

    async def phone_code(self) -> str:
        self.step = LoginStep.WANT_CODE
        logger.info("Telegram login: waiting for SMS/app code from admin chat.")
        return await self._code_queue.get()

    async def password(self) -> str:
        self.step = LoginStep.WANT_PASSWORD
        logger.info("Telegram login: waiting for 2FA password from admin chat.")
        return await self._password_queue.get()
