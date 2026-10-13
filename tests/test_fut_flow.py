from datetime import datetime
from zoneinfo import ZoneInfo

from src.fut_flow import (
    classify_fut_flow,
    format_fut_alert,
    format_fut_day_alert,
    mark_path,
    oi_change_pct,
    reading_from_mark,
    save_day_open_once,
)
from src.fut_slots import active_fut_slot, iter_fut_slots, should_run_fut_slot
from pathlib import Path

IST = ZoneInfo("Asia/Kolkata")


def _read(**kwargs):
    base = dict(
        price=100.0,
        oi=130_000,
        lot_size=65,
        prev_price=90.0,
        prev_oi=65_000,
        tradingsymbol="NIFTY26OCTFUT",
        expiry="2026-10-27",
        compared_to="2026-10-01 14:15:00",
    )
    base.update(kwargs)
    return classify_fut_flow(**base)


def test_new_buying_and_selling_use_futures_oi_change():
    buying = _read(price=110.0, prev_price=100.0, oi=130_000, prev_oi=65_000)
    selling = _read(price=90.0, prev_price=100.0, oi=130_000, prev_oi=65_000)
    assert buying.label == "NEW BUYING"
    assert selling.label == "NEW SELLING"
    assert buying.lots == 1_000
    assert selling.lots == 1_000


def test_cover_and_exit_share_the_oi_decrease():
    cover = _read(price=110.0, prev_price=100.0, oi=65_000, prev_oi=130_000)
    exit_ = _read(price=90.0, prev_price=100.0, oi=65_000, prev_oi=130_000)
    assert cover.label == "SHORT COVERING"
    assert exit_.label == "LONG EXIT"
    assert cover.lots == 1_000
    assert exit_.lots == 1_000


def test_flat_price_does_not_invent_a_side():
    up = _read(price=100.0, prev_price=100.0, oi=130_000, prev_oi=65_000)
    down = _read(price=100.0, prev_price=100.0, oi=65_000, prev_oi=130_000)
    assert up.label == "OI UP"
    assert down.label == "OI DOWN"


def test_ten_paise_does_not_flip_between_buying_and_selling():
    """The 14:45 and 14:46 alerts used one OI change and a 0.10 price flicker."""
    flicker = _read(price=22448.50, prev_price=22448.40, oi=300_587 * 65, prev_oi=298_645 * 65)
    real_drop = _read(price=22445.70, prev_price=22448.40, oi=300_587 * 65, prev_oi=298_645 * 65)
    assert flicker.label == "OI UP"
    assert flicker.lots == 1_942
    assert real_drop.label == "NEW SELLING"
    assert real_drop.lots == 1_942


def test_sensex_alert_is_separate_from_nifty():
    when = datetime(2026, 10, 5, 9, 30, tzinfo=IST)
    nifty = _read()
    sensex = _read(tradingsymbol="SENSEX26OCTFUT", lot_size=20, expiry="2026-10-29")
    assert format_fut_alert(nifty, when=when).startswith("NIFTY FUT 15m")
    assert format_fut_alert(sensex, when=when, name="SENSEX").startswith("SENSEX FUT 15m")
    assert mark_path("NIFTY").name == "nifty_fut_flow_mark.json"
    assert mark_path("SENSEX").name == "sensex_fut_flow_mark.json"


def test_day_summary_names_the_session_and_the_oi_percentage():
    reading = _read(price=110.0, prev_price=100.0, oi=110_000, prev_oi=100_000, lot_size=65)
    text = format_fut_day_alert(
        reading,
        when=datetime(2026, 10, 5, 16, 0, tzinfo=IST),
        name="NIFTY",
        start_at="2026-10-05 09:30:12",
        end_at="2026-10-05 15:30:40",
    )
    assert "LONG BUILDUP" in text
    assert "+10.00%" in text
    assert oi_change_pct(100_000, 110_000) == 10.0
    unwind = _read(price=90.0, prev_price=100.0, oi=90_000, prev_oi=100_000)
    cover = _read(price=110.0, prev_price=100.0, oi=90_000, prev_oi=100_000)
    short = _read(price=90.0, prev_price=100.0, oi=110_000, prev_oi=100_000)
    assert "LONG UNWIND" in format_fut_day_alert(
        unwind, when=datetime(2026, 10, 5, 16, 0, tzinfo=IST), name="SENSEX",
        start_at="09:30", end_at="15:30",
    )
    assert "SHORT COVERING" in format_fut_day_alert(
        cover, when=datetime(2026, 10, 5, 16, 0, tzinfo=IST), name="SENSEX",
        start_at="09:30", end_at="15:30",
    )
    assert "SHORT BUILDUP" in format_fut_day_alert(
        short, when=datetime(2026, 10, 5, 16, 0, tzinfo=IST), name="NIFTY",
        start_at="09:30", end_at="15:30",
    )


def test_day_open_is_kept_for_the_whole_session(tmp_path: Path):
    path = tmp_path / "open.json"
    fields = dict(tradingsymbol="NIFTY27OCT26FUT", token="1", expiry="2026-10-27", lot_size=65)
    assert save_day_open_once(**fields, price=100.0, oi=1000, path=path) is True
    assert save_day_open_once(**fields, price=200.0, oi=2000, path=path) is False
    import json
    body = json.loads(path.read_text())
    assert body["price"] == 100.0
    assert body["oi"] == 1000


def test_rollover_starts_a_new_baseline():
    reading = reading_from_mark(
        {"tradingsymbol": "NIFTY26SEPFUT", "price": 100.0, "oi": 65_000, "updated_at": "x"},
        price=110.0,
        oi=130_000,
        lot_size=65,
        tradingsymbol="NIFTY26OCTFUT",
        expiry="2026-10-27",
    )
    assert reading.label == "BASELINE"
    text = format_fut_alert(reading, when=datetime(2026, 10, 1, 14, 30, tzinfo=IST))
    assert "Baseline stored" in text
    assert "NIFTY26OCTFUT" in text


def test_weekday_grid_is_every_15_minutes_through_1530():
    monday = datetime(2026, 9, 28, 12, 0, tzinfo=IST)
    thursday = datetime(2026, 10, 1, 12, 0, tzinfo=IST)
    mon_slots = iter_fut_slots(monday)
    thu_slots = iter_fut_slots(thursday)
    assert len(mon_slots) == len(thu_slots) == 25
    assert mon_slots[0].strftime("%H:%M") == "09:30"
    assert mon_slots[-1].strftime("%H:%M") == "15:30"
    assert all(slot.minute in (0, 15, 30, 45) for slot in mon_slots)
    assert not any(slot.hour == 15 and slot.minute == 10 for slot in thu_slots)


def test_slot_runs_once(tmp_path: Path):
    marker = tmp_path / "slot.txt"
    at = datetime(2026, 10, 1, 14, 31, tzinfo=IST)
    run, reason, slot = should_run_fut_slot(now=at, path=marker)
    assert run and slot is not None and slot.strftime("%H:%M") == "14:30"
    assert "14:30" in reason
    marker.write_text(slot.isoformat(), encoding="utf-8")
    again, _, _ = should_run_fut_slot(now=at, path=marker)
    assert again is False
    assert active_fut_slot(datetime(2026, 10, 1, 15, 10, tzinfo=IST)).strftime("%H:%M") == "15:00"
