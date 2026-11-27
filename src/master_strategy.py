"""Master Strategy — production rules (paper). Live broker orders stay off.

Tier-1 BASE (priority over filler):
  NIFTY   extreme PCR: Buy if rank≤0.10, Sell if rank≥0.90
  SENSEX  aligned PCR: Buy if rank≤0.20, Sell if rank≥0.80
  BANKNIFTY aligned: same as Sensex

Tier-2 FILLER (only if that script has no base signal that day):
  soft PCR: Buy if rank≤0.30, Sell if rank≥0.70

Overlap (one trade/day): prefer any BASE, else FILLER; among scripts
  BANKNIFTY > SENSEX > NIFTY

Timing (honest):
  Signal uses 14:00 index CLOSE + PCR rank.
  Entry window 14:05–14:25 at option OPEN (else LTP) ± slip.
  If that book is still flat, a 15:00 PCR rank is checked on its own history.
  Only a base rank at 15:00 is taken, at the 15:05 open.
  Exit next session from 15:00 at option OPEN (else LTP) ± slip.
  Filler only: book 50% if unrealized synthetic pts ≥ TP1
    (200 for Nifty, 500 for BN/Sensex). Remainder to time exit.
  No stop-loss (scan showed SL cut honest P&L).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time
from pathlib import Path

from src.paths import ROOT

NAME = "Master Pro Synthetic"
BOOK_PATH = ROOT / "data" / "master_strategy_book.json"
PCR_PATH = ROOT / "data" / "master_pcr_history.json"
PCR_PATH_1500 = ROOT / "data" / "master_pcr_history_1500.json"
JOURNAL_NAME = "master_strategy_journal.xlsx"

STARTING_CAPITAL = 100_000_000.0  # ₹10 Cr paper
LOT_CAP = 1000
BASE_SLIP = 2.0
NIFTY_MARGIN = 268_518.0
MARGIN_FRAC = 0.12

ENTRY_CLOCK = "14:05"
SIGNAL_CLOCK = "14:00"
EXIT_CLOCK = "15:00"
SECOND_SIGNAL_CLOCK = "15:00"
SECOND_ENTRY_CLOCK = "15:05"
ENTRY_WINDOW = (time(14, 5), time(14, 25))
SECOND_ENTRY_WINDOW = (time(15, 5), time(15, 25))
EXIT_WINDOW_START = time(15, 0)

# PCR thresholds
BASE = {
    "NIFTY": ("extreme", 0.10, 0.90),
    "SENSEX": ("aligned", 0.20, 0.80),
    "BANKNIFTY": ("aligned", 0.20, 0.80),
}
FILLER_LO, FILLER_HI = 0.30, 0.70
PRIORITY = ("BANKNIFTY", "SENSEX", "NIFTY")

# Partial TP1 (synthetic points); scale for heavier underlyings
TP1 = {"NIFTY": 200.0, "SENSEX": 500.0, "BANKNIFTY": 500.0}
PARTIAL_FRAC = 0.5

STRIKE_STEP = {"NIFTY": 50, "SENSEX": 100, "BANKNIFTY": 100}
WINGS = 6  # ATM ± 6


def atm_strike(spot: float, symbol: str) -> int:
    step = STRIKE_STEP[symbol]
    return int(math.floor(float(spot) / step + 0.5)) * step


def pcr_percentile(values: list[float], window: int = 60) -> float | None:
    if len(values) < max(20, window // 2):
        return None
    sample = values[-window:]
    prior = sample[:-1]
    if not prior:
        return None
    latest = sample[-1]
    return sum(v < latest for v in prior) / len(prior)


def load_pcr_history(path: Path = PCR_PATH) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {s: {} for s in PRIORITY}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {s: {} for s in PRIORITY}
    for sym, series in (raw or {}).items():
        key = str(sym).upper()
        if key not in out:
            out[key] = {}
        for day, val in (series or {}).items():
            try:
                out[key][str(day)[:10]] = float(val)
            except (TypeError, ValueError):
                continue
    return out


def save_pcr_history(hist: dict[str, dict[str, float]], path: Path = PCR_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def remember_pcr(hist: dict[str, dict[str, float]], symbol: str, day: date, pcr: float) -> float | None:
    """Store today's PCR and return 60-session rank (using history through today)."""
    key = symbol.upper()
    hist.setdefault(key, {})[day.isoformat()] = float(pcr)
    days = sorted(hist[key])
    vals = [hist[key][d] for d in days if d <= day.isoformat()]
    return pcr_percentile(vals, 60)


