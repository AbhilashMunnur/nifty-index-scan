"""Paper the two open-interest books from the live Nifty session. No orders."""

from __future__ import annotations

import traceback
from datetime import date, datetime, time, timedelta

from src.loka_kalyanam import (
    LokaBook,
    atm_strike,
    classify_close,
    entry_window,
    pair_points,
)
from src.loka_live import _field, _hold_expiry, _index_bars, _index_ltp
from src.loka_oi import (
    MIN_PAIRS,
    VARIANTS,
    OiVariant,
    choose_side,
    exit_step,
    load_pcr,
    pcr_percentile,
    remember_pcr,
    series_ending,
)


def run_loka_oi(market, now: datetime) -> list[str]:
    books = [
        (
            variant,
            LokaBook(variant.book_path, name=variant.name, journal_name=variant.journal_name),
        )
        for variant in VARIANTS
    ]
    context = _context(market, now, books)
    notes: list[str] = []
    for variant, book in books:
        try:
            notes.extend(_run_one(market, book, now, variant, context))
        except Exception:
            traceback.print_exc()
            notes.append(f"{variant.name} paper step failed. The other books still stand.")
    return [note for note in notes if note]


def _run_one(market, book: LokaBook, now: datetime, variant: OiVariant, context: dict) -> list[str]:
    notes: list[str] = []
    if book.is_open and _later_session(book.position, now):
        notes.append(_exit(market, book, now, variant))
    if book.is_open or book.entered_on(now.date()) or not entry_window(now):
        return [note for note in notes if note]
    notes.append(_enter(market, book, now, variant, context))
    return [note for note in notes if note]


def _enter(market, book: LokaBook, now: datetime, variant: OiVariant, context: dict) -> str:
    bars = context["bars"]
    day_open = _field(bars, now.date(), "09:15", "open")
    bar_1415 = _field(bars, now.date(), "14:15", "close")
    bar_1445 = _field(bars, now.date(), "14:45", "close")
    if day_open is None or bar_1415 is None or bar_1445 is None:
        return f"{variant.name}: today's 09:15, 14:15 and 14:45 bars are not all in yet."
    loka_side = classify_close(day_open, bar_1415, bar_1445)
    prev_close = _prev_close(bars, now.date())
    pcr = context.get("pcr")
    percentile = None
    if pcr is not None:
        percentile = pcr_percentile(series_ending(load_pcr(), now.date(), pcr), variant.window)
    side, signal = choose_side(loka_side, bar_1445, prev_close, percentile, variant.cutoff)
    if side is None:
        if signal == "OI day":
            return (
                f"{variant.name}: open-interest day, Nifty is flat against yesterday, no trade. "
                f"Nifty {bar_1445:,.2f}"
            )
        return (
            f"{variant.name}: mixed close, no trade. "
            f"Nifty 09:15 {day_open:,.2f}, 14:15 {bar_1415:,.2f}, 15:00 {bar_1445:,.2f}"
        )
    expiry = _hold_expiry(market, now.date())
    if expiry is None:
        return f"{variant.name}: no later Nifty expiry is listed."
    strike = atm_strike(bar_1445)
    call = market.option_contract("NIFTY", expiry, strike, "CE")
    put = market.option_contract("NIFTY", expiry, strike, "PE")
    if not call or not put:
        return f"{variant.name}: {strike} call and put are not both listed for {expiry:%d-%m-%Y}."
    call_px = _candle_or_ltp(market, call, now.date(), "14:45", now)
    put_px = _candle_or_ltp(market, put, now.date(), "14:45", now)
    if call_px is None or put_px is None:
        return f"{variant.name}: an option price is missing, so nothing was filled."
    rank = None if percentile is None else round(percentile * 100, 1)
    missing = ""
    if pcr is None:
        missing = " Put-call ratio was missing, so the Loka close was used."
    note = book.enter(
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
        signal=signal,
        pcr=None if pcr is None else round(float(pcr), 3),
        pcr_rank=rank,
        check_clock=variant.check_clock,
        fallback_clock=variant.fallback_clock,
    )
    return note + missing


def _exit(market, book: LokaBook, now: datetime, variant: OiVariant) -> str:
    pos = book.position or {}
    check = _clock(str(pos.get("check_clock") or variant.check_clock))
    fallback = _clock(str(pos.get("fallback_clock") or variant.fallback_clock))
    points = None
    prices = None
    if not pos.get("checked"):
        prices = _pair_at(market, pos, now, _hhmm(check))
        if prices is not None:
            points = pair_points(str(pos["side"]), float(pos["call_entry"]), prices[0], float(pos["put_entry"]), prices[1])
    step = exit_step(points, bool(pos.get("checked")), now.time().replace(second=0, microsecond=0), check, fallback)
    if step == "wait":
        return ""
    if step == "mark":
        book.mark_checked()
        return (
            f"{variant.name}: {_hhmm(check)} is not in profit ({points:+.2f} pts). "
            f"Holding to {_hhmm(fallback)}."
        )
    if step == "close_check" and prices is not None:
        return _close(market, book, now, prices, _hhmm(check), f"{_hhmm(check)} in profit")
    fallback_prices = _pair_at(market, pos, now, _hhmm(fallback))
    if fallback_prices is None:
        fallback_prices = _pair_ltp(market, pos)
    if fallback_prices is None:
        return f"{variant.name}: the 14:00 prices are missing. The position stays open."
    if points is None and not pos.get("checked"):
        reason = f"{_hhmm(fallback)} ({_hhmm(check)} bar had no price)"
    else:
        reason = f"{_hhmm(fallback)} (not in profit at {_hhmm(check)})"
    return _close(market, book, now, fallback_prices, _hhmm(fallback), reason)


