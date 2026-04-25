"""
Load and validate AutoPlugTG settings from config file and environment.
Config is reloaded each run so you can edit config.yaml without restarting (when running 24/7).
"""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

try:
    from ruamel.yaml import YAML
except ImportError:
    YAML = None  # type: ignore[misc, assignment]

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
ENV_API_ID = "TELEGRAM_API_ID"
ENV_API_HASH = "TELEGRAM_API_HASH"
ENV_SOURCE_CHANNEL = "SOURCE_CHANNEL"
ENV_DESTINATION_CHATS = "DESTINATION_CHATS"
ENV_INTERVAL_MINUTES = "INTERVAL_MINUTES"
ENV_MESSAGES_TO_FORWARD = "MESSAGES_TO_FORWARD"
ENV_DELAY_DEST = "DELAY_BETWEEN_DESTINATIONS_SECONDS"
ENV_DELAY_MSG = "DELAY_BETWEEN_MESSAGES_SECONDS"


def coerce_chat_arg(text: str) -> int | str:
    """Parse a single chat id or @username from user input."""
    out = _coerce_chat_id(text.strip())
    if out is None:
        raise ValueError("Empty chat id")
    return out


def _coerce_chat_id(value: Any) -> int | str | None:
    """Keep int or string (@username); convert numeric string to int for consistency."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.startswith("@"):
            return s
        try:
            return int(s)
        except ValueError:
            return s
    raise TypeError(f"Invalid chat id: {value}")


def load_raw_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(*, strict: bool = True) -> dict[str, Any]:
    """Load config from env vars (primary) with config.yaml as fallback.
    config.yaml is optional — all values can be supplied via environment variables.
    If strict=False, allow empty source/destinations for setup UI."""
    raw = load_raw_config()

    # API credentials: env overrides config
    api_id = os.environ.get(ENV_API_ID) or raw.get("api_id")
    api_hash = os.environ.get(ENV_API_HASH) or raw.get("api_hash")
    if not api_id or not api_hash:
        raise ValueError(
            "Missing Telegram API credentials. Set TELEGRAM_API_ID and TELEGRAM_API_HASH "
            "in environment variables or in config.yaml. Get them from https://my.telegram.org"
        )
    try:
        api_id = int(api_id)
    except (TypeError, ValueError):
        raise ValueError("api_id must be an integer")

    interval_env = os.environ.get(ENV_INTERVAL_MINUTES)
    interval = interval_env if interval_env is not None else raw.get("interval_minutes", 60)
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        interval = 60
    if interval < 1:
        interval = 1

    source_env = os.environ.get(ENV_SOURCE_CHANNEL)
    source_raw = source_env if source_env is not None else raw.get("source_channel")
    source = _coerce_chat_id(source_raw) if source_raw is not None else None
    if strict and source is None:
        raise ValueError(
            "source_channel is not set. Use SOURCE_CHANNEL env var or set it in config.yaml."
        )

    n_msg_env = os.environ.get(ENV_MESSAGES_TO_FORWARD)
    n_msg = n_msg_env if n_msg_env is not None else raw.get("messages_to_forward", 1)
    try:
        n_msg = int(n_msg)
    except (TypeError, ValueError):
        n_msg = 1
    if n_msg < 1:
        n_msg = 1

    dest_env = os.environ.get(ENV_DESTINATION_CHATS)
    if dest_env is not None:
        dest_raw = [d.strip() for d in dest_env.split(",") if d.strip()]
    else:
        dest_raw = raw.get("destination_chats") or []
    if not isinstance(dest_raw, list):
        dest_raw = [dest_raw]
    destination_chats = [_coerce_chat_id(d) for d in dest_raw if d is not None and str(d).strip() != ""]
    destination_chats = [d for d in destination_chats if d is not None]

    delay_dest_env = os.environ.get(ENV_DELAY_DEST)
    delay_dest = delay_dest_env if delay_dest_env is not None else raw.get("delay_between_destinations_seconds", 2)
    try:
        delay_dest = max(0, float(delay_dest))
    except (TypeError, ValueError):
        delay_dest = 2

    delay_msg_env = os.environ.get(ENV_DELAY_MSG)
    delay_msg = delay_msg_env if delay_msg_env is not None else raw.get("delay_between_messages_seconds", 1)
    try:
        delay_msg = max(0, float(delay_msg))
    except (TypeError, ValueError):
        delay_msg = 1

    return {
        "api_id": api_id,
        "api_hash": str(api_hash).strip(),
        "interval_minutes": interval,
        "source_channel": source,
        "messages_to_forward": n_msg,
        "destination_chats": destination_chats,
        "delay_between_destinations_seconds": delay_dest,
        "delay_between_messages_seconds": delay_msg,
    }


def is_forwarding_setup_complete(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("source_channel")) and len(cfg.get("destination_chats") or []) > 0


def _build_yaml_structure_from_settings(cfg: dict[str, Any], preserve: dict[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(preserve) if preserve else {}
    base["interval_minutes"] = cfg["interval_minutes"]
    base["source_channel"] = cfg["source_channel"]
    base["messages_to_forward"] = cfg["messages_to_forward"]
    base["destination_chats"] = list(cfg["destination_chats"])
    base["delay_between_destinations_seconds"] = cfg["delay_between_destinations_seconds"]
    base["delay_between_messages_seconds"] = cfg["delay_between_messages_seconds"]
    if cfg.get("api_id") is not None:
        base["api_id"] = cfg["api_id"]
    if cfg.get("api_hash"):
        base["api_hash"] = cfg["api_hash"]
    return base


def save_settings(cfg: dict[str, Any]) -> None:
    """Write config.yaml. Uses ruamel when available to preserve comments/order where possible."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        preserve = load_raw_config()
    except FileNotFoundError:
        preserve = {}
    merged = _build_yaml_structure_from_settings(cfg, preserve)
    if os.environ.get(ENV_API_ID):
        merged.pop("api_id", None)
        merged.pop("api_hash", None)

    if YAML is not None and CONFIG_PATH.exists():
        y = YAML()
        y.indent(mapping=2, sequence=4, offset=2)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = y.load(f) or {}
        for k, v in merged.items():
            data[k] = v
        if os.environ.get(ENV_API_ID):
            data.pop("api_id", None)
            data.pop("api_hash", None)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            y.dump(data, f)
        return

    if YAML is not None and not CONFIG_PATH.exists():
        y = YAML()
        y.indent(mapping=2, sequence=4, offset=2)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            y.dump(merged, f)
        return

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            merged,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
