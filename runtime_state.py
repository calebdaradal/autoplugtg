"""Persistent scheduler state (next run, pause)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_PATH = Path(__file__).resolve().parent / "runtime_state.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RuntimeState:
    """next_run_at is timezone-aware UTC or None."""

    next_run_at: str | None  # ISO format Z or +00:00
    paused: bool

    @staticmethod
    def default() -> "RuntimeState":
        return RuntimeState(next_run_at=None, paused=True)

    def next_run_datetime(self) -> datetime | None:
        if not self.next_run_at:
            return None
        raw = self.next_run_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def set_next_run(self, dt: datetime | None) -> None:
        if dt is None:
            self.next_run_at = None
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            self.next_run_at = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_state() -> RuntimeState:
    if not STATE_PATH.exists():
        return RuntimeState.default()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError):
        return RuntimeState.default()
    return RuntimeState(
        next_run_at=data.get("next_run_at"),
        paused=bool(data.get("paused", True)),
    )


def save_state(state: RuntimeState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
