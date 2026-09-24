"""Session clock for 15-minute Telegram scans (Asia/Kolkata)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.calendar import is_nse_trading_day

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def is_market_session(start: str, end: str, at: datetime | None = None) -> bool:
    at = at or now_ist()
    if not is_nse_trading_day(at.date()):
        return False
    current = at.time()
    return parse_hhmm(start) <= current <= parse_hhmm(end)


def seconds_until_next_slot(interval_minutes: int, at: datetime | None = None) -> float:
    at = at or now_ist()
    slot = timedelta(minutes=interval_minutes)
    elapsed = timedelta(
        hours=at.hour,
        minutes=at.minute,
        seconds=at.second,
        microseconds=at.microsecond,
    )
    remainder = elapsed % slot
    if remainder == timedelta(0) and at.microsecond == 0:
        return 0.0
    wait = slot - remainder
    return wait.total_seconds()
