"""Replace Pyrogram's stdin ainput with Telegram-driven queues during user login."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from login_bridge import LoginBridge

from login_bridge import LoginStep

log = logging.getLogger("AutoPlugTG.login")
_original_ainput: Callable[..., Any] | None = None


def install_ainput_patch(bridge: "LoginBridge") -> None:
    import pyrogram.utils as pu

    global _original_ainput
    if _original_ainput is None:
        _original_ainput = pu.ainput

    async def telegram_ainput(prompt: str = "", *, hide: bool = False) -> str:
        p = prompt.lower()
        line = " ".join(prompt.split())
        log.info("[Pyrogram] %s", line[:500])

        if "confirmation code" in p:
            bridge.step = LoginStep.WANT_CODE
            return await bridge._code_queue.get()
        if "recovery code" in p:
            bridge.step = LoginStep.WANT_CODE
            return await bridge._code_queue.get()
        if "enter password" in p or (("password" in p and "recovery" not in p and "hint" not in p and "confirm" not in p)) and "two" not in p:
            bridge.step = LoginStep.WANT_PASSWORD
            return await bridge._password_queue.get()
        if "confirm password recovery" in p:
            return "n"
        if "first name" in p:
            return os.environ.get("TG_SIGNUP_FIRST_NAME", "AutoPlugTG").strip() or "AutoPlugTG"
        if "last name" in p:
            return os.environ.get("TG_SIGNUP_LAST_NAME", "").strip()
        if "enter phone number" in p or ("bot token" in p and "enter" in p):
            return await bridge._phone_queue.get()
        if "correct?" in p and "y" in p:
            return await bridge._confirm_queue.get()

        assert _original_ainput is not None
        return await _original_ainput(prompt, hide=hide)

    pu.ainput = telegram_ainput  # type: ignore[assignment]


def uninstall_ainput_patch() -> None:
    import pyrogram.utils as pu

    global _original_ainput
    if _original_ainput is not None:
        pu.ainput = _original_ainput  # type: ignore[assignment]
