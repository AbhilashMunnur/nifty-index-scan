from datetime import date, datetime

from src.loka_kalyanam import leg_costs
from src.option_buy_book import OptionBuyBook
from src.option_buy_strategy import (
    opt_side_for,
    pick_base_signal,
    size_from_premium,
    size_slip,
)


def test_opt_side_maps_signal():
    assert opt_side_for("Buy") == "CE"
    assert opt_side_for("Sell") == "PE"


def test_pick_base_ignores_filler():
    cands = [
        {"script": "NIFTY", "tier": "filler", "side": "Buy", "rank": 0.2},
        {"script": "SENSEX", "tier": "base", "side": "Sell", "rank": 0.85},
        {"script": "BANKNIFTY", "tier": "filler", "side": "Sell", "rank": 0.75},
    ]
    chosen = pick_base_signal(cands)
    assert chosen["script"] == "SENSEX"
    assert chosen["tier"] == "base"


def test_size_slip_grows_with_lots():
    assert size_slip(37, "NIFTY") == 2.0
    assert size_slip(148, "NIFTY") > 2.0
    assert size_slip(30, "BANKNIFTY") == 2.0


def test_size_from_premium_fits_budget():
    sized = size_from_premium(100.0, lot_size=65, symbol="NIFTY", budget=10_000_000)
    assert sized is not None
    assert sized["lots"] >= 1
    assert sized["qty"] == sized["lots"] * 65
    assert sized["lots"] * sized["per_lot"] <= 10_000_000 + 1e-6
    assert sized["entry"] >= 100.0 + 2.0 - 1e-9


def test_book_round_trip_deducts_charges(tmp_path):
    book = OptionBuyBook(tmp_path / "ob.json")
    msg = book.enter(
        script="NIFTY",
        signal_side="Buy",
        day=date(2026, 11, 18),
        spot=25000,
        pcr=0.9,
        pcr_rank=0.05,
        opt_side="CE",
        symbol="NIFTY18NOV2525000CE",
        token="1",
        raw_open=120.0,
        strike=25000,
        expiry="2026-11-25",
        lot_size=65,
    )
    assert book.is_open
    assert "BASE Buy CE" in msg
    pos = book.position
    exit_msg = book.exit(day=date(2026, 11, 19), raw_open=140.0, why="time")
    assert not book.is_open
    trade = book.closed[0]
    costs = leg_costs(date(2026, 11, 19), trade["exit"], trade["entry"], trade["qty"])
    assert trade["charges"] == costs["total"]
    assert trade["net"] == round(trade["gross"] - trade["charges"], 2)
    assert "charges" in exit_msg
    assert book.realised_pnl == trade["net"]


def test_exit_due_next_day_after_1500():
    from src.option_buy_strategy import exit_due

    pos = {"entry_date": "2026-11-18"}
    assert not exit_due(pos, datetime(2026, 11, 18, 15, 30))
    assert not exit_due(pos, datetime(2026, 11, 19, 14, 59))
    assert exit_due(pos, datetime(2026, 11, 19, 15, 0))
