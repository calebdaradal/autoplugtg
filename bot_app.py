"""Telegram Bot API control plane (BotFather token). Admin-only commands."""
from __future__ import annotations

import asyncio
import io
import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Import forwarder before Pyrogram submodules — forwarder bootstraps asyncio (required on Py 3.10+).
from forwarder import _get_app
from pyrogram.errors import PeerIdInvalid
from login_bridge import LoginBridge, LoginStep
from pyrogram_telegram_io import install_ainput_patch
from runtime_state import load_state, save_state
from settings import (
    CONFIG_PATH,
    coerce_chat_arg,
    is_forwarding_setup_complete,
    load_settings,
    save_settings,
)
from timefmt import format_dt_local

log = logging.getLogger("AutoPlugTG.bot")


def _parse_admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_USER_IDS") or os.environ.get("ADMIN_USER_ID", "")
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _admin_ok(user_id: int | None, admins: set[int]) -> bool:
    if user_id is None or not admins:
        return False
    return user_id in admins


async def _ensure_client_running(application: Application) -> None:
    """Start Pyrogram client if not started (session exists or already logged in)."""
    if application.bot_data.get("client_starting"):
        return
    client = application.bot_data.get("client")
    if client is not None and client.is_connected:
        return
    application.bot_data["client_starting"] = True
    bridge: LoginBridge = application.bot_data["bridge"]
    install_ainput_patch(bridge)
    try:
        cfg = load_settings(strict=False)
        c = _get_app(cfg["api_id"], cfg["api_hash"])
        await c.start()
        application.bot_data["client"] = c
        log.info("Pyrogram user client started.")
    except Exception as e:
        log.exception("Pyrogram start failed: %s", e)
        application.bot_data["client"] = None
    finally:
        application.bot_data["client_starting"] = False


async def _start_client_with_phone(application: Application, phone: str) -> None:
    application.bot_data["client_starting"] = True
    bridge: LoginBridge = application.bot_data["bridge"]
    install_ainput_patch(bridge)
    await _stop_scheduler(application)
    old = application.bot_data.get("client")
    if old is not None and old.is_connected:
        try:
            await old.stop()
        except Exception:
            pass
    try:
        cfg = load_settings(strict=False)
        c = _get_app(cfg["api_id"], cfg["api_hash"], phone_number=phone.strip())
        await c.start()
        application.bot_data["client"] = c
        bridge.step = LoginStep.IDLE
        log.info("Pyrogram user client logged in.")
    finally:
        application.bot_data["client_starting"] = False


async def _run_phone_login_background(application: Application, phone: str, chat_id: int) -> None:
    """
    Pyrogram start() waits on asyncio queues fed by other bot handlers.
    It must not run inside a PTB handler (same task would deadlock — code/password messages never processed).
    """
    bridge: LoginBridge = application.bot_data["bridge"]
    try:
        await _start_client_with_phone(application, phone.strip())
    except Exception as e:
        log.exception("Phone login failed: %s", e)
        bridge.step = LoginStep.IDLE
        try:
            await application.bot.send_message(chat_id, f"Login failed: {e}")
        except Exception:
            pass
        return
    _ensure_scheduler(application)
    try:
        await application.bot.send_message(
            chat_id,
            "Login complete. User client is connected; scheduler is running. You can use /verify.",
        )
    except Exception:
        pass


def _schedule_phone_login(application: Application, phone: str, chat_id: int) -> bool:
    """Returns False if a phone-login background task is already running."""
    existing = application.bot_data.get("login_background_task")
    if existing is not None and not existing.done():
        return False

    async def _wrapped() -> None:
        try:
            await _run_phone_login_background(application, phone, chat_id)
        finally:
            application.bot_data["login_background_task"] = None

    application.bot_data["login_background_task"] = asyncio.create_task(
        _wrapped(),
        name="autoplugtg_phone_login",
    )
    return True


async def _stop_scheduler(application: Application) -> None:
    stop_ev: asyncio.Event = application.bot_data["scheduler_stop"]
    stop_ev.set()
    t = application.bot_data.get("scheduler_task")
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    application.bot_data["scheduler_task"] = None


