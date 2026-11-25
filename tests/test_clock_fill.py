from datetime import datetime, time

from src.clock_fill import choose_price, fill_wait, paper_phase, pick_bar_open, wait_until


def test_pick_bar_open_is_the_1405_bar_only():
    bars = [
        {"stamp": datetime(2026, 11, 23, 14, 0), "open": 100.0},
        {"stamp": datetime(2026, 11, 23, 14, 5), "open": 109.4},
        {"stamp": datetime(2026, 11, 23, 14, 10), "open": 130.0},
    ]
    assert pick_bar_open(bars, datetime(2026, 11, 23, 14, 5, 2)) == 109.4
    assert pick_bar_open(bars, datetime(2026, 11, 23, 14, 15)) is None


def test_candle_open_beats_a_snapshot():
    px, source = choose_price(109.4, 112.0, armed=True, snapshot_label="14:05 snapshot")
    assert px == 109.4
    assert source == "5m open"


def test_snapshot_only_when_the_process_woke_on_the_clock():
    px, source = choose_price(None, 109.4, armed=True, snapshot_label="14:05 snapshot")
    assert px == 109.4
    assert source == "14:05 snapshot"
    late, late_source = choose_price(None, 112.0, armed=False, snapshot_label="14:05 snapshot")
    assert late is None
    assert late_source == "missing"


def test_entry_is_armed_before_1405_and_late_fill_stays_on_that_open():
    assert paper_phase(datetime(2026, 11, 26, 13, 50)) == "entry_arm"
    assert paper_phase(datetime(2026, 11, 26, 14, 0, 8)) == "entry_arm"
    assert paper_phase(datetime(2026, 11, 26, 14, 4, 59)) == "entry_arm"
    assert paper_phase(datetime(2026, 11, 26, 14, 5)) == "entry_late"
    assert paper_phase(datetime(2026, 11, 26, 14, 25)) == "entry_late"
    assert paper_phase(datetime(2026, 11, 26, 14, 26)) == "off"
    assert paper_phase(datetime(2026, 11, 26, 14, 50)) == "exit_arm"
    assert paper_phase(datetime(2026, 11, 26, 15, 0)) == "off"


def test_on_the_clock_fill_does_not_wait_for_the_candle():
    assert fill_wait(90, armed=True) == 0
    assert fill_wait(90, armed=False) == 90


def test_wait_until_holds_an_early_start_for_the_1405_print():
    clock = {"at": datetime(2026, 11, 26, 13, 50)}

    def now():
        return clock["at"]

    def sleep(seconds):
        clock["at"] = datetime(2026, 11, 26, 14, 5)

    assert wait_until(time(14, 5), now=now, sleep=sleep, early_limit_s=20 * 60) is True


def test_wait_until_wakes_on_the_clock_and_ignores_a_late_start():
    clock = {"at": datetime(2026, 11, 23, 14, 4, 30)}

    def now():
        return clock["at"]

    def sleep(seconds):
        clock["at"] = datetime(2026, 11, 23, 14, 5, 0)

    assert wait_until(time(14, 5), now=now, sleep=sleep) is True
    assert wait_until(time(14, 5), now=now, sleep=sleep) is False
