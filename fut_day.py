#!/usr/bin/env python3
"""End-of-day Nifty and Sensex futures summary. Does not run the option scan."""

from __future__ import annotations

import sys
import traceback

from src.fut_flow import (
    FUTURES_SYMBOLS,
    day_open_path,
    format_fut_day_alert,
    format_fut_day_alert_html,
    load_mark,
    mark_path,
    reading_from_mark,
)
from src.scan_slots import is_trading_day, now_ist
from src.telegram_client import TelegramClient, TelegramError


def run_day() -> bool:
    now = now_ist()
    if not is_trading_day(now.date()):
        print(f"{now:%Y-%m-%d} is not a trading day")
        return True

    today = now.strftime("%Y-%m-%d")
    try:
        client = TelegramClient()
    except TelegramError as exc:
        print(f"Telegram: {exc}")
        return False

    delivered_ok = True
    for symbol in FUTURES_SYMBOLS:
        opened = load_mark(day_open_path(symbol))
        closed = load_mark(mark_path(symbol))
        if not str(opened.get("updated_at", "")).startswith(today):
            print(f"{symbol}: morning futures mark is missing")
            delivered_ok = False
            continue
        if not str(closed.get("updated_at", "")).startswith(today):
            print(f"{symbol}: closing futures mark is missing")
            delivered_ok = False
            continue
        reading = reading_from_mark(
            opened,
            price=float(closed["price"]),
            oi=int(closed["oi"]),
            lot_size=int(closed["lot_size"]),
            tradingsymbol=str(closed["tradingsymbol"]),
            expiry=str(closed["expiry"]),
        )
        text = format_fut_day_alert(
            reading,
            when=now,
            name=symbol,
            start_at=str(opened["updated_at"]),
            end_at=str(closed["updated_at"]),
        )
        print(text)
        print()
        delivered = client.send_message(
            format_fut_day_alert_html(
                reading,
                when=now,
                name=symbol,
                start_at=str(opened["updated_at"]),
                end_at=str(closed["updated_at"]),
            ),
            parse_mode="HTML",
        )
        print(f"Telegram {symbol}: sent to {delivered} recipient(s)")
        if delivered < 1:
            delivered_ok = False
    return delivered_ok


def main() -> None:
    try:
        ok = run_day()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
