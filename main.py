#!/usr/bin/env python3
"""Nifty/Sensex OI scans + Master paper desks (synthetic + option-buy). Live orders stay off."""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time as clock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.market import IndexMarket
from src.observe import format_digest, format_snapshot
from src.oi_flow import (
    classify_band_flow,
    format_band_flow,
    format_flow_caption,
    mark_path_for,
    save_premium_mark,
)
from src.pcr import BandPcr
from src.power_hour import (
    format_power_hour_report,
    is_capture_slot,
    is_report_slot,
    save_baseline,
    saved_wide_strikes,
    should_capture_baseline,
    slot_clock,
    todays_mark,
    wide_wings,
)
from src.report import format_signal_report
from src.scan_slots import active_slot, should_run_slot, write_last_slot
from src.session import is_market_session, now_ist, seconds_until_next_slot
from src.strategy import evaluate_signal
from src.universe import UniverseError, require_index, validate_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nifty and Sensex 1H strategy. No live orders."
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Print Nifty and Sensex snapshots instead of running the strategy.",
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="With --observe, optional subset of NIFTY SENSEX.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Telegram even if there is no trade signal.",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Skip Telegram even if it is enabled in config.",
    )
    parser.add_argument(
        "--no-paper",
        action="store_true",
        help="Evaluate signals without writing the paper ledger.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Scan every 15 minutes in NSE hours and Telegram each snapshot.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit (GitHub Actions default).",
    )
    parser.add_argument(
        "--slot-guard",
        action="store_true",
        help="Only run unpaid 15-minute IST slots (09:30–15:40). Used by GitHub Actions.",
    )
    return parser.parse_args()


