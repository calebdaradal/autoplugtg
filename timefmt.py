"""Format datetimes for admin-facing messages (default Asia/Manila)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]

DEFAULT_TZ = "Asia/Manila"


def display_tz_name() -> str:
    return os.environ.get("DISPLAY_TIMEZONE", DEFAULT_TZ).strip() or DEFAULT_TZ


def display_zone():
    name = display_tz_name()
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def format_dt_local(dt_utc: datetime | None) -> str:
    """UTC naive or aware -> local AM/PM string + ISO UTC."""
    if dt_utc is None:
        return "not scheduled"
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    utc_iso = dt_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    local = dt_utc.astimezone(display_zone())
    local_str = local.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    return f"{local_str}\n({utc_iso})"
