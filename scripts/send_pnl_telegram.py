#!/usr/bin/env python3
"""Render + send the broker-style P&L JPG to Telegram."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.paper import NiftyPaperBook
from src.pnl_card import render_pnl_card
from src.telegram_client import TelegramClient, TelegramError


def main() -> None:
    book = NiftyPaperBook()
    mark = 23499.0 if book.is_open else None
    card = render_pnl_card(
        position=book.position if book.is_open else None,
        realised_pnl=book.realised_pnl,
        mark=mark,
        closed=book.closed,
        as_of="11 Sep 2026 15:40 IST (Fri close)",
        spot=23398.10,
        out_path=ROOT / "data" / "pnl_cards" / "latest_pnl.jpg",
    )
    print(f"Wrote {card}")
    caption = (
        f"Nifty paper P&L · {datetime.now():%d %b %Y %H:%M}\n"
        f"Weekend mark from Friday 15:40 IST futures LTP.\n"
        f"Every 15-min scan will send this JPG card."
    )
    try:
        delivered = TelegramClient().send_photo(card, caption=caption)
    except TelegramError as exc:
        raise SystemExit(exc) from exc
    print(f"Telegram photo: sent to {delivered} recipient(s)")


if __name__ == "__main__":
    main()
