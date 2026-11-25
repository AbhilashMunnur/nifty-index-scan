"""Master Strategy live paper tick — Angel One quotes, 5m IST clock.

Latency model (matches backtest honesty as closely as live allows):
  - 14:00–14:04: after 14:00 bar is closed, compute PCR rank + tier/side (signal).
  - 14:05: enter at the five-minute option OPEN (± slip), not a later LTP.
  - Intrabar (filler only): check TP1 partial on LTP marks.
  - Next session 15:00: exit remaining at the five-minute option OPEN (± slip).

Live broker orders stay off (PaperBroker). Paper reads Nifty, BankNifty, and Sensex.
"""

from __future__ import annotations

import json
import time as time_mod
import traceback
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from src.broker_adapter import OrderIntent, PaperBroker, get_broker
from src.clock_fill import choose_price, fill_wait, poll_pair_open
from src.master_book import MasterBook
from src.master_strategy import (
    ENTRY_CLOCK,
    EXIT_CLOCK,
    NAME,
    PCR_PATH,
    PRIORITY,
    SIGNAL_CLOCK,
    WINGS,
    atm_strike,
    classify_tier,
    entry_window,
    exit_due,
    load_pcr_history,
    pick_signal,
    remember_pcr,
    save_pcr_history,
    synth_entry_fills,
    synth_exit_fills,
)
from src.paths import ROOT
from src.universe import ALLOWED_SYMBOLS, require_index

IST = ZoneInfo("Asia/Kolkata")
PENDING_PATH = ROOT / "data" / "master_pending_signal.json"
LATENCY_LOG = ROOT / "data" / "master_latency.jsonl"

# Live scripts currently supported by Angel instrument loader
LIVE_SCRIPTS = tuple(s for s in PRIORITY if s in ALLOWED_SYMBOLS)
# Latest rank read, including scripts that did not qualify. The desk stores this.
LAST_READINGS: list[dict] = []


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _log_latency(event: str, started: float, **extra) -> None:
    ms = (time_mod.perf_counter() - started) * 1000.0
    row = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": event, "latency_ms": round(ms, 1), **extra}
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


def _atm_legs(market, symbol: str, spot: float, *, after: date | None = None) -> dict | None:
    """ATM CE/PE for a new entry. `after` skips an expiry that dies the same day."""
    band = market.get_atm_band_pcr(symbol, spot, wings=WINGS, after=after)
    if not band:
        return None
    atm = next((s for s in band.strikes if s.label == "ATM"), None)
    if not atm or not atm.call_token or not atm.put_token:
        return None
    if atm.call_ltp is None or atm.put_ltp is None:
        return None
    # tradingsymbols from instrument dump
    market._load_instruments()
    rows = (market._option_rows or {}).get(symbol) or []
    call_sym = put_sym = ""
    for row in rows:
        tok = str(row.get("token") or "")
        if tok == atm.call_token:
            call_sym = str(row.get("symbol") or row.get("name") or "")
        if tok == atm.put_token:
            put_sym = str(row.get("symbol") or row.get("name") or "")
    return {
        "expiry": band.expiry,
        "lot_size": int(band.lot_size),
        "strike": int(atm.strike),
        "call_token": atm.call_token,
        "put_token": atm.put_token,
        "call_symbol": call_sym,
        "put_symbol": put_sym,
        "call_ltp": float(atm.call_ltp),
        "put_ltp": float(atm.put_ltp),
        "pcr": float(band.pcr) if getattr(band, "pcr", None) is not None else _band_pcr(band),
    }


def _band_pcr(band) -> float | None:
    ce = sum(int(s.call_oi or 0) for s in band.strikes)
    pe = sum(int(s.put_oi or 0) for s in band.strikes)
    if ce <= 0:
        return None
    return pe / ce


