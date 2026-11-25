"""Pin a paper fill to the 14:05 entry or the 15:00 exit.

The desk is awake before the print. BankNifty, Sensex, and Nifty are scanned
after 14:00, and the qualifying script is booked at 14:05:00. A job that
misses that second still uses the 14:05 open. It does not use a later price.
"""

from __future__ import annotations

import time
from datetime import datetime, time as clock, timedelta


def pick_bar_open(bars: list[dict], stamp: datetime) -> float | None:
    """Open of the bar whose clock matches `stamp`. Later bars are ignored."""
    want = stamp.replace(second=0, microsecond=0)
    for bar in bars:
        got = bar.get("stamp")
        if not isinstance(got, datetime):
            continue
        if got.replace(second=0, microsecond=0) != want:
            continue
        try:
            px = float(bar["open"])
        except (KeyError, TypeError, ValueError):
            continue
        if px > 0:
            return px
    return None


# The entry job is started at 13:50 so login and the three-script scan finish
# before the print. The exit job is started at 14:50 for the same reason.
ENTRY_ARM_FROM = clock(13, 40)
ENTRY_AT = clock(14, 5)
ENTRY_LATE_UNTIL = clock(14, 25)
EXIT_ARM_FROM = clock(14, 40)
EXIT_AT = clock(15, 0)


def paper_phase(at: datetime) -> str:
    """entry_arm, entry_late, exit_arm, or off.

    entry_arm: process is up before 14:05. Scan, then fill at 14:05:00.
    entry_late: already inside 14:05–14:25. Fill the 14:05 open, never a later price.
    exit_arm: process is up before 15:00. Exit at 15:00:00.
    """
    t = at.time()
    if ENTRY_ARM_FROM <= t < ENTRY_AT:
        return "entry_arm"
    if ENTRY_AT <= t <= ENTRY_LATE_UNTIL:
        return "entry_late"
    if EXIT_ARM_FROM <= t < EXIT_AT:
        return "exit_arm"
    return "off"


def fill_wait(open_wait: float, armed: bool) -> float:
    """A desk that woke on the clock already has the print. Do not sit on the candle."""
    if armed:
        return 0.0
    return max(0.0, float(open_wait))


def wait_until(
    target: clock,
    *,
    now=None,
    sleep=time.sleep,
    early_limit_s: float = 300,
) -> bool:
    """Sleep until `target` today. True only when this call woke on that clock."""
    if now is None:
        from src.master_live import now_ist

        now = now_ist
    at = now()
    due = datetime.combine(at.date(), target)
    delay = (due - at).total_seconds()
    if delay <= 0 or delay > early_limit_s:
        return False
    if delay > 0.05:
        sleep(delay - 0.02)
    while now() < due:
        sleep(0.01)
    return True


def choose_price(
    candle_open: float | None,
    snapshot: float | None,
    *,
    armed: bool,
    snapshot_label: str,
) -> tuple[float | None, str]:
    """Prefer the official bar open. Snapshot is the on-the-clock fallback."""
    if candle_open is not None and float(candle_open) > 0:
        return float(candle_open), "5m open"
    if armed and snapshot is not None and float(snapshot) > 0:
        return float(snapshot), snapshot_label
    return None, "missing"


def poll_bar_open(market, exchange: str, token: str, stamp: datetime, wait_seconds: float) -> float | None:
    """Read one five-minute open. Retries until the bar is published or time runs out."""
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    start = stamp - timedelta(minutes=2)
    end = stamp + timedelta(minutes=6)
    while True:
        bars = market.candles(exchange, str(token), start, end, interval="FIVE_MINUTE")
        px = pick_bar_open(bars, stamp)
        if px is not None:
            return px
        if time.monotonic() >= deadline:
            return None
        time.sleep(4)


def poll_pair_open(
    market,
    exchange: str,
    call_token: str,
    put_token: str,
    stamp: datetime,
    wait_seconds: float,
) -> tuple[float | None, float | None]:
    """Both legs of the same five-minute bar. A single missing leg is not a fill."""
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    call_px = put_px = None
    while True:
        if call_px is None:
            call_px = poll_bar_open(market, exchange, call_token, stamp, 0)
        if put_px is None:
            put_px = poll_bar_open(market, exchange, put_token, stamp, 0)
        if call_px is not None and put_px is not None:
            return call_px, put_px
        if time.monotonic() >= deadline:
            return None, None
        time.sleep(4)
