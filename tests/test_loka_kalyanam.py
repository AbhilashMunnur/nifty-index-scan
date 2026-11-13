import json
from datetime import date, datetime

from src.desk import build_desk
from src.loka_kalyanam import (
    LokaBook,
    atm_strike,
    classify_close,
    entry_window,
    exit_due,
    pair_points,
)


def test_strong_close_buys_and_weak_close_sells():
    assert classify_close(100, 101, 102) == "Buy"
    assert classify_close(100, 99, 98) == "Short"
    assert classify_close(100, 102, 101) is None
    assert atm_strike(18330.30) == 18350


def test_first_sheet_trade_points_and_capital(tmp_path):
    book = LokaBook(tmp_path / "book.json")
    book.enter(
        side="Buy",
        day=date(2021, 10, 14),
        nifty=18330.30,
        call_symbol="NIFTY14OCT2118350CE",
        call_token="111",
        call_entry=110.8,
        put_symbol="NIFTY14OCT2118350PE",
        put_token="222",
        put_entry=120.7,
        strike=18350,
        expiry="2021-10-21",
        lot_size=65,
    )
    assert book.position["lots"] == 37
    assert book.position["call_action"] == "Buy"
    assert book.position["put_action"] == "Sell"
    assert book.position["exit_clock"] == "09:15"
    note = book.close(
        exit_on=date(2021, 10, 18),
        nifty_exit=18475.40,
        call_exit=125.4,
        put_exit=0,
        quote="Intrinsic",
    )
    assert pair_points("Buy", 110.8, 125.4, 120.7, 0) == 131.3
    trade = book.closed[0]
    assert trade["points"] == 131.3
    assert trade["call_exit_action"] == "Sell"
    assert trade["put_exit_action"] == "Buy"
    assert trade["capital_after"] == round(trade["capital_before"] + trade["profit"] - trade["charges"], 2)
    assert book.capital == trade["capital_after"]
    assert book.realised_pnl == round(book.capital - book.starting_capital, 2)
    assert "131.30" in note
    assert "111" in (tmp_path / "book.json").read_text(encoding="utf-8")


def test_journal_uses_the_page_lines(tmp_path):
    from openpyxl import load_workbook

    book = LokaBook(tmp_path / "book.json")
    book.enter(
        side="Short",
        day=date(2026, 11, 12),
        nifty=25980,
        call_symbol="C",
        call_token="1",
        call_entry=110,
        put_symbol="P",
        put_token="2",
        put_entry=90,
        strike=25900,
        expiry="2026-11-17",
        lot_size=65,
    )
    book.close(
        exit_on=date(2026, 11, 13),
        nifty_exit=25840,
        call_exit=40,
        put_exit=110,
        quote="Market",
    )
    sheet = load_workbook(tmp_path / "loka_kalyanam_journal.xlsx")["Journal"]
    assert sheet["A2"].value == "entry 15:00 -- 12-11-2026"
    assert sheet["A3"].value == "exit 14:00 -- 13-11-2026"
    assert sheet["B3"].value == "entry 12-11-2026 · closed 13-11-2026 · Short · Nifty 25,840.00"
    assert sheet["C3"].value == "Buy 25900 call @ 40.00"
    assert sheet["D3"].value == "Sell 25900 put @ 110.00"
    assert sheet["H3"].value == book.capital


def test_short_waits_until_the_next_session_at_14():
    position = {"entry_date": "2026-11-13", "exit_clock": "14:00"}
    assert not exit_due(position, datetime(2026, 11, 13, 15, 1))
    assert not exit_due(position, datetime(2026, 11, 16, 9, 30))
    assert exit_due(position, datetime(2026, 11, 16, 14, 0))
    assert entry_window(datetime(2026, 11, 13, 15, 0))
    assert not entry_window(datetime(2026, 11, 13, 14, 45))


def test_desk_shows_two_rows_and_hides_the_token(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "loka_kalyanam_book.json").write_text(
        json.dumps(
            {
                "name": "Loka Kalyanam",
                "started_on": "2026-11-13",
                "starting_capital": 10000000,
                "capital": 10314967.87,
                "realised_pnl": 314967.87,
                "updated_at": "2026-11-18 09:30:00",
                "position": None,
                "closed": [
                    {
                        "side": "Buy",
                        "entry_date": "2026-11-13",
                        "entry_time": "15:00",
                        "exit_clock": "09:15",
                        "nifty_entry": 18330.3,
                        "call_action": "Buy",
                        "call_token": "SECRET",
                        "call_strike": 18350,
                        "call_entry": 110.8,
                        "put_action": "Sell",
                        "put_token": "SECRET2",
                        "put_strike": 18350,
                        "put_entry": 120.7,
                        "lots": 37,
                        "capital_before": 10000000,
                        "capital_used": 9935166,
                        "exit_date": "2026-11-16",
                        "exit_time": "09:15",
                        "nifty_exit": 18475.4,
                        "call_exit_action": "Sell",
                        "call_exit": 125.4,
                        "put_exit_action": "Buy",
                        "put_exit": 0,
                        "exit_quote": "Market",
                        "points": 131.3,
                        "profit": 315777.46,
                        "charges": 809.59,
                        "capital_after": 10314967.87,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    desk = build_desk(tmp_path, today=date(2026, 11, 13))
    encoded = json.dumps(desk)
    assert "SECRET" not in encoded
    book = desk["loka_kalyanam"]
    assert book["name"] == "Loka Kalyanam"
    assert book["realised"] == 314967.87
    assert [row["event"] for row in book["rows"]] == ["Entry", "Exit"]
    assert book["rows"][0]["date"] == "13-11-2026"
    assert book["rows"][0]["call_strike"] == "18350 call"
    assert book["rows"][1]["put_strike"] == "18350 put"
    assert book["rows"][1]["exit_date"] == "16-11-2026"
    assert book["rows"][1]["time"] == "09:15"
