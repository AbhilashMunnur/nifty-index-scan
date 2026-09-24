"""IST scan slots for GitHub Actions (stdlib only — slot guard runs before pip).

Tuesday and Thursday: every 15 minutes, including 15:10 and 15:15.
Monday, Wednesday, and Friday: every 30 minutes. No 15:10 or 15:15.
Every weekday ends with the 15:40 close scan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
FIRST_SLOT = time(9, 30)
DEFAULT_MARKER = Path("data/last_scan_slot.txt")
# Jobs are kicked at the slot, not 10 minutes early (early kicks billed extra minutes).
WARMUP_SECONDS = 0

# Keep in sync with src/calendar.py. Duplicated so the slot guard needs no numpy.
NSE_HOLIDAYS = {
    "2026-01-15",
    "2026-01-26",
    "2026-03-03",
    "2026-03-26",
    "2026-03-31",
    "2026-04-03",
    "2026-04-14",
    "2026-05-01",
    "2026-05-28",
    "2026-06-26",
    "2026-09-14",
    "2026-10-02",
    "2026-10-20",
    "2026-11-10",
    "2026-11-24",
    "2026-12-25",
}


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def is_trading_day(as_of: date) -> bool:
    if as_of.weekday() >= 5:
        return False
    return as_of.isoformat() not in NSE_HOLIDAYS


def is_quarter_hour_day(as_of: date) -> bool:
    """Tuesday and Thursday keep the 15-minute grid, including 15:10 and 15:15."""
    return as_of.weekday() in (1, 3)


def iter_slots_for_day(day: datetime) -> list[datetime]:
    """Weekday scan times. Tue/Thu every 15 min; Mon/Wed/Fri every 30 min; 15:40 close."""
    day = now_ist(day)
    step = 15 if is_quarter_hour_day(day.date()) else 30
    slots: list[datetime] = []
    cursor = day.replace(hour=9, minute=30, second=0, microsecond=0)
    last_regular = day.replace(hour=15, minute=30, second=0, microsecond=0)
    while cursor <= last_regular:
        slots.append(cursor)
        if step == 15 and cursor.time() == time(15, 0):
            slots.append(day.replace(hour=15, minute=10, second=0, microsecond=0))
        cursor += timedelta(minutes=step)
    slots.append(day.replace(hour=15, minute=40, second=0, microsecond=0))
    return slots


def active_slot(now: datetime | None = None) -> datetime | None:
    """Latest scheduled slot that has already started, or None outside 09:30–15:50 IST."""
    current = now_ist(now)
    if not is_trading_day(current.date()):
        return None

    t = current.time()
    if t < FIRST_SLOT or t > time(15, 50):
        return None
    started = [slot for slot in iter_slots_for_day(current) if slot <= current]
    return started[-1] if started else None


def read_last_slot(path: Path = DEFAULT_MARKER) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_last_slot(slot: datetime, path: Path = DEFAULT_MARKER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(slot.isoformat(), encoding="utf-8")


def target_scan_slot(
    *,
    now: datetime | None = None,
    path: Path = DEFAULT_MARKER,
    warmup_seconds: int = WARMUP_SECONDS,
) -> datetime | None:
    """Unpaid slot this run should serve, including the next one during warmup."""
    current = now_ist(now)
    due = active_slot(current)
    if due is not None and read_last_slot(path) != due.isoformat():
        return due

    if not is_trading_day(current.date()):
        return None

    for slot in iter_slots_for_day(current):
        if slot <= current:
            continue
        if (slot - current).total_seconds() <= warmup_seconds:
            if read_last_slot(path) != slot.isoformat():
                return slot
        break
    return None


def should_run_slot(
    *,
    force: bool = False,
    now: datetime | None = None,
    path: Path = DEFAULT_MARKER,
) -> tuple[bool, str, datetime | None]:
    """Return (run, reason, slot)."""
    if force:
        return True, "forced", active_slot(now)

    slot = target_scan_slot(now=now, path=path)
    if slot is None:
        current = active_slot(now)
        if current is not None and read_last_slot(path) == current.isoformat():
            return False, f"slot {current:%H:%M} IST already completed", current
        return False, "outside 09:30–15:40 IST scan slots", None

    current = now_ist(now)
    if slot > current:
        return True, f"warmup for slot {slot:%H:%M} IST", slot
    return True, f"due for slot {slot:%H:%M} IST", slot


def seconds_until_next_slot(now: datetime | None = None) -> int | None:
    current = now_ist(now)
    if not is_trading_day(current.date()):
        return None
    for slot in iter_slots_for_day(current):
        if slot > current:
            return max(1, int((slot - current).total_seconds()))
    return None


def seconds_until_warmup_dispatch(
    now: datetime | None = None,
    warmup_seconds: int = WARMUP_SECONDS,
) -> int | None:
    wait = seconds_until_next_slot(now)
    if wait is None:
        return None
    return max(1, wait - warmup_seconds)
