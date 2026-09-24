from datetime import datetime
from zoneinfo import ZoneInfo

from src.scan_slots import (
    active_slot,
    iter_slots_for_day,
    seconds_until_next_slot,
    should_run_slot,
    target_scan_slot,
)

IST = ZoneInfo("Asia/Kolkata")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=IST)


def test_tuesday_keeps_the_15_minute_grid():
    day = _at("2026-08-25 12:00:00")  # Tuesday
    stamps = [row.strftime("%H:%M") for row in iter_slots_for_day(day)]
    assert stamps[0] == "09:30"
    assert stamps[1] == "09:45"
    assert "15:10" in stamps
    assert stamps.index("15:10") == stamps.index("15:00") + 1
    assert stamps.index("15:15") == stamps.index("15:10") + 1
    assert "15:30" in stamps
    assert stamps[-1] == "15:40"
    assert len(stamps) == 27


def test_monday_is_every_30_minutes_without_1510():
    day = _at("2026-08-24 12:00:00")  # Monday
    stamps = [row.strftime("%H:%M") for row in iter_slots_for_day(day)]
    assert stamps[0] == "09:30"
    assert stamps[1] == "10:00"
    assert "09:45" not in stamps
    assert "14:30" in stamps
    assert "15:00" in stamps
    assert "15:10" not in stamps
    assert "15:15" not in stamps
    assert "15:30" in stamps
    assert stamps[-1] == "15:40"
    assert len(stamps) == 14


def test_active_slot_follows_the_day_grid():
    assert active_slot(_at("2026-08-24 09:37:00")).strftime("%H:%M") == "09:30"  # Monday
    assert active_slot(_at("2026-08-24 09:45:00")).strftime("%H:%M") == "09:30"
    assert active_slot(_at("2026-08-25 09:45:00")).strftime("%H:%M") == "09:45"  # Tuesday
    assert active_slot(_at("2026-08-24 15:12:00")).strftime("%H:%M") == "15:00"
    assert active_slot(_at("2026-08-25 15:08:00")).strftime("%H:%M") == "15:00"
    assert active_slot(_at("2026-08-25 15:10:00")).strftime("%H:%M") == "15:10"
    assert active_slot(_at("2026-08-25 15:12:00")).strftime("%H:%M") == "15:10"
    assert active_slot(_at("2026-08-25 15:15:00")).strftime("%H:%M") == "15:15"
    assert active_slot(_at("2026-08-24 15:35:00")).strftime("%H:%M") == "15:30"
    assert active_slot(_at("2026-08-24 15:42:00")).strftime("%H:%M") == "15:40"
    assert active_slot(_at("2026-08-24 09:20:00")) is None
    assert active_slot(_at("2026-08-24 16:01:00")) is None


def test_weekend_and_holiday_skip():
    assert active_slot(_at("2026-08-22 10:00:00")) is None  # Saturday
    assert active_slot(_at("2026-10-02 10:00:00")) is None  # Gandhi Jayanti


def test_before_open_does_not_start_a_job(tmp_path):
    marker = tmp_path / "last.txt"
    run, reason, slot = should_run_slot(
        now=_at("2026-08-24 09:22:00"),
        path=marker,
    )
    assert run is False
    assert "outside" in reason
    assert slot is None


def test_skip_completed_slot(tmp_path):
    marker = tmp_path / "last.txt"
    due = target_scan_slot(now=_at("2026-08-24 10:02:00"), path=marker)
    assert due is not None
    marker.write_text(due.isoformat(), encoding="utf-8")
    run, reason, _ = should_run_slot(now=_at("2026-08-24 10:02:00"), path=marker)
    assert run is False
    assert "already completed" in reason


def test_1510_runs_on_tuesday_after_1500(tmp_path):
    marker = tmp_path / "last.txt"
    fifteen = active_slot(_at("2026-08-25 15:00:00"))
    assert fifteen is not None
    marker.write_text(fifteen.isoformat(), encoding="utf-8")
    run, reason, slot = should_run_slot(
        now=_at("2026-08-25 15:10:00"),
        path=marker,
    )
    assert run is True
    assert slot is not None
    assert slot.strftime("%H:%M") == "15:10"
    assert "15:10" in reason


def test_monday_1510_does_not_open_a_new_slot(tmp_path):
    marker = tmp_path / "last.txt"
    fifteen = active_slot(_at("2026-08-24 15:00:00"))
    assert fifteen is not None
    marker.write_text(fifteen.isoformat(), encoding="utf-8")
    run, reason, slot = should_run_slot(
        now=_at("2026-08-24 15:10:00"),
        path=marker,
    )
    assert run is False
    assert slot is not None
    assert slot.strftime("%H:%M") == "15:00"
    assert "already completed" in reason


def test_morning_chain_starts_before_open():
    wait = seconds_until_next_slot(_at("2026-08-24 08:30:00"))
    assert wait is not None
    assert 50 * 60 < wait <= 60 * 60
