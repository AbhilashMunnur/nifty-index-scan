from datetime import date

from src.calendar import trading_days_remaining
from src.config import load_config
from src.pcr import BandPcr, StrikePcr
from src.strategy import (
    StrategyRules,
    evaluate_signal,
    first_target_price,
    remaining_lot_exit,
    scale_lot_hit,
    stop_loss_hit,
    stop_loss_price,
)
from src.strikes import atm_band
from src.universe import ALLOWED_SYMBOLS, UniverseError, require_index, validate_universe


def test_only_nifty_and_sensex_are_allowed():
    assert require_index("nifty").symbol == "NIFTY"
    assert require_index("Sensex").symbol == "SENSEX"
    assert validate_universe(["NIFTY", "SENSEX"]) == ["NIFTY", "SENSEX"]


def test_stocks_and_other_indices_are_rejected():
    for symbol in ("RELIANCE", "BANKNIFTY", "FINNIFTY", "BANKEX"):
        try:
            require_index(symbol)
            raise AssertionError(f"{symbol} should be rejected")
        except UniverseError:
            pass


def test_config_universe_is_locked():
    config = load_config()
    assert config.universe == list(ALLOWED_SYMBOLS)
    assert config.mode in {"observe", "paper"}
    assert config.strategy_symbol == "NIFTY"
    assert config.strategy_symbols == ["NIFTY", "SENSEX"]
    assert config.rules.min_trading_days_to_expiry == 10
    assert config.lots == 2
    assert config.rules.first_target_pct == 1.5
    assert config.rules.stop_loss_pct == 0.75


def test_sensex_gets_its_own_paper_book_and_oi_mark():
    from src.oi_flow import MARKER, mark_path_for
    from src.paper import LEDGER_PATH, NiftyPaperBook, ledger_path_for
    from src.paths import ROOT

    assert mark_path_for("NIFTY") == MARKER
    assert mark_path_for("SENSEX") == ROOT / "data" / "sensex_band_scan_mark.json"
    assert ledger_path_for("NIFTY") == LEDGER_PATH
    assert ledger_path_for("SENSEX") == ROOT / "data" / "sensex_paper_book.json"
    book = NiftyPaperBook(symbol="SENSEX", lots=2)
    assert book.symbol == "SENSEX"
    assert book.path == ledger_path_for("SENSEX")


def test_signal_report_titles_the_index():
    from src.report import format_signal_report

    text = format_signal_report(
        spot=76000.0,
        rsi=55.0,
        future=None,
        skipped=None,
        band=None,
        signal=None,
        reason="1H RSI 55.0 is between 30 and 70 (PCR n/a, ΔPCR n/a)",
        symbol="SENSEX",
    )
    assert text.startswith("Sensex 1H strategy")
    assert "Spot 76,000.00" in text
    as_of = date(2026, 8, 20)
    august = date(2026, 8, 25)
    september = date(2026, 9, 29)
    assert trading_days_remaining(as_of, august) < 10
    assert trading_days_remaining(as_of, september) >= 10


def test_atm_band_is_five_strikes_around_spot():
    strikes = [float(x) for x in range(23900, 24250, 50)]
    band = atm_band(strikes, 24078.3)
    assert band is not None
    labels, values = zip(*band)
    assert labels == ("ITM2", "ITM1", "ATM", "OTM1", "OTM2")
    assert values == (24000.0, 24050.0, 24100.0, 24150.0, 24200.0)


def _band(change_pcr_call: int, change_pcr_put: int, call_oi: int = 100, put_oi: int = 80) -> BandPcr:
    return BandPcr(
        expiry="2026-08-25",
        atm=24050,
        step=50,
        lot_size=65,
        strikes=[
            StrikePcr("ATM", 24050, call_oi, put_oi, call_oi_change=change_pcr_call, put_oi_change=change_pcr_put)
        ],
    )


def test_short_requires_rsi_70_and_change_pcr_below_0_8():
    rules = StrategyRules()
    signal, _reason = evaluate_signal(70, _band(100, 70), rules)
    assert signal == "SHORT"
    signal, _reason = evaluate_signal(69.9, _band(100, 70), rules)
    assert signal is None
    signal, _reason = evaluate_signal(80, _band(100, 80), rules)
    assert signal is None


def test_long_requires_rsi_30_and_change_pcr_above_1():
    rules = StrategyRules()
    signal, _reason = evaluate_signal(30, _band(100, 110), rules)
    assert signal == "LONG"
    signal, _reason = evaluate_signal(30.1, _band(100, 110), rules)
    assert signal is None
    signal, _reason = evaluate_signal(20, _band(100, 100), rules)
    assert signal is None


def test_first_lot_books_at_1_5_percent():
    entry = 24394.90
    short_target = first_target_price("SHORT", entry, 1.5)
    long_target = first_target_price("LONG", entry, 1.5)
    assert abs(short_target - entry * 0.985) < 1e-6
    assert abs(long_target - entry * 1.015) < 1e-6
    assert scale_lot_hit("SHORT", entry, short_target, 1.5)
    assert not scale_lot_hit("SHORT", entry, entry * 0.99, 1.5)
    assert scale_lot_hit("LONG", entry, long_target, 1.5)


