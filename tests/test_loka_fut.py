import json
from datetime import date, datetime

from src.desk import build_desk
from src.loka_fut import LokaFuturesBook, futures_charges, futures_points
from src.loka_fut_live import _run
from src.models import FutureContract


def test_futures_charges_match_the_first_sheet_trade():
    costs = futures_charges("Buy", 18330.3, 18475.4, 37 * 65, date(2021, 10, 14), date(2021, 10, 18))
    assert costs["stt"] == 4443.33
    assert costs["stamp"] == 881.69
    assert costs["exchange_sebi"] == 1708.39
    assert costs["gst"] == 314.71
    assert costs["brokerage"] == 40
    assert futures_points("Buy", 18330.3, 18475.4) == 143.1


def test_book_compounds_the_future_and_hides_the_token(tmp_path):
    book = LokaFuturesBook(tmp_path / "book.json")
    book.enter(
        side="Buy",
        day=date(2026, 11, 16),
        price=18330.3,
        tradingsymbol="NIFTY26NOVFUT",
        token="SECRET",
        expiry="2026-11-24",
        lot_size=65,
        signal="Loka",
        pcr=0.8,
        pcr_rank=40,
    )
    assert book.position["exit_clock"] == "09:15"
    assert book.position["lots"] == 37
    note = book.close(exit_on=date(2026, 11, 17), price=18475.4)
    trade = book.closed[0]
    assert trade["points"] == 143.1
    assert trade["stt"] == 22216.67
    assert trade["capital_after"] == round(trade["capital_before"] + trade["profit"] - trade["charges"], 2)
    assert "09:15" in note
    assert (tmp_path / "loka_oi_60_futures_journal.xlsx").exists()

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "loka_oi_60_futures_book.json").write_text(
        (tmp_path / "book.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    desk = build_desk(tmp_path, today=date(2026, 11, 17))
    assert "SECRET" not in json.dumps(desk)
    page = desk["loka_oi_60_futures"]
    assert page["saved"] is True
    assert page["flat"] is True
    assert page["rows"][1]["contract"] == "NIFTY26NOVFUT"
    assert page["rows"][1]["price"] == 18475.4


class _Market:
    def __init__(self):
        self.contract = FutureContract(
            symbol="NIFTY",
            tradingsymbol="NIFTY26NOVFUT",
            token="SECRET",
            expiry="2026-11-24",
            lot_size=65,
            trading_days_left=6,
            exchange="NFO",
        )

    def tradeable_future(self, symbol, min_trading_days=2, as_of=None):
        return self.contract

    def candles(self, exchange, token, start, end, interval="FIFTEEN_MINUTE"):
        day = end.date()
        return [
            {"stamp": datetime(day.year, day.month, day.day, 9, 15), "open": 25100.0, "close": 25110.0},
            {"stamp": datetime(day.year, day.month, day.day, 14, 0), "open": 25200.0, "close": 25210.0},
            {"stamp": datetime(day.year, day.month, day.day, 14, 45), "close": 25050.0, "open": 25040.0},
        ]

    def get_future_ltp(self, contract):
        return None


def test_live_step_buys_the_future_and_sells_it_at_the_0915_open(tmp_path, monkeypatch):
    import src.loka_fut_live as live

    monkeypatch.setattr(live, "_index_bars", lambda *args, **kwargs: [
        {"stamp": datetime(2026, 11, 13, 15, 15), "close": 24900.0},
        {"stamp": datetime(2026, 11, 16, 9, 15), "open": 24950.0, "close": 24960.0},
        {"stamp": datetime(2026, 11, 16, 14, 15), "close": 25000.0},
        {"stamp": datetime(2026, 11, 16, 14, 45), "close": 25080.0},
    ])
    monkeypatch.setattr(live, "_ratio", lambda *args, **kwargs: (0.9, 0.5))

    notes = _run(_Market(), LokaFuturesBook(tmp_path / "book.json"), datetime(2026, 11, 16, 15, 0))
    book = LokaFuturesBook(tmp_path / "book.json")
    assert book.position["side"] == "Buy"
    assert book.position["price"] == 25050.0
    assert book.position["exit_clock"] == "09:15"
    assert "SECRET" not in notes[0]

    notes = _run(_Market(), book, datetime(2026, 11, 17, 9, 30))
    closed = LokaFuturesBook(tmp_path / "book.json")
    assert closed.position is None
    assert closed.closed[0]["exit_price"] == 25100.0
    assert closed.closed[0]["exit_time"] == "09:15"
    assert "09:15" in notes[-1]
