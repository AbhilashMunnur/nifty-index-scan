"""Fill the Loka OI 60 futures paper book from the live session. No orders."""

from __future__ import annotations

import traceback
from datetime import date, datetime, timedelta

from src.loka_fut import LokaFuturesBook, manage_exit
from src.loka_kalyanam import classify_close, entry_window
from src.loka_live import _field, _index_bars
from src.loka_oi import choose_side, load_pcr, pcr_percentile, remember_pcr, series_ending
from src.loka_oi import VARIANTS
from src.loka_oi_live import _prev_close, band_pcr
from src.models import FutureContract

OI_60 = next(variant for variant in VARIANTS if variant.key == "loka_oi_60")


def run_loka_oi_futures(market, now: datetime) -> list[str]:
    book = LokaFuturesBook()
    try:
        return [note for note in _run(market, book, now) if note]
    except Exception:
        traceback.print_exc()
        return [f"{book.name} paper step failed. The other books still stand."]


def _run(market, book: LokaFuturesBook, now: datetime) -> list[str]:
    notes: list[str] = []
    if book.is_open:
        notes.append(_manage(market, book, now))
    if book.is_open or book.entered_on(now.date()) or not entry_window(now):
        return notes
    notes.append(_enter(market, book, now))
    return notes


def _enter(market, book: LokaFuturesBook, now: datetime) -> str:
    bars = _index_bars(market, now - timedelta(days=10), now)
    day_open = _field(bars, now.date(), "09:15", "open")
    bar_1415 = _field(bars, now.date(), "14:15", "close")
    bar_1445 = _field(bars, now.date(), "14:45", "close")
    if day_open is None or bar_1415 is None or bar_1445 is None:
        return f"{book.name}: today's 09:15, 14:15 and 14:45 bars are not all in yet."
    loka_side = classify_close(day_open, bar_1415, bar_1445)
    pcr, percentile = _ratio(market, now, bar_1445)
    side, signal = choose_side(loka_side, bar_1445, _prev_close(bars, now.date()), percentile, OI_60.cutoff)
    if side is None:
        if signal == "OI day":
            return f"{book.name}: open-interest day, Nifty is flat against yesterday, no trade. Nifty {bar_1445:,.2f}"
        return (
            f"{book.name}: mixed close, no trade. "
            f"Nifty 09:15 {day_open:,.2f}, 14:15 {bar_1415:,.2f}, 15:00 {bar_1445:,.2f}"
        )
    contract = market.tradeable_future("NIFTY", min_trading_days=2, as_of=now.date())
    if contract is None:
        return f"{book.name}: no Nifty future has two sessions left."
    price = _future_price(market, contract, now, "14:45", "close")
    if price is None:
        price = market.get_future_ltp(contract)
    if price is None:
        return f"{book.name}: the future price is missing, so nothing was filled."
    rank = None if percentile is None else round(percentile * 100, 1)
    missing = ""
    if pcr is None:
        missing = " Put-call ratio was missing, so the Loka close was used."
    note = book.enter(
        side=side,
        day=now.date(),
        price=price,
        tradingsymbol=contract.tradingsymbol,
        token=contract.token,
        expiry=contract.expiry,
        lot_size=int(contract.lot_size),
        signal=signal,
        pcr=None if pcr is None else round(float(pcr), 3),
        pcr_rank=rank,
    )
    return note + missing


def _manage(market, book: LokaFuturesBook, now: datetime) -> str:
    pos = book.position or {}
    entry_day = date.fromisoformat(str(pos.get("entry_date")))
    contract = FutureContract(
        symbol="NIFTY",
        tradingsymbol=str(pos.get("tradingsymbol") or ""),
        token=str(pos.get("token") or ""),
        expiry=str(pos.get("expiry") or ""),
        lot_size=int(pos.get("lot_size") or 0),
        trading_days_left=0,
        exchange="NFO",
    )
    if not contract.token:
        return f"{book.name}: the future is missing, so the position stays open."
    start = datetime.combine(entry_day, datetime.min.time())
    try:
        bars = market.candles(contract.exchange, contract.token, start, now)
    except Exception as exc:
        return f"{book.name}: the future candles are missing ({exc}). The position stays open."
    decision = manage_exit(str(pos.get("side")), float(pos.get("price")), bars or [], now, entry_day)
    if decision is None:
        return ""
    price, clock, reason, exit_day = decision
    return book.close(exit_on=exit_day, price=price, exit_time=clock, reason=reason)


def _ratio(market, now: datetime, spot: float) -> tuple[float | None, float | None]:
    rows = load_pcr()
    stored = next((float(row["pcr"]) for row in rows if str(row.get("date")) == now.date().isoformat()), None)
    pcr = stored if stored is not None else band_pcr(market, spot, now.date())
    if pcr is None:
        return None, None
    if stored is None:
        remember_pcr(now.date(), pcr)
    percentile = pcr_percentile(series_ending(load_pcr(), now.date(), pcr), OI_60.window)
    return pcr, percentile


def _future_price(market, contract: FutureContract, now: datetime, clock: str, field: str) -> float | None:
    if not contract.token:
        return None
    start = datetime.combine(now.date(), datetime.min.time())
    bars = market.candles(contract.exchange, contract.token, start, now)
    return _field(bars, now.date(), clock, field)
