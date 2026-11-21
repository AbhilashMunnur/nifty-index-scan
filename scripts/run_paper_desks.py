#!/usr/bin/env python3
"""Run both Master paper desks once (synthetic + option-buy). Live orders stay off."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.broker_adapter import PaperBroker, get_broker  # noqa: E402
from src.market import IndexMarket  # noqa: E402
from src.master_book import MasterBook  # noqa: E402
from src.master_live import LIVE_SCRIPTS, collect_signals, now_ist, run_master_tick  # noqa: E402
from src.option_buy_book import OptionBuyBook  # noqa: E402
from src.option_buy_live import run_option_buy_tick  # noqa: E402
from src.option_buy_strategy import INTENT_LOG  # noqa: E402
from src.scan_slots import is_trading_day  # noqa: E402
from src.seed_pcr_history import seed_master_pcr_history  # noqa: E402


def main() -> None:
    at = now_ist()
    if not is_trading_day(at.date()):
        print(f"{at:%d-%b %Y} is not an NSE session. Paper desks stay idle.")
        return
    critical = (at.hour == 14 and at.minute <= 25) or (at.hour == 15 and at.minute <= 10)
    if not critical:
        added = seed_master_pcr_history()
        if any(added.values()):
            print("PCR seeded:", {k: v for k, v in added.items() if v})
    market = IndexMarket()
    try:
        print(f"\nPaper desks @ {at:%d-%b %Y %H:%M} IST | scripts={LIVE_SCRIPTS}")
        candidates = None
        if at.hour == 14 and at.minute <= 25:
            print("\n--- Master PCR collect (shared) ---")
            candidates = collect_signals(market, LIVE_SCRIPTS, at.date())

        print("\n--- Synthetic futures (base+filler) ---")
        for line in run_master_tick(
            market, MasterBook(), get_broker("paper"), at, candidates=candidates
        ):
            print(line)

        print("\n--- Option-buy BASE ONLY ---")
        for line in run_option_buy_tick(
            market,
            OptionBuyBook(),
            PaperBroker(log_path=INTENT_LOG),
            at,
            candidates=candidates,
        ):
            print(line)

        syn = MasterBook()
        ob = OptionBuyBook()
        print("\n=== Ledgers ===")
        print(
            f"Synthetic: capital ₹{syn.capital:,.0f} | realised ₹{syn.realised_pnl:,.0f} "
            f"| open={syn.is_open} | closed={len(syn.closed)}"
        )
        print(
            f"Option-buy: capital ₹{ob.capital:,.0f} | realised ₹{ob.realised_pnl:,.0f} "
            f"| open={ob.is_open} | closed={len(ob.closed)}"
        )
    finally:
        market.close()


if __name__ == "__main__":
    main()
