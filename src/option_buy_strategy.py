"""Master Option-Buy — BASE ONLY preferred paper rules.

Same Master PCR signal stack as the synthetic desk, but:
  - BASE tier only (filler softPCR excluded)
  - Buy the directional ATM option only (Buy→CE, Sell→PE)
  - Fixed ₹1 Cr premium budget per trade (no compounding for size)
  - Size-slip on fills; STT/charges via leg_costs
  - Time exit only (next session 15:00); no SL / partial
"""

from __future__ import annotations

import math
from datetime import date, datetime

from src.master_strategy import ENTRY_WINDOW, EXIT_WINDOW_START, pick_signal
from src.paths import ROOT

NAME = "Master Option-Buy (BASE ONLY)"
BOOK_PATH = ROOT / "data" / "option_buy_book.json"
JOURNAL_NAME = "option_buy_journal.xlsx"
PENDING_PATH = ROOT / "data" / "option_buy_pending_signal.json"
INTENT_LOG = ROOT / "data" / "option_buy_order_intents.jsonl"
LATENCY_LOG = ROOT / "data" / "option_buy_latency.jsonl"

STARTING_CAPITAL = 10_000_000.0  # ₹1 Cr fixed premium budget
BASE_SLIP = 2.0
IMPACT = 1.5
REF_LOTS = {"NIFTY": 37.0, "SENSEX": 37.0, "BANKNIFTY": 30.0}


def size_slip(lots: int, symbol: str) -> float:
    if lots <= 0:
        return BASE_SLIP
    ref = float(REF_LOTS.get(symbol.upper(), 37.0))
    return BASE_SLIP + IMPACT * max(0.0, math.sqrt(lots / ref) - 1.0)


def opt_side_for(signal_side: str) -> str:
    """Master Buy → long CE; Master Sell → long PE."""
    return "CE" if signal_side == "Buy" else "PE"


def size_from_premium(
    raw_open: float,
    lot_size: int,
    symbol: str,
    budget: float = STARTING_CAPITAL,
) -> dict | None:
    """Iterate lots ↔ size-slip until stable. Always size from fixed budget."""
    raw = float(raw_open)
    if raw <= 0 or lot_size < 1 or budget <= 0:
        return None
    lots = 0
    slip = BASE_SLIP
    entry = raw + slip
    for _ in range(8):
        per_lot = entry * lot_size
        if per_lot <= 0:
            return None
        new_lots = int(budget // per_lot)
        if new_lots < 1:
            return None
        new_slip = size_slip(new_lots, symbol)
        new_entry = raw + new_slip
        if new_lots == lots and abs(new_slip - slip) < 1e-9:
            break
        lots, slip, entry = new_lots, new_slip, new_entry
    per_lot = entry * lot_size
    lots = int(budget // per_lot)
    if lots < 1:
        return None
    slip = size_slip(lots, symbol)
    entry = raw + slip
    per_lot = entry * lot_size
    if lots * per_lot > budget + 1e-6:
        lots = int(budget // per_lot)
        if lots < 1:
            return None
        slip = size_slip(lots, symbol)
        entry = raw + slip
        per_lot = entry * lot_size
    return {
        "lots": int(lots),
        "qty": int(lots) * int(lot_size),
        "slip": round(float(slip), 4),
        "entry": round(float(entry), 2),
        "per_lot": round(float(per_lot), 2),
    }


def exit_fill(raw_open: float, slip: float) -> float:
    return round(max(0.05, float(raw_open) - float(slip)), 2)


def pick_base_signal(candidates: list[dict]) -> dict | None:
    bases = [c for c in candidates if c.get("tier") == "base"]
    return pick_signal(bases)


def entry_window(at: datetime) -> bool:
    return ENTRY_WINDOW[0] <= at.time() <= ENTRY_WINDOW[1]


def exit_due(position: dict, at: datetime) -> bool:
    entry_on = date.fromisoformat(str(position["entry_date"]))
    if at.date() <= entry_on:
        return False
    return at.time() >= EXIT_WINDOW_START