def _ensure_scheduler(application: Application) -> None:
    from scheduler import scheduler_loop

    t = application.bot_data.get("scheduler_task")
    if t is not None and not t.done():
        return
    client = application.bot_data.get("client")
    if client is None or not client.is_connected:
        return
    stop_ev: asyncio.Event = application.bot_data["scheduler_stop"]
    stop_ev.clear()
    application.bot_data["scheduler_task"] = asyncio.create_task(
        scheduler_loop(client, stop_ev),
        name="autoplugtg_scheduler",
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    try:
        cfg = load_settings(strict=False)
    except Exception as e:
        await update.message.reply_text(f"Config error: {e}")
        return
    ready = is_forwarding_setup_complete(cfg)
    st = load_state()
    await update.message.reply_text(
        "AutoPlugTG control bot.\n\n"
        f"Setup complete (source + targets): {ready}\n"
        f"Paused: {st.paused}\n"
        f"Next run: {format_dt_local(st.next_run_datetime())}\n\n"
        "Commands: /status /pause /resume /show_config /verify /dialogs\n"
        "/set_source /add_target /remove_target /set_interval /set_messages\n"
        "/set_delay_dest /set_delay_msg\n"
        "/login [phone] — first-time login (optional phone as argument)\n"
        "If no session file exists, run /login then send your phone number."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    try:
        cfg = load_settings(strict=False)
    except Exception as e:
        await update.message.reply_text(f"Config error: {e}")
        return
    st = load_state()
    client = context.application.bot_data.get("client")
    conn = bool(client and client.is_connected)
    await update.message.reply_text(
        f"Paused: {st.paused}\n"
        f"Next run:\n{format_dt_local(st.next_run_datetime())}\n\n"
        f"User client connected: {conn}\n"
        f"Setup complete: {is_forwarding_setup_complete(cfg)}\n"
        f"interval_minutes: {cfg.get('interval_minutes')}\n"
        f"source_channel: {cfg.get('source_channel')!r}\n"
        f"destinations ({len(cfg.get('destination_chats') or [])}): {cfg.get('destination_chats')}\n"
        f"messages_to_forward: {cfg.get('messages_to_forward')}\n"
        f"delay_between_destinations_seconds: {cfg.get('delay_between_destinations_seconds')}\n"
        f"delay_between_messages_seconds: {cfg.get('delay_between_messages_seconds')}"
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    lock: asyncio.Lock = context.application.bot_data["state_lock"]
    async with lock:
        st = load_state()
        st.paused = True
        save_state(st)
    await update.message.reply_text(
        "Paused. Forwarding will not run until /resume.\n"
        f"Next run time unchanged:\n{format_dt_local(st.next_run_datetime())}"
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    lock: asyncio.Lock = context.application.bot_data["state_lock"]
    async with lock:
        st = load_state()
        st.paused = False
        save_state(st)
    _ensure_scheduler(context.application)
    await update.message.reply_text(
        "Resumed. The scheduler will run when due (immediately if the next run time has already passed).\n"
        f"Next run:\n{format_dt_local(load_state().next_run_datetime())}"
    )


async def cmd_show_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            txt = f.read()
    except OSError as e:
        await update.message.reply_text(f"Cannot read config: {e}")
        return
    if len(txt) > 3500:
        await update.message.reply_document(document=io.BytesIO(txt.encode("utf-8")), filename="config.yaml")
    else:
        await update.message.reply_text(f"<pre>{txt[:3500]}</pre>", parse_mode="HTML")


async def cmd_set_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_source <id_or_@username>")
        return
    raw = " ".join(context.args).strip()
    try:
        chat = coerce_chat_arg(raw)
    except Exception as e:
        await update.message.reply_text(f"Invalid: {e}")
        return
    lock: asyncio.Lock = context.application.bot_data["state_lock"]
    async with lock:
        cfg = load_settings(strict=False)
        cfg["source_channel"] = chat
        save_settings(cfg)
    await update.message.reply_text(f"source_channel set to {chat!r}")


async def cmd_add_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    if not context.args:
        await update.message.reply_text("Usage: /add_target <id_or_@username>")
        return
    raw = " ".join(context.args).strip()
    try:
        chat = coerce_chat_arg(raw)
    except Exception as e:
        await update.message.reply_text(f"Invalid: {e}")
        return
    lock: asyncio.Lock = context.application.bot_data["state_lock"]
    async with lock:
        cfg = load_settings(strict=False)
        dest: list = list(cfg.get("destination_chats") or [])
        if chat in dest:
            await update.message.reply_text("Already in list.")
            return
        dest.append(chat)
        cfg["destination_chats"] = dest
        save_settings(cfg)
    await update.message.reply_text(f"Added {chat!r}. Run /verify to confirm the user client can see it.")


async def cmd_remove_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove_target <id_or_@username>")
        return
    raw = " ".join(context.args).strip()
    try:
        chat = coerce_chat_arg(raw)
    except Exception as e:
        await update.message.reply_text(f"Invalid: {e}")
        return
    lock: asyncio.Lock = context.application.bot_data["state_lock"]
    async with lock:
        cfg = load_settings(strict=False)
        dest: list = list(cfg.get("destination_chats") or [])
        if chat not in dest:
            await update.message.reply_text("Not found in list.")
            return
        dest = [d for d in dest if d != chat]
        cfg["destination_chats"] = dest
        save_settings(cfg)
    await update.message.reply_text(f"Removed {chat!r}. Run /verify to refresh checks.")


async def _set_numeric(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    as_int: bool,
    label: str,
) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    if not context.args:
        await update.message.reply_text(f"Usage: /{label} <number>")
        return
    try:
        v = int(context.args[0]) if as_int else float(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid number.")
        return
    lock: asyncio.Lock = context.application.bot_data["state_lock"]
    async with lock:
        cfg = load_settings(strict=False)
        cfg[key] = v
        save_settings(cfg)
    await update.message.reply_text(f"{key} = {v}")


async def cmd_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_numeric(update, context, "interval_minutes", True, "set_interval")


async def cmd_set_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_numeric(update, context, "messages_to_forward", True, "set_messages")


async def cmd_set_delay_dest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_numeric(update, context, "delay_between_destinations_seconds", False, "set_delay_dest")


async def cmd_set_delay_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_numeric(update, context, "delay_between_messages_seconds", False, "set_delay_msg")


def _normalize_peer_id(peer: object) -> object:
    """YAML / env sometimes yields float (e.g. -1003... read as scientific); coerce to int for Telegram."""
    if isinstance(peer, float):
        return int(peer)
    if isinstance(peer, str) and peer.strip().lstrip("-").isdigit():
        try:
            return int(peer.strip())
        except ValueError:
            return peer
    return peer


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    client = context.application.bot_data.get("client")
    if client is None or not client.is_connected:
        await update.message.reply_text("User client not connected. Use /login or wait for startup.")
        return
    try:
        cfg = load_settings(strict=False)
    except Exception as e:
        await update.message.reply_text(f"Config error: {e}")
        return
    me = await client.get_me()
    me_line = (
        f"User client: @{me.username} (id={me.id}, {me.first_name or ''})"
        if me.username
        else f"User client: id={me.id} ({me.first_name!r}, no @username)"
    )

    dialog_ids: set[int] = set()
    async for d in client.get_dialogs():
        dialog_ids.add(d.chat.id)

    lines: list[str] = [me_line, f"Dialogs loaded: {len(dialog_ids)} chats.", ""]

    src = cfg.get("source_channel")
    to_check: list[tuple[str, object]] = []
    if src:
        to_check.append(("source", _normalize_peer_id(src)))
    for d in cfg.get("destination_chats") or []:
        to_check.append(("destination", _normalize_peer_id(d)))
    if not to_check:
        await update.message.reply_text("No source or destinations configured.")
        return

    hint_peer_invalid = (
        "PEER_ID_INVALID means this user session cannot resolve that id (no access_hash). "
        "Common causes: (1) wrong Telegram account logged in vs the one that joined those channels; "
        "(2) wrong id — use /dialogs and copy the exact ID shown for each chat; "
        "(3) channel/group never opened with this account — open it once in the official Telegram app "
        "while logged in as THIS account, or use @channelusername in config instead of a raw id."
    )

    for label, peer in to_check:
        pid = peer if isinstance(peer, int) else None
        in_dialogs = pid is not None and pid in dialog_ids
        try:
            ch = await client.get_chat(peer)
            title = getattr(ch, "title", None) or getattr(ch, "first_name", "") or "?"
            tid = ch.id
            typ = ch.type.name if hasattr(ch.type, "name") else str(ch.type)
            lines.append(f"OK [{label}] id={tid} type={typ} title={title}")
        except PeerIdInvalid:
            lines.append(
                f"FAIL [{label}] peer={peer!r} — Peer id invalid.\n"
                f"    In /dialogs list: {'yes' if in_dialogs else 'no'}\n"
                f"    {hint_peer_invalid}"
            )
        except Exception as e:
            lines.append(
                f"FAIL [{label}] peer={peer!r} — {e}\n"
                f"    In /dialogs list: {'yes' if in_dialogs else 'no'}"
            )

    text = "\n".join(lines)
    for chunk in _chunks(text, 3800):
        await update.message.reply_text(chunk)


async def cmd_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    client = context.application.bot_data.get("client")
    if client is None or not client.is_connected:
        await update.message.reply_text("User client not connected.")
        return
    lines: list[str] = []
    async for d in client.get_dialogs():
        chat = d.chat
        cid = chat.id
        title = getattr(chat, "title", None) or getattr(chat, "first_name", "") or "?"
        typ = chat.type.name if hasattr(chat.type, "name") else str(chat.type)
        lines.append(f"ID: {cid}  |  {typ:12}  |  {title}")
    body = "\n".join(lines)
    bio = io.BytesIO(body.encode("utf-8"))
    await update.message.reply_document(document=bio, filename="dialogs.txt")


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    bridge: LoginBridge = context.application.bot_data["bridge"]
    if context.args:
        phone = " ".join(context.args).strip()
        chat_id = update.effective_chat.id if update.effective_chat else 0
        if not _schedule_phone_login(context.application, phone, chat_id):
            await update.message.reply_text("Login is already in progress. Wait for it to finish or fail.")
            return
        await update.message.reply_text(
            "Login started in the background.\n\n"
            "When Telegram sends a login code, reply in this chat with the digits only. "
            "If 2FA is enabled, send your password in this chat when Pyrogram asks for it.\n\n"
            "You will get another message here when login succeeds or fails."
        )
        return
    bridge.step = LoginStep.WANT_PHONE
    await update.message.reply_text(
        "Reply to this chat with your phone number in international format (e.g. +639171234567).\n"
        "You will then enter the login code and 2FA password in this chat when asked.\n\n"
        "If you never get an OTP prompt, an old session file may still be valid — or delete "
        "`autoplugtg_session.session` (and `-journal` if present) in the app folder to force a new login."
    )


async def on_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phone reply after /login, or login code / 2FA while Pyrogram is authorizing."""
    admins: set[int] = context.application.bot_data["admin_ids"]
    if not _admin_ok(update.effective_user.id if update.effective_user else None, admins):
        return
    if not update.message or not update.message.text:
        return
    bridge: LoginBridge = context.application.bot_data["bridge"]
    text = update.message.text.strip()
    # Code / 2FA must be handled before WANT_PHONE — numeric OTP is not a phone number.
    if bridge.step == LoginStep.WANT_CODE:
        await bridge.provide_code(text)
        return
    if bridge.step == LoginStep.WANT_PASSWORD:
        await bridge.provide_password(text)
        return
    if context.application.bot_data.get("client_starting"):
        if text.startswith("+"):
            try:
                await bridge._phone_queue.put(text)
            except asyncio.QueueFull:
                pass
            return
        low = text.lower()
        if low in ("y", "n", "yes", "no"):
            try:
                await bridge._confirm_queue.put("y" if low.startswith("y") else "n")
            except asyncio.QueueFull:
                pass
            return
    if bridge.step == LoginStep.WANT_PHONE and text.startswith("+"):
        chat_id = update.effective_chat.id if update.effective_chat else 0
        if not _schedule_phone_login(context.application, text, chat_id):
            await update.message.reply_text("Login is already in progress. Wait for it to finish.")
            return
        bridge.step = LoginStep.IDLE
        await update.message.reply_text(
            "Login started in the background.\n\n"
            "When Telegram sends a login code, reply here with the digits only, then your 2FA password if asked.\n\n"
            "You will get another message when login succeeds or fails."
        )
        return


def build_application(token: str, *, tg_log_handler=None, primary_admin_id: int | None = None) -> Application:
    admins = _parse_admin_ids()
    log_admin = primary_admin_id if primary_admin_id is not None else (next(iter(admins)) if admins else None)
    if not admins:
        log.warning("No ADMIN_USER_ID(S) set — bot will ignore everyone.")

    async def post_init(application: Application) -> None:
        application.bot_data["admin_ids"] = admins
        application.bot_data.setdefault("bridge", LoginBridge())
        application.bot_data.setdefault("state_lock", asyncio.Lock())
        application.bot_data.setdefault("scheduler_stop", asyncio.Event())
        application.bot_data["scheduler_stop"].clear()
        install_ainput_patch(application.bot_data["bridge"])
        if tg_log_handler is not None and log_admin is not None:
            tg_log_handler.set_target(application.bot, log_admin)
            tg_log_handler.start_worker()

        async def delayed_bootstrap() -> None:
            await asyncio.sleep(2.0)
            try:
                await _ensure_client_running(application)
            except Exception:
                log.info("Pyrogram did not start automatically (expected if no session yet). Use /login.")
            _ensure_scheduler(application)

        asyncio.create_task(delayed_bootstrap())

    async def post_shutdown(application: Application) -> None:
        if tg_log_handler is not None:
            await tg_log_handler.stop_worker()
        await _stop_scheduler(application)
        c = application.bot_data.get("client")
        if c is not None and c.is_connected:
            try:
                await c.stop()
            except Exception:
                pass

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("show_config", cmd_show_config))
    app.add_handler(CommandHandler("set_source", cmd_set_source))
    app.add_handler(CommandHandler("add_target", cmd_add_target))
    app.add_handler(CommandHandler("remove_target", cmd_remove_target))
    app.add_handler(CommandHandler("set_interval", cmd_set_interval))
    app.add_handler(CommandHandler("set_messages", cmd_set_messages))
    app.add_handler(CommandHandler("set_delay_dest", cmd_set_delay_dest))
    app.add_handler(CommandHandler("set_delay_msg", cmd_set_delay_msg))
    app.add_handler(CommandHandler("verify", cmd_verify))
    app.add_handler(CommandHandler("dialogs", cmd_dialogs))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_text))

    return app