def _position_marks(market, pos: dict) -> tuple[float, float] | None:
    """LTP of the entered call and put, not whatever strike is ATM now."""
    spec = require_index(pos["script"])
    call_token = str(pos.get("call_token") or "")
    put_token = str(pos.get("put_token") or "")
    if call_token in ("", "hist") or put_token in ("", "hist"):
        expiry = date.fromisoformat(str(pos["expiry"])[:10])
        strike = float(pos["strike"])
        call = market.option_contract(pos["script"], expiry, strike, "CE")
        put = market.option_contract(pos["script"], expiry, strike, "PE")
        if not call or not put:
            return None
        call_token, put_token = call["token"], put["token"]
    quotes = market._fetch_option_quotes(spec.fno_exchange, [call_token, put_token])
    call_px = (quotes.get(call_token) or {}).get("ltp")
    put_px = (quotes.get(put_token) or {}).get("ltp")
    if call_px is None or put_px is None:
        return None
    return float(call_px), float(put_px)


def _emit_orders(broker, side: str, legs: dict, qty: int, note: str) -> None:
    """Two-leg synthetic: Buy side → buy CE sell PE; Sell side → sell CE buy PE."""
    if side == "Buy":
        intents = [
            OrderIntent("Buy", legs["call_symbol"], legs["call_token"], qty, legs["call_ltp"], note=note),
            OrderIntent("Sell", legs["put_symbol"], legs["put_token"], qty, legs["put_ltp"], note=note),
        ]
    else:
        intents = [
            OrderIntent("Sell", legs["call_symbol"], legs["call_token"], qty, legs["call_ltp"], note=note),
            OrderIntent("Buy", legs["put_symbol"], legs["put_token"], qty, legs["put_ltp"], note=note),
        ]
    for intent in intents:
        broker.place(intent)


def collect_signals(market, scripts: tuple[str, ...], day: date) -> list[dict]:
    t0 = time_mod.perf_counter()
    prices = market.get_ltps(list(scripts))
    hist = load_pcr_history()
    cands = []
    readings: list[dict] = []
    for sym in scripts:
        spot = prices.get(sym)
        if not spot:
            print(f"  {sym}: no LTP")
            continue
        band = market.get_atm_band_pcr(sym, float(spot), wings=WINGS)
        if not band:
            continue
        pcr = _band_pcr(band)
        if pcr is None:
            print(f"  {sym}: PCR unavailable")
            continue
        rank = remember_pcr(hist, sym, day, pcr)
        if rank is None:
            print(f"  {sym}: PCR rank warming up (need history)")
            continue
        tier, side = classify_tier(sym, rank)
        print(f"  {sym}: spot={spot:.2f} PCR={pcr:.3f} rank={rank:.3f} → {tier}/{side}")
        readings.append(
            {
                "script": sym,
                "spot": round(float(spot), 2),
                "pcr": round(float(pcr), 4),
                "rank": round(float(rank), 4),
                "tier": tier or "",
                "side": side or "",
                "expiry": str(getattr(band, "expiry", "") or ""),
            }
        )
        if tier and side:
            cands.append(
                dict(
                    script=sym,
                    tier=tier,
                    side=side,
                    rank=rank,
                    pcr=pcr,
                    spot=float(spot),
                    day=day.isoformat(),
                    signal_clock=SIGNAL_CLOCK,
                )
            )
    save_pcr_history(hist)
    global LAST_READINGS
    LAST_READINGS = readings
    _log_latency("collect_signals", t0, n=len(cands))
    return cands


def _pinned_open(
    market,
    script: str,
    call_token: str,
    put_token: str,
    stamp: datetime,
    pin: dict | None,
    wait_seconds: float,
    snapshot_label: str,
) -> tuple[float | None, float | None, str]:
    """14:05 / 15:00 prices. An on-the-clock snapshot is booked immediately.

    A late job waits for the five-minute open and never uses a fresh LTP.
    """
    spec = require_index(script)
    armed = bool(pin and pin.get("stamped"))
    if armed:
        call_candle = put_candle = None
    else:
        call_candle, put_candle = poll_pair_open(
            market,
            spec.fno_exchange,
            call_token,
            put_token,
            stamp,
            fill_wait(wait_seconds, armed),
        )
    call_snap = float(pin["call_ltp"]) if armed else None
    put_snap = float(pin["put_ltp"]) if armed else None
    call_px, call_src = choose_price(
        call_candle, call_snap, armed=armed, snapshot_label=snapshot_label
    )
    put_px, put_src = choose_price(
        put_candle, put_snap, armed=armed, snapshot_label=snapshot_label
    )
    if call_px is None or put_px is None or call_src != put_src:
        return None, None, "missing"
    return call_px, put_px, call_src