def maybe_telegram(config, args, text: str, *, force: bool) -> bool:
    if args.no_telegram:
        return False
    if not (args.notify or (config.telegram and force)):
        return False
    from src.telegram_client import TelegramClient, TelegramError

    last: Exception | str | None = None
    for attempt in range(1, 4):
        try:
            client = TelegramClient()
            delivered = client.send_message(text, scan_html=True)
            print(f"Telegram: sent to {delivered}/{len(client.chat_ids)} recipient(s)")
            return delivered > 0
        except TelegramError as exc:
            last = exc
            print(f"Telegram attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                time.sleep(2 * attempt)
    print(f"Telegram skipped after retries: {last}")
    return False


def maybe_telegram_photo(
    config,
    args,
    image_path,
    *,
    caption: str,
    force: bool,
) -> bool:
    if args.no_telegram:
        return False
    if not (args.notify or (config.telegram and force)):
        return False
    from src.telegram_client import TelegramClient, TelegramError

    last: Exception | str | None = None
    for attempt in range(1, 4):
        try:
            client = TelegramClient()
            delivered = client.send_photo(image_path, caption=caption)
            print(
                f"Telegram photo: sent to {delivered}/{len(client.chat_ids)} recipient(s)"
            )
            return delivered > 0
        except TelegramError as exc:
            last = exc
            print(f"Telegram photo attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                time.sleep(2 * attempt)
    print(f"Telegram photo skipped after retries: {last}")
    return False


def run_observe(config, args) -> None:
    try:
        symbols = (
            validate_universe(args.symbols) if args.symbols else list(config.universe)
        )
        for symbol in symbols:
            require_index(symbol)
    except UniverseError as exc:
        print(exc)
        sys.exit(1)

    print("Observe mode: NIFTY and SENSEX quotes + nearest-expiry OI.\n")
    market = IndexMarket()
    try:
        prices = market.get_ltps(symbols)
        snapshots = []
        for symbol in symbols:
            ltp = prices.get(symbol)
            if not ltp:
                print(f"{symbol}: no index LTP")
                continue
            snapshot = market.get_oi_snapshot(symbol, ltp=ltp)
            if not snapshot:
                print(f"{symbol}: option chain unavailable")
                continue
            snapshots.append(snapshot)
            if config.console:
                print(format_snapshot(snapshot))
                print()
        if not snapshots:
            sys.exit(1)
        maybe_telegram(
            config, args, format_digest(snapshots, mode="observe"), force=True
        )
    finally:
        market.close()


_STRATEGY_LABELS = ("ITM2", "ITM1", "ATM", "OTM1", "OTM2")


def _strategy_band(band):
    """Entry rules stay on the middle five strikes when the scan list is wider."""
    if band is None or len(band.strikes) <= 5:
        return band
    rows = [row for row in band.strikes if row.label in _STRATEGY_LABELS]
    if len(rows) != 5:
        return band
    return BandPcr(
        expiry=band.expiry,
        atm=band.atm,
        step=band.step,
        lot_size=band.lot_size,
        strikes=rows,
    )


def _option_band(market, symbol: str, spot: float):
    """Tue/Thu from 14:30: 13 strikes frozen at the 2:30 ATM. Other scans stay at 5."""
    now = now_ist()
    wings = wide_wings(now)
    fixed = saved_wide_strikes(symbol, now) if wings == 6 else None
    return market.get_atm_band_pcr(symbol, spot, wings=wings, fixed=fixed)


def _fetch_sensex_band(market) -> tuple[object | None, float | None]:
    try:
        spot = market.get_ltps(["SENSEX"]).get("SENSEX")
    except Exception as exc:
        print(f"Power-hour Sensex LTP failed: {exc}")
        return None, None
    if not spot:
        print("Power-hour Sensex: no index LTP")
        return None, None
    try:
        return _option_band(market, "SENSEX", spot), spot
    except Exception as exc:
        print(f"Power-hour Sensex chain failed: {exc}")
        return None, spot


def maybe_power_hour(
    config,
    args,
    market,
    *,
    slot,
    nifty,
    nifty_spot,
    sensex=None,
    sensex_spot=None,
) -> None:
    """Extra 15:00 / 15:10 / 15:15 Telegram: Nifty + Sensex CE/PE BUY vs WRITE since 14:30."""
    if slot is None or not (is_capture_slot(slot) or is_report_slot(slot)):
        return

    if sensex is None:
        sensex, sensex_spot = _fetch_sensex_band(market)
    if should_capture_baseline(slot):
        save_baseline(
            nifty=nifty,
            sensex=sensex,
            nifty_spot=nifty_spot,
            sensex_spot=sensex_spot,
            slot=slot,
        )
        clock = slot_clock(slot)
        label = clock.strftime("%H:%M") if clock else "14:30"
        print(f"Power-hour baseline stored ({label} IST) for 15:00 / 15:10 / 15:15 extra scans.")

    if not is_report_slot(slot):
        return

    mark = todays_mark(now=slot)
    end_label = slot.strftime("%H:%M")
    if not mark or not (mark.get("indices") or {}).get("NIFTY"):
        msg = (
            f"Power hour {end_label} extra scan skipped — "
            "no 14:30 (or 14:45 fallback) Nifty baseline today."
        )
        print(msg)
        maybe_telegram(config, args, msg, force=True)
        return

    text = format_power_hour_report(
        end_label=end_label,
        nifty=nifty,
        sensex=sensex,
        nifty_spot=nifty_spot,
        sensex_spot=sensex_spot,
        mark=mark,
    )
    print(text)
    print()
    maybe_telegram(config, args, text, force=True)


def _pretty_index(symbol: str) -> str:
    return "Nifty" if symbol.upper() == "NIFTY" else symbol.title()


@dataclass
class IndexScanResult:
    symbol: str
    ok: bool
    ltp: float | None = None
    rsi: float | None = None
    band: object | None = None
    band_flow: object | None = None
    signal: str | None = None
    report: str = ""
    events: list[str] = field(default_factory=list)
    book: object | None = None
    future_ltp: float | None = None


def scan_one_index(config, args, market, symbol: str, ltp: float | None) -> IndexScanResult:
    """1H RSI + 5-strike Angel OI/PCR observation scan (no RSI paper fills)."""
    rules = config.rules
    label = _pretty_index(symbol)
    if not ltp:
        msg = f"{label} 1H scan {now_ist():%H:%M IST}: no index LTP"
        print(msg)
        return IndexScanResult(symbol=symbol, ok=False, report=msg)

    rsi = market.hourly_rsi(symbol, ltp, period=rules.rsi_period)
    listed = market.listed_futures(symbol)
    skipped = [
        (row.tradingsymbol, row.trading_days_left)
        for row in listed
        if row.trading_days_left < rules.min_trading_days_to_expiry
    ]
    future = market.tradeable_future(
        symbol, min_trading_days=rules.min_trading_days_to_expiry
    )
    future_ltp = market.get_future_ltp(future) if future else None
    band = _option_band(market, symbol, ltp)
    signal_band = _strategy_band(band)
    signal, reason = evaluate_signal(rsi, signal_band, rules)

    report = format_signal_report(
        spot=ltp,
        rsi=rsi,
        future=future,
        skipped=skipped,
        band=signal_band,
        signal=signal,
        reason=reason,
        future_ltp=future_ltp,
        symbol=symbol,
    )
    band_flow = None
    if band is not None:
        mark = mark_path_for(symbol)
        band_flow = classify_band_flow(band, path=mark)
        flow_text = format_band_flow(band, band_flow)
        report = report + "\n\n" + flow_text
        print(flow_text)
        print()
        save_premium_mark(band, path=mark)
    if config.console:
        print(report)
        print()

    # RSI 1H futures paper book retired — only Master desks paper-trade now.
    events: list[str] = []
    notify_text = report
    force = config.telegram_every_scan or bool(signal)
    if force and band_flow:
        notify_text = report + "\n\n" + format_flow_caption(band_flow)
    maybe_telegram(config, args, notify_text, force=force)

    return IndexScanResult(
        symbol=symbol,
        ok=True,
        ltp=ltp,
        rsi=rsi,
        band=band,
        band_flow=band_flow,
        signal=signal,
        report=notify_text,
        events=events,
        book=None,
        future_ltp=future_ltp,
    )


def _seed_pcr_once() -> None:
    """Warm Master PCR ranks from Fyers OI archives (idempotent)."""
    try:
        from src.seed_pcr_history import seed_master_pcr_history

        added = seed_master_pcr_history()
        if any(added.values()):
            print(
                "PCR history seeded: "
                + ", ".join(f"{k}+{v}" for k, v in added.items() if v)
            )
    except Exception:
        traceback.print_exc()
        print("PCR history seed skipped — ranks may warm from live only.")


def _run_master_desks(market) -> None:
    """Run both Master paper desks off one shared PCR collect when needed."""
    try:
        from datetime import time as dtime

        from src.broker_adapter import PaperBroker, get_broker
        from src.master_book import MasterBook
        from src.master_live import LIVE_SCRIPTS, collect_signals, run_master_tick
        from src.option_buy_book import OptionBuyBook
        from src.option_buy_live import run_option_buy_tick
        from src.option_buy_strategy import INTENT_LOG

        at = now_ist()
        candidates = None
        # Shared collect in signal / entry windows so ranks stay identical.
        if dtime(14, 0) <= at.time() <= dtime(14, 25):
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
    except Exception:
        traceback.print_exc()
        print("Master paper desks failed. The other scans still stand.")


def run_strategy(config, args, slot=None) -> bool:
    symbols = list(config.strategy_symbols)
    rules = config.rules
    names = " + ".join(_pretty_index(s) for s in symbols)
    print(
        f"{names} Angel OI observation + Master paper desks.\n"
        f"Paper trades: synthetic futures (base+filler) and option-buy BASE only. "
        f"RSI 1H / Loka / instinct paper books are retired. Live orders stay off.\n"
    )

    market = IndexMarket()
    try:
        if config.mode == "paper" and not args.no_paper:
            # Fill before the OI scan. A 14:05 entry or 15:00 exit should not
            # wait for the rest of the slot.
            at = now_ist()
            critical = (clock(14, 0) <= at.time() <= clock(14, 25)) or (
                clock(15, 0) <= at.time() <= clock(15, 10)
            )
            if not critical:
                _seed_pcr_once()
            _run_master_desks(market)
        prices = market.get_ltps(symbols)
        results: list[IndexScanResult] = []
        for symbol in symbols:
            print(f"\n=== {symbol} ===")
            result = scan_one_index(config, args, market, symbol, prices.get(symbol))
            if not result.ok:
                maybe_telegram(config, args, result.report, force=True)
            results.append(result)

        by_symbol = {row.symbol: row for row in results}
        nifty = by_symbol.get("NIFTY")
        sensex = by_symbol.get("SENSEX")
        maybe_power_hour(
            config,
            args,
            market,
            slot=slot or active_slot(),
            nifty=nifty.band if nifty and nifty.ok else None,
            nifty_spot=nifty.ltp if nifty and nifty.ok else None,
            sensex=sensex.band if sensex and sensex.ok else None,
            sensex_spot=sensex.ltp if sensex and sensex.ok else None,
        )
        return any(row.ok for row in results)
    finally:
        market.close()


def run_loop(config, args) -> None:
    interval = max(1, config.interval_minutes)
    now = now_ist()
    live = is_market_session(config.market_start, config.market_end, now)
    banner = (
        f"Nifty + Sensex OI scan loop is on.\n"
        f"Paper desks: Master synthetic (base+filler) + option-buy BASE only.\n"
        f"Telegram every {interval} min, {config.market_start}–{config.market_end} IST.\n"
        f"Live Angel One orders stay off.\n"
        f"Clock {now:%d %b %Y %H:%M IST} — "
        + ("in session, scanning." if live else "outside hours, waiting for the next NSE open.")
    )
    print(banner + "\nCtrl+C to stop.\n")
    maybe_telegram(config, args, banner, force=True)
    while True:
        try:
            now = now_ist()
            if is_market_session(config.market_start, config.market_end, now):
                print(f"\n=== {now:%d %b %Y %H:%M IST} ===")
                try:
                    ok = run_strategy(config, args, slot=active_slot())
                    if not ok:
                        maybe_telegram(
                            config,
                            args,
                            f"Nifty/Sensex 1H scan at {now:%H:%M IST} did not complete — retrying next slot.",
                            force=True,
                        )
                except Exception:
                    traceback.print_exc()
                    maybe_telegram(
                        config,
                        args,
                        f"Scan failed at {now:%H:%M IST}\n{traceback.format_exc()[-1500:]}",
                        force=True,
                    )
            else:
                print(
                    f"{now:%d %b %H:%M IST} outside {config.market_start}–{config.market_end} IST "
                    f"— next slot in {interval} min"
                )
        except Exception:
            traceback.print_exc()

        wait = seconds_until_next_slot(interval)
        if wait < 1:
            wait = interval * 60
        time.sleep(wait)


def main() -> None:
    args = parse_args()
    config = load_config()
    if args.observe:
        run_observe(config, args)
        return

    slot = None
    if args.slot_guard:
        force = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        run, reason, slot = should_run_slot(force=force)
        print(f"Slot guard: {reason}")
        if not run:
            return
        if slot is not None:
            write_last_slot(slot)
            print(f"Claimed scan slot {slot:%Y-%m-%d %H:%M} IST")
            remaining = (slot - now_ist()).total_seconds()
            if remaining > 1:
                print(f"Warmup: sleeping {int(remaining)}s until {slot:%H:%M} IST")
                time.sleep(remaining)

    if args.loop:
        run_loop(config, args)
        return

    ok = run_strategy(config, args, slot=slot)
    if args.slot_guard and slot is not None and ok:
        write_last_slot(slot)
        print(f"Marked scan slot {slot:%Y-%m-%d %H:%M} IST complete.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
