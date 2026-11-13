import json
from datetime import date, datetime, time

from src.desk import build_desk
from src.loka_kalyanam import LokaBook
from src.loka_oi import (
    VARIANTS,
    choose_side,
    exit_step,
    load_pcr,
    pcr_percentile,
    remember_pcr,
    series_ending,
)


def test_low_ratio_follows_yesterday_and_a_high_ratio_stays_with_loka():
    assert choose_side(None, 100, 99, 0.10, 0.20) == ("Buy", "OI day")
    assert choose_side("Buy", 98, 99, 0.10, 0.20) == ("Short", "OI day")
    assert choose_side("Buy", 100, 100, 0.10, 0.20) == (None, "OI day")
    assert choose_side("Short", 100, 99, 0.40, 0.20) == ("Short", "Loka")
    assert choose_side(None, 100, 99, None, 0.20) == (None, "Loka")


def test_percentile_uses_only_the_window_and_needs_half_of_it():
    values = [float(i) for i in range(10)]
    assert pcr_percentile(values, 60) is None
    assert pcr_percentile(values, 10) == 1.0
    assert pcr_percentile([5, 1, 2, 3, 4, 0], 6) == 0.0


def test_morning_profit_closes_and_a_loss_waits_for_14():
    check, fallback = time(10, 0), time(14, 0)
    assert exit_step(None, False, time(9, 45), check, fallback) == "wait"
    assert exit_step(12, False, time(10, 0), check, fallback) == "close_check"
    assert exit_step(-3, False, time(10, 15), check, fallback) == "mark"
    assert exit_step(None, True, time(11, 0), check, fallback) == "wait"
    assert exit_step(None, True, time(14, 0), check, fallback) == "close_fallback"
    assert exit_step(-3, False, time(14, 0), check, fallback) == "close_fallback"
    assert exit_step(None, False, time(10, 0), check, fallback) == "wait"
    assert exit_step(None, False, time(14, 0), time(10, 15), fallback) == "close_fallback"


def test_a_stored_ratio_is_kept_for_the_day(tmp_path):
    path = tmp_path / "pcr.json"
    remember_pcr(date(2026, 11, 13), 0.7, path)
    remember_pcr(date(2026, 11, 13), 0.4, path)
    rows = load_pcr(path)
    assert rows == [{"date": "2026-11-13", "pcr": 0.7}]
    assert series_ending(rows, date(2026, 11, 13), 0.4) == [0.7]


def test_oi_book_records_the_signal_and_the_clock_it_actually_used(tmp_path):
    variant = VARIANTS[0]
    book = LokaBook(tmp_path / "book.json", name=variant.name, journal_name=variant.journal_name)
    book.enter(
        side="Buy",
        day=date(2026, 11, 16),
        nifty=25000,
        call_symbol="C",
        call_token="SECRET",
        call_entry=100,
        put_symbol="P",
        put_token="SECRET2",
        put_entry=90,
        strike=25000,
        expiry="2026-11-24",
        lot_size=65,
        signal="OI day",
        pcr=0.651,
        pcr_rank=12.4,
        check_clock="10:00",
        fallback_clock="14:00",
    )
    assert book.position["exit_clock"] == "10:00"
    assert book.position["signal"] == "OI day"
    book.mark_checked()
    assert book.position["checked"] is True
    book.close(
        exit_on=date(2026, 11, 17),
        nifty_exit=24900,
        call_exit=80,
        put_exit=110,
        quote="Market",
        exit_clock_used="14:00",
        reason="14:00 (not in profit at 10:00)",
    )
    trade = book.closed[0]
    assert trade["exit_time"] == "14:00"
    assert trade["exit_reason"] == "14:00 (not in profit at 10:00)"
    assert trade["capital_after"] == round(trade["capital_before"] + trade["profit"] - trade["charges"], 2)
    assert (tmp_path / variant.journal_name).exists()


def test_desk_lists_both_books_and_hides_tokens(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "loka_oi_120_book.json").write_text(
        json.dumps(
            {
                "name": "Loka OI 120",
                "started_on": "2026-11-13",
                "starting_capital": 10000000,
                "capital": 10000000,
                "realised_pnl": 0,
                "position": {
                    "side": "Short",
                    "entry_date": "2026-11-16",
                    "entry_time": "15:00",
                    "exit_clock": "10:00",
                    "check_clock": "10:00",
                    "fallback_clock": "14:00",
                    "signal": "OI day",
                    "pcr": 0.7,
                    "pcr_rank": 8,
                    "call_token": "SECRET",
                    "call_action": "Sell",
                    "call_strike": 25000,
                    "call_entry": 40,
                    "put_token": "SECRET2",
                    "put_action": "Buy",
                    "put_strike": 25000,
                    "put_entry": 80,
                    "lots": 37,
                    "capital_before": 10000000,
                    "capital_used": 9935166,
                },
                "closed": [],
            }
        ),
        encoding="utf-8",
    )
    desk = build_desk(tmp_path, today=date(2026, 11, 16))
    assert "SECRET" not in json.dumps(desk)
    book = desk["loka_oi_120"]
    assert book["name"] == "Loka OI 120"
    assert book["open"]["signal"] == "OI day"
    assert book["open"]["check_clock"] == "10:00"
    assert book["open"]["fallback_clock"] == "14:00"
    assert desk["loka_oi_60"]["saved"] is False


def test_saved_ratio_history_covers_both_windows():
    rows = load_pcr()
    assert len(rows) >= 120
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates)
    assert pcr_percentile(series_ending(rows, date(2026, 11, 13), 0.5), 120) is not None
    assert pcr_percentile(series_ending(rows, date(2026, 11, 13), 0.5), 60) is not None
