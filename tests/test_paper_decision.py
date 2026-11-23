from datetime import datetime

from src.paper_decision import commit_session, record_decision, script_reason


class _Book:
    def __init__(self, position=None, closed=None):
        self.position = position
        self.closed = closed or []

    @property
    def is_open(self):
        return isinstance(self.position, dict)


def test_middle_rank_says_which_band_was_missed():
    assert "outside base 20/80" in script_reason("SENSEX", 0.492, "", "")
    assert "outside base 10/90" in script_reason("NIFTY", 0.644, "", "")
    assert script_reason("SENSEX", 0.85, "base", "Sell") == "Sensex 85.0% is a base Sell."


def test_same_day_check_replaces_the_earlier_one(tmp_path):
    path = tmp_path / "paper_decision.json"
    readings = [
        {"script": "SENSEX", "spot": 82658.54, "pcr": 0.979, "rank": 0.492, "tier": "", "side": "", "expiry": "2026-11-26"},
        {"script": "NIFTY", "spot": 25423.75, "pcr": 0.963, "rank": 0.644, "tier": "", "side": "", "expiry": "2026-11-23"},
    ]
    record_decision(
        datetime(2026, 11, 23, 14, 0).date(),
        datetime(2026, 11, 23, 14, 0),
        readings,
        synthetic_taken=False,
        synthetic_trade=None,
        synthetic_note="",
        option_taken=False,
        option_trade=None,
        option_note="",
        path=path,
    )
    row = commit_session(
        datetime(2026, 11, 23, 14, 15),
        readings,
        _Book(),
        _Book(),
        ["entry window — no signal"],
        ["entry window — no BASE signal"],
        path=path,
    )
    assert row["synthetic"]["taken"] is False
    assert "Sensex 49.2%" in row["synthetic"]["summary"]
    assert "Nifty 64.4%" in row["option_buy"]["summary"]
    saved = path.read_text(encoding="utf-8")
    assert saved.count("2026-11-23") >= 1
    assert "14:00:00" not in saved


def test_an_open_trade_is_reported_as_taken(tmp_path):
    path = tmp_path / "paper_decision.json"
    readings = [
        {"script": "SENSEX", "pcr": 1.2, "rank": 0.86, "tier": "base", "side": "Sell", "expiry": "2026-11-26"},
    ]
    book = _Book(position={"entry_date": "2026-11-23", "script": "SENSEX", "tier": "base", "side": "Sell", "pcr_rank": 0.86})
    row = commit_session(
        datetime(2026, 11, 23, 14, 5),
        readings,
        book,
        _Book(),
        ["entered"],
        ["entry window — no BASE signal"],
        path=path,
    )
    assert row["synthetic"]["taken"] is True
    assert row["synthetic"]["summary"].startswith("Position taken.")
    assert row["option_buy"]["taken"] is False
