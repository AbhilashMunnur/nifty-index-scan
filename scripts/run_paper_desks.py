#!/usr/bin/env python3
"""Run both Master paper desks once (synthetic + option-buy). Live orders stay off."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datetime import time as clock  # noqa: E402

from src.broker_adapter import PaperBroker, get_broker  # noqa: E402
from src.clock_fill import wait_until  # noqa: E402
from src.market import IndexMarket  # noqa: E402
from src.master_book import MasterBook  # noqa: E402
from src.master_live import (  # noqa: E402
    LIVE_SCRIPTS,
    _atm_legs,
    _load_pending as load_master_pending,
    _position_marks,
    collect_signals,
    now_ist,
    run_master_tick,
)
from src.master_strategy import pick_signal  # noqa: E402
from src.option_buy_book import OptionBuyBook  # noqa: E402
from src.option_buy_live import _load_pending as load_option_pending  # noqa: E402
from src.option_buy_live import run_option_buy_tick  # noqa: E402
from src.option_buy_strategy import INTENT_LOG, pick_base_signal  # noqa: E402
from src.scan_slots import is_trading_day  # noqa: E402
from src.seed_pcr_history import seed_master_pcr_history  # noqa: E402
from src.universe import require_index  # noqa: E402

# The runner is started a couple of minutes early, then this process waits
# until the print. A late start still fills the bar open, not a later LTP.
OPEN_WAIT_SECONDS = 90


def _signal_for(day, pending: dict | None, candidates: list[dict] | None, pick) -> dict | None:
    """Same choice the tick will make: today's saved signal, else this collect."""
    if pending and pending.get("day") == day.isoformat():
        return pending
    chosen = pick(candidates or [])
    if chosen:
        chosen = dict(chosen)
        chosen["day"] = day.isoformat()
    return chosen


def _legs_for(market, sig: dict | None, day) -> dict | None:
    if not sig:
        return None
    legs = _atm_legs(market, sig["script"], float(sig["spot"]), after=day)
    if not legs:
        return None
    legs = dict(legs)
    legs["script"] = sig["script"]
    legs["stamped"] = False
    return legs


def _restamp(market, legs: dict | None) -> dict | None:
    """Quote the already chosen contracts the moment the clock is reached."""
    if not legs:
        return None
    spec = require_index(legs["script"])
    tokens = [legs["call_token"], legs["put_token"]]
    quotes = market._fetch_option_quotes(spec.fno_exchange, tokens)
    call_px = (quotes.get(legs["call_token"]) or {}).get("ltp")
    put_px = (quotes.get(legs["put_token"]) or {}).get("ltp")
    legs = dict(legs)
    if call_px is None or put_px is None:
        return legs
    legs["call_ltp"] = float(call_px)
    legs["put_ltp"] = float(put_px)
    legs["stamped"] = True
    print(
        f"  14:05 snapshot {legs['script']} CE {legs['call_ltp']:.2f} "
        f"PE {legs['put_ltp']:.2f}"
    )
    return legs


def _exit_pins(market) -> tuple[dict | None, dict | None]:
    """LTP of the open contracts at 15:00, used only if the 15:00 bar is late."""
    syn = MasterBook()
    ob = OptionBuyBook()
    syn_pin = None
    if syn.is_open:
        pos = syn.position
        marks = _position_marks(market, pos)
        if marks:
            syn_pin = {
                "script": pos["script"],
                "call_ltp": marks[0],
                "put_ltp": marks[1],
                "stamped": True,
            }
            print(f"  15:00 snapshot {pos['script']} CE {marks[0]:.2f} PE {marks[1]:.2f}")
    ob_pin = None
    if ob.is_open:
        pos = ob.position
        token = str(pos.get("token") or "")
        if token and token != "hist":
            spec = require_index(pos["script"])
            quotes = market._fetch_option_quotes(spec.fno_exchange, [token])
            px = (quotes.get(token) or {}).get("ltp")
            if px is not None:
                ob_pin = {"script": pos["script"], "ltp": float(px), "stamped": True, "token": token}
                print(f"  15:00 snapshot {pos['script']} {pos['opt_side']} {float(px):.2f}")
    return syn_pin, ob_pin


def main() -> None:
    at = now_ist()
    if not is_trading_day(at.date()):
        print(f"{at:%d-%b %Y} is not an NSE session. Paper desks stay idle.")
        return
    early_entry = clock(13, 58) <= at.time() < clock(14, 5)
    early_exit = clock(14, 55) <= at.time() < clock(15, 0)
    critical = early_entry or early_exit or (at.hour == 14 and at.minute <= 25) or (
        at.hour == 15 and at.minute <= 10
    )
    if not critical:
        added = seed_master_pcr_history()
        if any(added.values()):
            print("PCR seeded:", {k: v for k, v in added.items() if v})
    market = IndexMarket()
    try:
        print(f"\nPaper desks @ {at:%d-%b %Y %H:%M:%S} IST | scripts={LIVE_SCRIPTS}")
        candidates = None
        pinned_synth = None
        pinned_opt = None
        exit_synth = None
        exit_opt = None
        if early_entry or (at.hour == 14 and at.minute <= 25):
            print("\n--- Master PCR collect (shared) ---")
            candidates = collect_signals(market, LIVE_SCRIPTS, at.date())
        if early_entry:
            day = at.date()
            pinned_synth = _legs_for(
                market,
                _signal_for(day, load_master_pending(), candidates, pick_signal),
                day,
            )
            pinned_opt = _legs_for(
                market,
                _signal_for(day, load_option_pending(), candidates, pick_base_signal),
                day,
            )
            print("Waiting for 14:05:00 IST so the fill is that print.")
            if wait_until(clock(14, 5)):
                pinned_synth = _restamp(market, pinned_synth)
                pinned_opt = _restamp(market, pinned_opt)
            at = now_ist()
        elif early_exit:
            print("Waiting for 15:00:00 IST so the exit is that print.")
            if wait_until(clock(15, 0)):
                exit_synth, exit_opt = _exit_pins(market)
            at = now_ist()

        print("\n--- Synthetic futures (base+filler) ---")
        for line in run_master_tick(
            market,
            MasterBook(),
            get_broker("paper"),
            at,
            candidates=candidates,
            pinned_legs=pinned_synth,
            exit_pin=exit_synth,
            open_wait=OPEN_WAIT_SECONDS,
        ):
            print(line)

        print("\n--- Option-buy BASE ONLY ---")
        for line in run_option_buy_tick(
            market,
            OptionBuyBook(),
            PaperBroker(log_path=INTENT_LOG),
            at,
            candidates=candidates,
            pinned_legs=pinned_opt,
            exit_pin=exit_opt,
            open_wait=OPEN_WAIT_SECONDS,
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