def run_master_tick(
    market,
    book: MasterBook | None = None,
    broker=None,
    at: datetime | None = None,
    scripts: tuple[str, ...] = LIVE_SCRIPTS,
    candidates: list[dict] | None = None,
    pinned_legs: dict | None = None,
    exit_pin: dict | None = None,
    open_wait: float = 25,
) -> list[str]:
    """One production tick. Safe to call every scan slot (~1–5 min).

    Pass candidates=... when the scan loop already collected PCR signals
    (shared with option-buy desk).
    """
    book = book or MasterBook()
    broker = broker or get_broker("paper")
    at = at or now_ist()
    day = at.date()
    lines: list[str] = []
    clock = at.strftime("%H:%M")
    lines.append(f"{NAME} tick {at:%d-%b %H:%M} IST | broker={getattr(broker, 'name', '?')}")

    # --- manage open position first ---
    if book.is_open:
        pos = book.position
        sym = pos["script"]
        try:
            fill_source = "ltp"
            if exit_due(pos, at):
                call_token = str(pos.get("call_token") or "")
                put_token = str(pos.get("put_token") or "")
                if call_token in ("", "hist") or put_token in ("", "hist"):
                    expiry = date.fromisoformat(str(pos["expiry"])[:10])
                    call = market.option_contract(sym, expiry, float(pos["strike"]), "CE")
                    put = market.option_contract(sym, expiry, float(pos["strike"]), "PE")
                    if not call or not put:
                        lines.append(f"  open {sym}: 15:00 contracts missing — exit held")
                        return lines
                    call_token, put_token = call["token"], put["token"]
                call_px, put_px, fill_source = _pinned_open(
                    market,
                    sym,
                    call_token,
                    put_token,
                    datetime.combine(day, time(15, 0)),
                    exit_pin,
                    open_wait,
                    "15:00 snapshot",
                )
                if call_px is None or put_px is None:
                    lines.append(f"  open {sym}: 15:00 open not published yet — exit held")
                    return lines
            else:
                marks = _position_marks(market, pos)
                if marks is None:
                    call_px = put_px = None
                else:
                    call_px, put_px = marks
            if call_px is None or put_px is None:
                lines.append(f"  open {sym}: option marks unavailable")
            else:
                # filler partial
                if pos.get("tier") == "filler" and not pos.get("partial_done"):
                    msg = book.partial_book(day=day, call_px=call_px, put_px=put_px)
                    lines.append(f"  {msg}")
                    if "PARTIAL" in msg and book.position:
                        qty_slice = int(round(book.position["qty"] * 0.5))
                        # closing orders for booked slice (opposite of entry)
                        side = pos["side"]
                        close_side = "Sell" if side == "Buy" else "Buy"
                        # reuse opening_orders inverted via exit fills economically; order intents:
                        if side == "Buy":
                            broker.place(
                                OrderIntent("Sell", pos["call_symbol"], pos["call_token"], qty_slice, call_px, note="partial")
                            )
                            broker.place(
                                OrderIntent("Buy", pos["put_symbol"], pos["put_token"], qty_slice, put_px, note="partial")
                            )
                        else:
                            broker.place(
                                OrderIntent("Buy", pos["call_symbol"], pos["call_token"], qty_slice, call_px, note="partial")
                            )
                            broker.place(
                                OrderIntent("Sell", pos["put_symbol"], pos["put_token"], qty_slice, put_px, note="partial")
                            )
                if exit_due(pos, at):
                    ce_x, pe_x = synth_exit_fills(pos["side"], call_px, put_px)
                    msg = book.exit(day=day, call_px=ce_x, put_px=pe_x, clock="15:00", why="time")
                    lines.append(f"  {msg} | {fill_source}")
                    rem_qty = int(round(pos["qty"] * float(pos.get("remaining_frac") or 0)))
                    if rem_qty > 0:
                        if pos["side"] == "Buy":
                            broker.place(OrderIntent("Sell", pos["call_symbol"], pos["call_token"], rem_qty, call_px, note="exit"))
                            broker.place(OrderIntent("Buy", pos["put_symbol"], pos["put_token"], rem_qty, put_px, note="exit"))
                        else:
                            broker.place(OrderIntent("Buy", pos["call_symbol"], pos["call_token"], rem_qty, call_px, note="exit"))
                            broker.place(OrderIntent("Sell", pos["put_symbol"], pos["put_token"], rem_qty, put_px, note="exit"))
                    _save_pending(None)
        except Exception:
            traceback.print_exc()
            lines.append("  manage open failed — see traceback")
        return lines

    # --- signal capture after 14:00 bar (14:00–14:04) ---
    pending = _load_pending()
    if pending and pending.get("day") != day.isoformat():
        pending = None
        _save_pending(None)

    if pending is None and time(14, 0) <= at.time() < time(14, 5):
        cands = candidates if candidates is not None else collect_signals(market, scripts, day)
        chosen = pick_signal(cands)
        if chosen:
            _save_pending(chosen)
            lines.append(
                f"  SIGNAL {chosen['tier']} {chosen['side']} {chosen['script']} "
                f"rank={chosen['rank']:.3f} — wait {ENTRY_CLOCK} entry"
            )
        else:
            lines.append("  no Master signal today")
        return lines

    # --- entry window ---
    if entry_window(at) and not book.entered_on(day):
        sig = pending or _load_pending()
        if not sig or sig.get("day") != day.isoformat():
            # Late join: compute signal now (still uses live PCR; prefer after 14:00)
            if at.time() >= time(14, 5):
                cands = candidates if candidates is not None else collect_signals(market, scripts, day)
                sig = pick_signal(cands)
                if sig:
                    sig["day"] = day.isoformat()
        if not sig:
            lines.append("  entry window — no signal")
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
        ce_raw, pe_raw, fill_source = _pinned_open(
            market,
            sig["script"],
            str(legs["call_token"]),
            str(legs["put_token"]),
            datetime.combine(day, time(14, 5)),
            legs if legs.get("stamped") else None,
            open_wait,
            "14:05 snapshot",
        )
        if ce_raw is None or pe_raw is None:
            lines.append(
                f"  entry held — 14:05 open not published yet for {sig['script']}"
            )
            return lines
        legs = dict(legs)
        legs["call_ltp"] = ce_raw
        legs["put_ltp"] = pe_raw
        ce_e, pe_e = synth_entry_fills(sig["side"], ce_raw, pe_raw)
        msg = book.enter(
            script=sig["script"],
            tier=sig["tier"],
            side=sig["side"],
            day=day,
            spot=float(sig["spot"]),
            pcr=float(sig["pcr"]),
            pcr_rank=float(sig["rank"]),
            call_symbol=legs["call_symbol"],
            call_token=legs["call_token"],
            call_entry=ce_e,
            put_symbol=legs["put_symbol"],
            put_token=legs["put_token"],
            put_entry=pe_e,
            strike=int(legs["strike"]),
            expiry=str(legs["expiry"]),
            lot_size=int(legs["lot_size"]),
        )
        lines.append(f"  {msg} | {fill_source}")
        if book.is_open:
            _emit_orders(broker, sig["side"], legs, book.position["qty"], note="entry")
            _save_pending(None)
        _log_latency("entry", t0, script=sig["script"], tier=sig["tier"])
        return lines

    lines.append(f"  idle @ {clock} (signal {SIGNAL_CLOCK}, entry {ENTRY_CLOCK}, exit {EXIT_CLOCK})")
    return lines


def run_master_once(market=None) -> list[str]:
    from src.market import IndexMarket

    own = market is None
    market = market or IndexMarket()
    try:
        return run_master_tick(market, MasterBook(), PaperBroker())
    finally:
        if own:
            market.close()