def _close(market, book: LokaBook, now: datetime, prices: tuple[float, float], clock: str, reason: str) -> str:
    pos = book.position or {}
    nifty = _field(_index_bars(market, now - timedelta(days=10), now), now.date(), clock, "open")
    if nifty is None:
        nifty = _index_ltp(market)
    if nifty is None:
        return f"{book.name}: Nifty exit price is missing. The position stays open."
    call_px, put_px = prices
    if str(pos.get("side")) not in ("Buy", "Short"):
        return f"{book.name}: the open side is missing. The position stays open."
    return book.close(
        exit_on=now.date(),
        nifty_exit=nifty,
        call_exit=call_px,
        put_exit=put_px,
        quote="Market",
        exit_clock_used=clock,
        reason=reason,
    )


def _context(market, now: datetime, books: list[tuple[OiVariant, LokaBook]]) -> dict:
    needs_session = any(
        book.is_open or (entry_window(now) and not book.entered_on(now.date()))
        for _, book in books
    )
    if not needs_session:
        return {"bars": [], "pcr": None}
    bars = _index_bars(market, now - timedelta(days=10), now)
    pcr = None
    wants_entry = entry_window(now) and any(
        not book.is_open and not book.entered_on(now.date()) for _, book in books
    )
    if wants_entry:
        spot = _field(bars, now.date(), "14:45", "close")
        if spot is not None:
            pcr = band_pcr(market, spot, now.date())
            if pcr is not None:
                remember_pcr(now.date(), pcr)
    return {"bars": bars, "pcr": pcr}


def band_pcr(market, spot: float, day: date) -> float | None:
    """Put open interest ÷ call open interest, ATM±250, nearest expiry including expiry day."""
    expiries = [item for item in market.option_expiries("NIFTY", day) if item >= day]
    if not expiries:
        return None
    expiry = min(expiries)
    atm = atm_strike(spot)
    pairs: list[tuple[str, str]] = []
    for offset in range(-5, 6):
        strike = atm + offset * 50
        call = market.option_contract("NIFTY", expiry, strike, "CE")
        put = market.option_contract("NIFTY", expiry, strike, "PE")
        if call and put:
            pairs.append((str(call["token"]), str(put["token"])))
    if len(pairs) < MIN_PAIRS:
        return None
    try:
        quotes = market._fetch_option_quotes("NFO", [token for pair in pairs for token in pair])
    except Exception as exc:
        print(f"  Loka OI: open interest failed ({exc})")
        return None
    call_oi = put_oi = got = 0
    for call_token, put_token in pairs:
        call = quotes.get(call_token)
        put = quotes.get(put_token)
        if not call or not put:
            continue
        call_oi += int(call.get("oi") or 0)
        put_oi += int(put.get("oi") or 0)
        got += 1
    if got < MIN_PAIRS or call_oi <= 0:
        return None
    return put_oi / call_oi


def _pair_at(market, pos: dict, now: datetime, clock: str) -> tuple[float, float] | None:
    call = _candle_open(market, pos.get("call_token"), now, clock)
    put = _candle_open(market, pos.get("put_token"), now, clock)
    if call is None or put is None:
        return None
    return call, put


def _pair_ltp(market, pos: dict) -> tuple[float, float] | None:
    call = {"token": pos.get("call_token"), "tradingsymbol": pos.get("call_symbol") or ""}
    put = {"token": pos.get("put_token"), "tradingsymbol": pos.get("put_symbol") or ""}
    call_px = _ltp(market, call)
    put_px = _ltp(market, put)
    if call_px is None or put_px is None:
        return None
    return call_px, put_px


def _candle_open(market, token, now: datetime, clock: str) -> float | None:
    if not token:
        return None
    start = datetime.combine(now.date(), datetime.min.time())
    bars = market.candles("NFO", str(token), start, now)
    return _field(bars, now.date(), clock, "open")


def _candle_or_ltp(market, contract: dict, day: date, clock: str, now: datetime) -> float | None:
    price = _candle_open(market, contract.get("token"), now, clock)
    if price is not None and price >= 0:
        return price
    return _ltp(market, contract)


def _ltp(market, contract: dict) -> float | None:
    token = str(contract.get("token") or "")
    if not token:
        return None
    try:
        quotes = market._fetch_option_quotes("NFO", [token])
    except Exception as exc:
        print(f"  Loka OI: {contract.get('tradingsymbol') or token} quote failed ({exc})")
        return None
    row = quotes.get(token) or {}
    ltp = row.get("ltp")
    if ltp is None:
        return None
    return float(ltp)


def _prev_close(bars: list[dict], today: date) -> float | None:
    last = None
    for bar in bars:
        stamp = bar.get("stamp")
        if stamp is None or stamp.date() >= today:
            continue
        close = bar.get("close")
        if close is not None:
            last = float(close)
    return last


def _later_session(position: dict, now: datetime) -> bool:
    entry_on = date.fromisoformat(str(position["entry_date"]))
    return now.date() > entry_on


def _clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"
