"""Option-buy BASE ONLY live paper tick — shares Master PCR history.

Latency model (matches preferred backtest):
  - 14:00–14:04: after 14:00 bar closed, rank PCR; keep BASE signals only.
  - 14:05–14:25: buy ATM CE (Buy) or PE (Sell) at LTP + size-slip.
  - Next session from 15:00: sell at LTP − size-slip.
  - Charges via leg_costs. Live broker orders stay off.
"""

from __future__ import annotations

import json
import time as time_mod
import traceback
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.broker_adapter import OrderIntent, PaperBroker
from src.master_live import LIVE_SCRIPTS, _atm_legs, collect_signals
from src.master_strategy import ENTRY_CLOCK, EXIT_CLOCK, SIGNAL_CLOCK
from src.option_buy_book import OptionBuyBook
from src.option_buy_strategy import (
    INTENT_LOG,
    LATENCY_LOG,
    NAME,
    PENDING_PATH,
    entry_window,
    exit_due,
    opt_side_for,
    pick_base_signal,
)

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _log_latency(event: str, started: float, **extra) -> None:
    ms = (time_mod.perf_counter() - started) * 1000.0
    row = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "latency_ms": round(ms, 1),
        **extra,
    }
    LATENCY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LATENCY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    print(f"  latency {event}: {ms:.0f} ms")


def _load_pending() -> dict | None:
    if not PENDING_PATH.exists():
        return None
    try:
        raw = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _save_pending(sig: dict | None) -> None:
    if sig is None:
        if PENDING_PATH.exists():
            PENDING_PATH.unlink()
        return
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(sig, indent=2) + "\n", encoding="utf-8")


def _leg_for_side(legs: dict, opt_side: str) -> tuple[str, str, float] | None:
    if opt_side == "CE":
        return legs["call_symbol"], legs["call_token"], float(legs["call_ltp"])
    if opt_side == "PE":
        return legs["put_symbol"], legs["put_token"], float(legs["put_ltp"])
    return None


def run_option_buy_tick(
    market,
    book: OptionBuyBook | None = None,
    broker=None,
    at: datetime | None = None,
    scripts: tuple[str, ...] = LIVE_SCRIPTS,
    candidates: list[dict] | None = None,
) -> list[str]:
    """One option-buy paper tick. Safe to call every scan slot.

    Pass candidates=... when the scan loop already collected PCR signals
    (shared with synthetic Master desk).
    """
    book = book or OptionBuyBook()
    broker = broker or PaperBroker(log_path=INTENT_LOG)
    at = at or now_ist()
    day = at.date()
    lines: list[str] = []
    clock = at.strftime("%H:%M")
    lines.append(f"{NAME} tick {at:%d-%b %H:%M} IST | broker={getattr(broker, 'name', '?')}")

    # --- manage open long option ---
    if book.is_open:
        pos = book.position
        sym = pos["script"]
        try:
            prices = market.get_ltps([sym])
            spot = prices.get(sym) or float(pos.get("spot_entry") or 0)
            legs = _atm_legs(market, sym, float(spot))
            raw = None
            if legs:
                got = _leg_for_side(legs, pos["opt_side"])
                if got:
                    raw = got[2]
            if raw is None:
                lines.append(f"  open {sym}: {pos['opt_side']} mark unavailable")
            elif exit_due(pos, at):
                msg = book.exit(day=day, raw_open=raw, clock=clock, why="time")
                lines.append(f"  {msg}")
                broker.place(
                    OrderIntent(
                        "Sell",
                        pos["symbol"],
                        pos["token"],
                        int(pos["qty"]),
                        raw,
                        tag="option_buy",
                        note="exit",
                    )
                )
                _save_pending(None)
            else:
                mark = raw - float(pos["slip"])
                pts = mark - float(pos["entry"])
                lines.append(
                    f"  open Buy {pos['opt_side']} {sym} mark≈{raw:.2f} "
                    f"uPnL {pts:+.2f} pts | exit after {EXIT_CLOCK} next day"
                )
        except Exception:
            traceback.print_exc()
            lines.append("  manage open failed — see traceback")
        return lines

    # --- signal capture (BASE only) ---
    pending = _load_pending()
    if pending and pending.get("day") != day.isoformat():
        pending = None
        _save_pending(None)

    if pending is None and time(14, 0) <= at.time() < time(14, 5):
        cands = candidates if candidates is not None else collect_signals(market, scripts, day)
        chosen = pick_base_signal(cands)
        if chosen:
            _save_pending(chosen)
            lines.append(
                f"  SIGNAL BASE {chosen['side']} → Buy "
                f"{opt_side_for(chosen['side'])} {chosen['script']} "
                f"rank={chosen['rank']:.3f} — wait {ENTRY_CLOCK} entry"
            )
        else:
            lines.append("  no BASE option-buy signal today (filler ignored)")
        return lines

    # --- entry window ---
    if entry_window(at) and not book.entered_on(day):
        sig = pending or _load_pending()
        if not sig or sig.get("day") != day.isoformat():
            if at.time() >= time(14, 5):
                cands = candidates if candidates is not None else collect_signals(market, scripts, day)
                sig = pick_base_signal(cands)
                if sig:
                    sig["day"] = day.isoformat()
        if not sig:
            lines.append("  entry window — no BASE signal")
            return lines
        t0 = time_mod.perf_counter()
        legs = _atm_legs(market, sig["script"], float(sig["spot"]))
        if not legs:
            lines.append(f"  entry failed — ATM legs missing for {sig['script']}")
            return lines
        opt = opt_side_for(sig["side"])
        got = _leg_for_side(legs, opt)
        if not got:
            lines.append(f"  entry failed — {opt} quote missing")
            return lines
        symbol, token, raw = got
        msg = book.enter(
            script=sig["script"],
            signal_side=sig["side"],
            day=day,
            spot=float(sig["spot"]),
            pcr=float(sig["pcr"]),
            pcr_rank=float(sig["rank"]),
            opt_side=opt,
            symbol=symbol,
            token=token,
            raw_open=raw,
            strike=int(legs["strike"]),
            expiry=str(legs["expiry"]),
            lot_size=int(legs["lot_size"]),
        )
        lines.append(f"  {msg}")
        if book.is_open:
            broker.place(
                OrderIntent(
                    "Buy",
                    symbol,
                    token,
                    int(book.position["qty"]),
                    raw,
                    tag="option_buy",
                    note="entry",
                )
            )
            _save_pending(None)
        _log_latency("entry", t0, script=sig["script"], opt=opt)
        return lines

    lines.append(
        f"  idle @ {clock} (signal {SIGNAL_CLOCK}, entry {ENTRY_CLOCK}, exit {EXIT_CLOCK})"
    )
    return lines


def run_option_buy_once(market=None) -> list[str]:
    from src.market import IndexMarket

    own = market is None
    market = market or IndexMarket()
    try:
        return run_option_buy_tick(market, OptionBuyBook(), PaperBroker(log_path=INTENT_LOG))
    finally:
        if own:
            market.close()
