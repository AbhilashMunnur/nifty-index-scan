#!/usr/bin/env python3
"""Nifty and Sensex futures 15-minute alerts. Does not run the option OI scan."""

from __future__ import annotations

import os
import sys
import traceback

from src.fut_flow import (
    FUTURES_SYMBOLS,
    day_open_path,
    format_fut_alert,
    format_fut_alert_html,
    load_mark,
    mark_path,
    reading_from_mark,
    save_day_open_once,
    save_mark,
)
from src.fut_slots import should_run_fut_slot, write_last_fut_slot
from src.market import IndexMarket
from src.models import FutureContract
from src.scan_slots import now_ist
from src.telegram_client import TelegramClient, TelegramError


def run_once(*, force: bool = False) -> bool:
    run, reason, slot = should_run_fut_slot(force=force)
    print(f"Futures slot guard: {reason}")
    if not run:
        return True
    if slot is not None:
        write_last_fut_slot(slot)
        print(f"Claimed futures slot {slot:%Y-%m-%d %H:%M} IST")

    market = IndexMarket()
    quotes: list[tuple[str, FutureContract, float, int]] = []
    try:
        for symbol in FUTURES_SYMBOLS:
            contracts = market.listed_futures(symbol)
            if not contracts:
                print(f"No {symbol} futures listed")
                return False
            contract = contracts[0]
            price, oi = market.get_future_quote(contract)
            if price is None or oi is None:
                print(f"{contract.tradingsymbol}: futures price or open interest missing")
                return False
            quotes.append((symbol, contract, price, oi))
    finally:
        market.close()

    when = now_ist()
    try:
        client = TelegramClient()
    except TelegramError as exc:
        print(f"Telegram: {exc}")
        return False

    delivered_ok = True
    for symbol, contract, price, oi in quotes:
        path = mark_path(symbol)
        reading = reading_from_mark(
            load_mark(path),
            price=price,
            oi=oi,
            lot_size=contract.lot_size,
            tradingsymbol=contract.tradingsymbol,
            expiry=contract.expiry,
        )
        text = format_fut_alert(reading, when=when, name=symbol)
        print(text)
        print()
        save_mark(
            tradingsymbol=contract.tradingsymbol,
            token=contract.token,
            expiry=contract.expiry,
            lot_size=contract.lot_size,
            price=price,
            oi=oi,
            path=path,
        )
        save_day_open_once(
            tradingsymbol=contract.tradingsymbol,
            token=contract.token,
            expiry=contract.expiry,
            lot_size=contract.lot_size,
            price=price,
            oi=oi,
            path=day_open_path(symbol),
        )
        delivered = client.send_message(
            format_fut_alert_html(reading, when=when, name=symbol),
            parse_mode="HTML",
        )
        print(f"Telegram {symbol}: sent to {delivered} recipient(s)")
        if delivered < 1:
            delivered_ok = False
    return delivered_ok


def main() -> None:
    force = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    try:
        ok = run_once(force=force)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
