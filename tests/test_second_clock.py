from datetime import date, datetime

from src.master_strategy import load_pcr_history, remember_pcr, save_pcr_history, second_entry_window
from src.option_buy_strategy import pick_base_signal
from src.paper_decision import add_second_clock, record_decision


def test_fifteen_oh_five_is_its_own_window():
    assert second_entry_window(datetime(2026, 11, 27, 15, 5))
    assert second_entry_window(datetime(2026, 11, 27, 15, 25))
    assert not second_entry_window(datetime(2026, 11, 27, 15, 0))
    assert not second_entry_window(datetime(2026, 11, 27, 14, 5))


def test_fifteen_hundred_history_stays_separate(tmp_path):
    morning = tmp_path / "1400.json"
    afternoon = tmp_path / "1500.json"
    save_pcr_history({"NIFTY": {"2026-11-01": 1.0}}, morning)
    hist = load_pcr_history(afternoon)
    remember_pcr(hist, "NIFTY", date(2026, 11, 27), 0.5)
    save_pcr_history(hist, afternoon)
    assert "2026-11-27" not in load_pcr_history(morning).get("NIFTY", {})
    assert load_pcr_history(afternoon)["NIFTY"]["2026-11-27"] == 0.5


def test_three_pm_check_keeps_the_two_pm_lines(tmp_path):
    path = tmp_path / "decision.json"
    record_decision(
        date(2026, 11, 27),
        datetime(2026, 11, 27, 14, 15),
        [{"script": "NIFTY", "spot": 1, "pcr": 0.7, "rank": 0.4, "tier": "", "side": "", "expiry": ""}],
        synthetic_taken=False,
        synthetic_trade=None,
        synthetic_note="No position.",
        option_taken=False,
        option_trade=None,
        option_note="No position.",
        path=path,
    )

    class Book:
        is_open = False
        position = None
        closed = []

    add_second_clock(
        datetime(2026, 11, 27, 15, 6),
        [{"script": "SENSEX", "spot": 2, "pcr": 1.2, "rank": 0.86, "tier": "base", "side": "Sell", "expiry": ""}],
        Book(),
        Book(),
        ["no base signal"],
        ["no base signal"],
        path,
    )
    import json

    saved = json.loads(path.read_text())
    day = saved["days"][0]
    assert day["scripts"][0]["script"] == "NIFTY"
    assert day["scripts_1500"][0]["script"] == "SENSEX"
    assert "base Sell" in day["scripts_1500"][0]["reason"]


def test_filler_is_not_a_three_pm_buy():
    chosen = pick_base_signal(
        [
            {"script": "BANKNIFTY", "tier": "filler", "side": "Sell"},
            {"script": "NIFTY", "tier": "base", "side": "Buy"},
        ]
    )
    assert chosen["script"] == "NIFTY"
    assert pick_base_signal([{"script": "SENSEX", "tier": "filler", "side": "Buy"}]) is None
