"""Option-buy BASE ONLY live paper tick — shares Master PCR history.

Latency model (matches preferred backtest):
  - 14:00–14:04: after 14:00 bar closed, rank PCR; keep BASE signals only.
  - 14:05: buy ATM CE (Buy) or PE (Sell) at the five-minute OPEN + size-slip.
  - If this book is still flat, a 15:00 base rank is bought at the 15:05 open.
  - Next session 15:00: sell at the five-minute OPEN − size-slip.
  - Charges via leg_costs. Live broker orders stay off.
"""

from __future__ import annotations

import json
import time as time_mod
import traceback
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.broker_adapter import OrderIntent, PaperBroker
from src.clock_fill import choose_price, poll_bar_open
from src.master_live import LIVE_SCRIPTS, _atm_legs, _position_marks, collect_signals
from src.universe import require_index
from src.master_strategy import ENTRY_CLOCK, EXIT_CLOCK, SIGNAL_CLOCK, second_entry_window
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


def _official_raw(
    market,
    script: str,
    token: str,
    stamp: datetime,
    pin: dict | None,
    wait_seconds: float,
    snapshot_label: str,
) -> tuple[float | None, str]:
    spec = require_index(script)
    armed = bool(pin and pin.get("stamped"))
    # The on-the-clock quote is the fill. A candle poll here would push the entry late.
    candle = None if armed else poll_bar_open(market, spec.fno_exchange, token, stamp, wait_seconds)
    snapshot = float(pin["ltp"]) if armed and pin.get("ltp") is not None else None
    return choose_price(candle, snapshot, armed=armed, snapshot_label=snapshot_label)


def run_option_buy_tick(
    market,
    book: OptionBuyBook | None = None,
    broker=None,
    at: datetime | None = None,
    scripts: tuple[str, ...] = LIVE_SCRIPTS,
    candidates: list[dict] | None = None,
    pinned_legs: dict | None = None,
    exit_pin: dict | None = None,
    open_wait: float = 25,
    fill_at: time | None = None,
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
            raw = None
            token = str(pos.get("token") or "")
            if token and token != "hist":
                spec = require_index(sym)
                quotes = market._fetch_option_quotes(spec.fno_exchange, [token])
                px = (quotes.get(token) or {}).get("ltp")
                if px is not None:
                    raw = float(px)
            if raw is None:
                marks = _position_marks(market, pos)
                if marks:
                    raw = marks[0] if pos["opt_side"] == "CE" else marks[1]
            if token in ("", "hist"):
                expiry = date.fromisoformat(str(pos["expiry"])[:10])
                contract = market.option_contract(sym, expiry, float(pos["strike"]), pos["opt_side"])
                if contract:
                    token = contract["token"]
            if exit_due(pos, at):
                raw, fill_source = _official_raw(
                    market,
                    sym,
                    token,
                    datetime.combine(day, time(15, 0)),
                    exit_pin,
                    open_wait,
                    "15:00 snapshot",
                )
                if raw is None:
                    lines.append(f"  open {sym}: 15:00 open not published yet — exit held")
                    return lines
                msg = book.exit(day=day, raw_open=raw, clock="15:00", why="time")
                lines.append(f"  {msg} | {fill_source}")
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
            elif raw is None:
                lines.append(f"  open {sym}: {pos['opt_side']} mark unavailable")
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

    # --- entry window (14:05, or 15:05 base only) ---
    stamp_at = fill_at or time(14, 5)
    second = stamp_at == time(15, 5)
    window_open = second_entry_window(at) if second else entry_window(at)
    if window_open and not book.entered_on(day):
        if second:
            sig = pick_base_signal(candidates or [])
            if sig:
                sig = dict(sig)
                sig["day"] = day.isoformat()
        else:
            sig = pending or _load_pending()
            if not sig or sig.get("day") != day.isoformat():
                if at.time() >= time(14, 5):
                    cands = candidates if candidates is not None else collect_signals(market, scripts, day)
                    sig = pick_base_signal(cands)
                    if sig:
                        sig["day"] = day.isoformat()
        if not sig:
            lines.append("  15:05 window — no BASE signal" if second else "  entry window — no BASE signal")
            return lines
        t0 = time_mod.perf_counter()
        if (
            pinned_legs
            and pinned_legs.get("script") == sig["script"]
            and pinned_legs.get("call_token")
            and pinned_legs.get("put_token")
        ):
            legs = pinned_legs
        else:
            legs = _atm_legs(market, sig["script"], float(sig["spot"]), after=day)
        if not legs:
            lines.append(f"  entry failed — ATM legs missing for {sig['script']}")
            return lines
        opt = opt_side_for(sig["side"])
        got = _leg_for_side(legs, opt)
        if not got:
            lines.append(f"  entry failed — {opt} quote missing")
            return lines
        symbol, token, _stale = got
        pin = None
        if legs is pinned_legs and legs.get("stamped"):
            pin = {"stamped": True, "ltp": _stale}
        raw, fill_source = _official_raw(
            market,
            sig["script"],
            token,
            datetime.combine(day, stamp_at),
            pin,
            open_wait,
            f"{stamp_at.strftime('%H:%M')} snapshot",
        )
        if raw is None:
            lines.append(f"  entry held — {stamp_at.strftime('%H:%M')} open not published yet for {sig['script']}")
            return lines
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
            entry_clock=stamp_at.strftime("%H:%M"),
        )
        lines.append(f"  {msg} | {fill_source}")
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
