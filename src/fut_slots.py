"""15-minute Nifty futures slots. Separate from the option-scan clock."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

from src.scan_slots import is_trading_day, now_ist

DEFAULT_MARKER = Path("data/nifty_fut_flow_slot.txt")
FIRST_SLOT = time(9, 30)
LAST_SLOT = time(15, 30)


def iter_fut_slots(day: datetime) -> list[datetime]:
    """Every 15 minutes from 09:30 through 15:30 IST, on every weekday."""
    day = now_ist(day)
    slots: list[datetime] = []
    cursor = day.replace(hour=9, minute=30, second=0, microsecond=0)
    last = day.replace(hour=15, minute=30, second=0, microsecond=0)
    while cursor <= last:
        slots.append(cursor)
        cursor += timedelta(minutes=15)
    return slots


def active_fut_slot(now: datetime | None = None) -> datetime | None:
    current = now_ist(now)
    if not is_trading_day(current.date()):
        return None
    t = current.time()
    if t < FIRST_SLOT or t > time(15, 40):
        return None
    started = [slot for slot in iter_fut_slots(current) if slot <= current]
    return started[-1] if started else None


def read_last_fut_slot(path: Path = DEFAULT_MARKER) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_last_fut_slot(slot: datetime, path: Path = DEFAULT_MARKER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(slot.isoformat(), encoding="utf-8")


def should_run_fut_slot(
    *,
    force: bool = False,
    now: datetime | None = None,
    path: Path = DEFAULT_MARKER,
) -> tuple[bool, str, datetime | None]:
    if force:
        return True, "forced", active_fut_slot(now)
    slot = active_fut_slot(now)
    if slot is None:
        return False, "outside 09:30–15:30 IST futures slots", None
    if read_last_fut_slot(path) == slot.isoformat():
        return False, f"futures slot {slot:%H:%M} IST already completed", slot
    return True, f"due for futures slot {slot:%H:%M} IST", slot
