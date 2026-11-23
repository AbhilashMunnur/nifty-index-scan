"""Pin a paper fill to the 14:05 or 15:00 five-minute option open.

GitHub can start the job a minute late. The price that matches the backtest
is the five-minute bar open, which does not change after the bar starts.
A snapshot is used only when this process was waiting and woke on the clock,
and the candle is not published yet.
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
