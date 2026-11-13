"""Fill the Loka Kalyanam paper book from the live Nifty session. No orders."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.loka_kalyanam import (
    LokaBook,
    atm_strike,
    classify_close,
    entry_window,
    exit_due,
)
from src.universe import require_index


def run_loka_kalyanam(market, book: LokaBook, now: datetime) -> list[str]:
    """Exit a position whose clock has arrived, then open today's 15:00 pair."""
    notes: list[str] = []
    if book.is_open and exit_due(book.position, now):
        notes.append(_exit(market, book, now))
    if book.is_open or book.entered_on(now.date()) or not entry_window(now):
        return [note for note in notes if note]
    notes.append(_enter(market, book, now))
    return [note for note in notes if note]


def _enter(market, book: LokaBook, now: datetime) -> str:
    bars = _index_bars(market, now - timedelta(hours=8), now)
    day_open = _field(bars, now.date(), "09:15", "open")
    bar_1415 = _field(bars, now.date(), "14:15", "close")
    bar_1445 = _field(bars, now.date(), "14:45", "close")
    if day_open is None or bar_1415 is None or bar_1445 is None:
        return "Loka Kalyanam: today's 09:15, 14:15 and 14:45 bars are not all in yet."
    side = classify_close(day_open, bar_1415, bar_1445)
    if side is None:
        return (
            f"Loka Kalyanam: mixed close, no trade. "
            f"Nifty 09:15 {day_open:,.2f}, 14:15 {bar_1415:,.2f}, 15:00 {bar_1445:,.2f}"
        )
    expiry = _hold_expiry(market, now.date())
    if expiry is None:
        return "Loka Kalyanam: no later Nifty expiry is listed."
    strike = atm_strike(bar_1445)
    call = market.option_contract("NIFTY", expiry, strike, "CE")
    put = market.option_contract("NIFTY", expiry, strike, "PE")
    if not call or not put:
        return f"Loka Kalyanam: {strike} call and put are not both listed for {expiry:%d-%m-%Y}."
    call_px = _option_fill(market, call, now.date(), "14:45", "close", now)
    put_px = _option_fill(market, put, now.date(), "14:45", "close", now)
    if call_px is None or put_px is None:
        return "Loka Kalyanam: an option price is missing, so nothing was filled."
    return book.enter(
        side=side,
        day=now.date(),
        nifty=bar_1445,
        call_symbol=call["tradingsymbol"],
        call_token=call["token"],
        call_entry=call_px,
        put_symbol=put["tradingsymbol"],
        put_token=put["token"],
        put_entry=put_px,
        strike=strike,
        expiry=expiry.isoformat(),
        lot_size=int(call["lot_size"] or put["lot_size"] or 0),
    )


def _exit(market, book: LokaBook, now: datetime) -> str:
    pos = book.position
    clock = str(pos["exit_clock"])
    bars = _index_bars(market, now - timedelta(hours=8), now)
    nifty = _field(bars, now.date(), clock, "open")
    if nifty is None:
        spot = _index_ltp(market)
        nifty = spot
    if nifty is None:
        return "Loka Kalyanam: Nifty exit price is missing. The position stays open."
    call = {
        "token": pos["call_token"],
        "tradingsymbol": pos.get("call_symbol") or "",
    }
    put = {
        "token": pos["put_token"],
        "tradingsymbol": pos.get("put_symbol") or "",
    }
    call_px = _option_fill(market, call, now.date(), clock, "open", now)
    put_px = _option_fill(market, put, now.date(), clock, "open", now)
    if call_px is None or put_px is None:
        return "Loka Kalyanam: an exit price is missing. The position stays open."
    return book.close(
        exit_on=now.date(),
        nifty_exit=nifty,
        call_exit=call_px,
        put_exit=put_px,
        quote="Market",
    )


def _hold_expiry(market, day: date) -> date | None:
    """Nearest expiry after the entry day, so an expiry-day entry is not the dying week."""
    expiries = [item for item in market.option_expiries("NIFTY", day) if item > day]
    return min(expiries) if expiries else None


def _index_bars(market, start: datetime, end: datetime) -> list[dict]:
    spec = require_index("NIFTY")
    market._load_instruments()
    token = (market._index_tokens or {}).get("NIFTY")
    if not token:
        return []
    return market.candles(spec.index_exchange, token, start, end)


def _index_ltp(market) -> float | None:
    try:
        prices = market.get_ltps(["NIFTY"])
    except Exception as exc:
        print(f"  Loka Kalyanam: Nifty quote failed ({exc})")
        return None
    price = prices.get("NIFTY")
    return None if price is None else float(price)


def _option_fill(market, contract: dict, day: date, hhmm: str, field: str, now: datetime) -> float | None:
    start = datetime.combine(day, datetime.min.time())
    bars = market.candles("NFO", contract["token"], start, now)
    price = _field(bars, day, hhmm, field)
    if price is not None and price >= 0:
        return price
    try:
        quotes = market._fetch_option_quotes("NFO", [contract["token"]])
    except Exception as exc:
        print(f"  Loka Kalyanam: {contract.get('tradingsymbol') or contract['token']} quote failed ({exc})")
        return None
    row = quotes.get(str(contract["token"])) or {}
    ltp = row.get("ltp")
    if ltp is None:
        return None
    return float(ltp)


def _field(bars: list[dict], day: date, hhmm: str, field: str) -> float | None:
    hour, minute = hhmm.split(":")
    for bar in bars:
        stamp = bar.get("stamp")
        if stamp is None or stamp.date() != day:
            continue
        if stamp.hour == int(hour) and stamp.minute == int(minute):
            value = bar.get(field)
            if value is None:
                return None
            return float(value)
    return None