def test_remaining_short_books_at_35_if_atm_pcr_flips():
    rules = StrategyRules()
    assert remaining_lot_exit("SHORT", 35, 1.1, rules)
    assert remaining_lot_exit("SHORT", 33, 1.2, rules)
    assert remaining_lot_exit("SHORT", 32, 0.5, rules) is None
    assert remaining_lot_exit("SHORT", 30, 0.5, rules)
    assert remaining_lot_exit("SHORT", 40, 2.0, rules) is None


def test_remaining_long_books_at_65_if_atm_pcr_flips():
    rules = StrategyRules()
    assert remaining_lot_exit("LONG", 65, 0.7, rules)
    assert remaining_lot_exit("LONG", 68, 0.7, rules)
    assert remaining_lot_exit("LONG", 68, 1.2, rules) is None
    assert remaining_lot_exit("LONG", 70, 1.2, rules)
    assert remaining_lot_exit("LONG", 60, 0.5, rules) is None


def test_rsi_exit_before_1_5_percent_books_both_lots():
    from src.models import FutureContract
    from src.paper import NiftyPaperBook
    from pathlib import Path
    import tempfile

    rules = StrategyRules()
    path = Path(tempfile.mkdtemp()) / "book.json"
    book = NiftyPaperBook(path=path, lots=2)
    fut = FutureContract("NIFTY", "NIFTY29SEP26FUT", "1", "2026-09-29", 65, 27, "NFO")
    book.apply(
        "SHORT",
        fut,
        24394.90,
        rsi=71,
        pcr=0.7,
        change_pcr=0.6,
        reason="test",
        rules=rules,
    )
    # RSI 34 + ATM ΔPCR 1.2, price has not reached 1.5% — close both lots.
    notes = book.manage(24100.0, 34, 1.2, rules)
    assert len(notes) == 1
    assert "2 lots" in notes[0]
    assert book.is_open is False


def test_1_5_percent_first_still_books_only_one_lot():
    from src.models import FutureContract
    from src.paper import NiftyPaperBook
    from pathlib import Path
    import tempfile

    rules = StrategyRules()
    path = Path(tempfile.mkdtemp()) / "book.json"
    book = NiftyPaperBook(path=path, lots=2)
    fut = FutureContract("NIFTY", "NIFTY29SEP26FUT", "1", "2026-09-29", 65, 27, "NFO")
    entry = 24394.90
    book.apply("SHORT", fut, entry, rsi=71, pcr=0.7, change_pcr=0.6, reason="test", rules=rules)
    notes = book.manage(first_target_price("SHORT", entry, 1.5), 60, 0.5, rules)
    assert len(notes) == 1
    assert "1 lot" in notes[0]
    assert book.is_open
    assert book.position["lots"] == 1
    assert book.position["scale_lot_open"] is False
    assert book.position["rsi_lot_open"] is True


def test_adverse_0_75_percent_stops_all_lots_then_allows_reentry():
    from src.models import FutureContract
    from src.paper import NiftyPaperBook
    from pathlib import Path
    import tempfile

    rules = StrategyRules()
    path = Path(tempfile.mkdtemp()) / "book.json"
    book = NiftyPaperBook(path=path, lots=2)
    fut = FutureContract("NIFTY", "NIFTY29SEP26FUT", "1", "2026-09-29", 65, 27, "NFO")
    entry = 24394.90
    book.apply("SHORT", fut, entry, rsi=71, pcr=0.7, change_pcr=0.6, reason="test", rules=rules)

    stop = stop_loss_price("SHORT", entry, 0.75)
    assert stop_loss_hit("SHORT", entry, stop, 0.75)
    assert not stop_loss_hit("SHORT", entry, entry * 1.005, 0.75)
    notes = book.manage(stop, 72, 0.5, rules)
    assert len(notes) == 1
    assert "2 lots" in notes[0]
    assert "SL 0.75%" in notes[0]
    assert book.is_open is False

    again = book.apply(
        "SHORT",
        fut,
        stop,
        rsi=72,
        pcr=0.6,
        change_pcr=0.5,
        reason="rules still valid",
        rules=rules,
    )
    assert "Paper SHORT 2 lots" in again
    assert book.is_open
    assert book.position["lots"] == 2


def test_odd_lots_put_more_on_the_1_5_percent_side():
    from src.strategy import split_lots

    assert split_lots(2) == (1, 1)
    assert split_lots(3) == (2, 1)
    assert split_lots(4) == (2, 2)
    assert split_lots(5) == (3, 2)


def test_long_stop_is_0_75_percent_below_entry():
    entry = 24000.0
    stop = stop_loss_price("LONG", entry, 0.75)
    assert abs(stop - entry * 0.9925) < 1e-6
    assert stop_loss_hit("LONG", entry, stop, 0.75)
    assert not stop_loss_hit("LONG", entry, entry * 0.995, 0.75)
