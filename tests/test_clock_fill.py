from datetime import datetime, time

from src.clock_fill import choose_price, pick_bar_open, wait_until


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


def test_wait_until_wakes_on_the_clock_and_ignores_a_late_start():
    clock = {"at": datetime(2026, 11, 23, 14, 4, 30)}

    def now():
        return clock["at"]

    def sleep(seconds):
        clock["at"] = datetime(2026, 11, 23, 14, 5, 0)

    assert wait_until(time(14, 5), now=now, sleep=sleep) is True
    assert wait_until(time(14, 5), now=now, sleep=sleep) is False