def classify_tier(symbol: str, rank: float) -> tuple[str | None, str | None]:
    """Return (tier, side) or (None, None)."""
    sym = symbol.upper()
    if sym not in BASE:
        return None, None
    _, lo, hi = BASE[sym]
    if rank <= lo:
        return "base", "Buy"
    if rank >= hi:
        return "base", "Sell"
    if rank <= FILLER_LO:
        return "filler", "Buy"
    if rank >= FILLER_HI:
        return "filler", "Sell"
    return None, None


def pick_signal(candidates: list[dict]) -> dict | None:
    """candidates: dicts with script, tier, side, rank, ... Prefer base, then PRIORITY."""
    if not candidates:
        return None
    bases = [c for c in candidates if c.get("tier") == "base"]
    pool = bases if bases else [c for c in candidates if c.get("tier") == "filler"]
    if not pool:
        return None
    for script in PRIORITY:
        for c in pool:
            if c.get("script") == script:
                return c
    return pool[0]


def entry_window(at: datetime) -> bool:
    return ENTRY_WINDOW[0] <= at.time() <= ENTRY_WINDOW[1]


def second_entry_window(at: datetime) -> bool:
    return SECOND_ENTRY_WINDOW[0] <= at.time() <= SECOND_ENTRY_WINDOW[1]


def exit_due(position: dict, at: datetime) -> bool:
    entry_on = date.fromisoformat(str(position["entry_date"]))
    if at.date() <= entry_on:
        return False
    return at.time() >= EXIT_WINDOW_START


def fill_buy(px: float, slip: float = BASE_SLIP) -> float:
    return float(px) + slip


def fill_sell(px: float, slip: float = BASE_SLIP) -> float:
    return max(0.05, float(px) - slip)


def synth_entry_fills(side: str, ce: float, pe: float, slip: float = BASE_SLIP) -> tuple[float, float]:
    if side == "Buy":
        return fill_buy(ce, slip), fill_sell(pe, slip)
    return fill_sell(ce, slip), fill_buy(pe, slip)


def synth_exit_fills(side: str, ce: float, pe: float, slip: float = BASE_SLIP) -> tuple[float, float]:
    if side == "Buy":
        return fill_sell(ce, slip), fill_buy(pe, slip)
    return fill_buy(ce, slip), fill_sell(pe, slip)


def synth_points(side: str, ce_e: float, pe_e: float, ce_x: float, pe_x: float) -> float:
    if side == "Buy":
        return (ce_x - ce_e) + (pe_e - pe_x)
    return (ce_e - ce_x) + (pe_x - pe_e)


def margin_per_lot(symbol: str, spot: float, lot_size: int) -> float:
    if symbol.upper() == "NIFTY":
        return float(NIFTY_MARGIN)
    return max(60_000.0, MARGIN_FRAC * float(spot) * int(lot_size))


def lots_for(capital: float, mpl: float, cap: int = LOT_CAP) -> int:
    if mpl <= 0:
        return 0
    return min(int(capital // mpl), int(cap))


def tp1_for(symbol: str) -> float:
    return float(TP1.get(symbol.upper(), 200.0))


def opening_orders(side: str) -> tuple[str, str]:
    return ("Buy", "Sell") if side == "Buy" else ("Sell", "Buy")
