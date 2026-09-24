from datetime import datetime
from zoneinfo import ZoneInfo

from src.oi_flow import classify_band_flow
from src.pcr import BandPcr, StrikePcr
from src.power_hour import (
    format_power_hour_report,
    is_capture_slot,
    is_report_slot,
    save_baseline,
    should_capture_baseline,
    todays_mark,
)
from src.telegram_client import scan_message_html

IST = ZoneInfo("Asia/Kolkata")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=IST)


def _band(
    *,
    expiry="2026-09-22",
    atm=23100.0,
    lot_size=65,
    call_oi=100_000,
    put_oi=200_000,
    call_ltp=100.0,
    put_ltp=80.0,
) -> BandPcr:
    return BandPcr(
        expiry=expiry,
        atm=atm,
        step=50.0,
        lot_size=lot_size,
        strikes=[
            StrikePcr(
                label="ATM",
                strike=atm,
                call_oi=call_oi,
                put_oi=put_oi,
                call_oi_change=0,
                put_oi_change=0,
                call_ltp=call_ltp,
                put_ltp=put_ltp,
            )
        ],
    )


def test_capture_and_report_slots():
    assert is_capture_slot(_at("2026-09-17 14:30:00"))
    assert is_capture_slot(_at("2026-09-17 14:45:00"))
    assert not is_capture_slot(_at("2026-09-17 15:00:00"))
    assert is_report_slot(_at("2026-09-17 15:00:00"))
    assert is_report_slot(_at("2026-09-17 15:10:00"))  # Thursday
    assert is_report_slot(_at("2026-09-17 15:15:00"))
    assert not is_report_slot(_at("2026-09-14 15:10:00"))  # Monday
    assert not is_report_slot(_at("2026-09-14 15:15:00"))
    assert is_report_slot(_at("2026-09-14 15:00:00"))
    assert not is_report_slot(_at("2026-09-17 15:30:00"))
    assert not is_report_slot(_at("2026-09-17 14:30:00"))


def test_1430_mark_is_not_overwritten_at_1445(tmp_path):
    path = tmp_path / "power_hour_mark.json"
    nifty = _band()
    slot_1430 = _at("2026-09-17 14:30:00")
    assert should_capture_baseline(slot_1430, path=path)
    save_baseline(
        nifty=nifty,
        sensex=None,
        nifty_spot=23100.0,
        sensex_spot=None,
        slot=slot_1430,
        path=path,
    )
    mark = todays_mark(path, now=slot_1430)
    assert mark["baseline_slot"] == "14:30"
    assert mark["indices"]["NIFTY"]["atm"] == 23100.0
    assert not should_capture_baseline(_at("2026-09-17 14:45:00"), path=path)


def test_1445_retries_if_1430_had_no_nifty(tmp_path):
    path = tmp_path / "power_hour_mark.json"
    slot_1430 = _at("2026-09-17 14:30:00")
    save_baseline(
        nifty=None,
        sensex=_band(expiry="2026-09-17", atm=76000.0, lot_size=20),
        nifty_spot=None,
        sensex_spot=76000.0,
        slot=slot_1430,
        path=path,
    )
    assert todays_mark(path, now=slot_1430)["baseline_slot"] == "14:30"
    assert should_capture_baseline(_at("2026-09-17 14:45:00"), path=path)


def test_1445_is_fallback_when_1430_missed(tmp_path):
    path = tmp_path / "power_hour_mark.json"
    slot = _at("2026-09-17 14:45:00")
    assert should_capture_baseline(slot, path=path)
    save_baseline(
        nifty=_band(),
        sensex=_band(expiry="2026-09-17", atm=76000.0, lot_size=20),
        nifty_spot=23100.0,
        sensex_spot=76000.0,
        slot=slot,
        path=path,
    )
    mark = todays_mark(path, now=slot)
    assert mark["baseline_slot"] == "14:45"
    assert "SENSEX" in mark["indices"]


def test_stale_mark_from_yesterday_is_ignored(tmp_path):
    path = tmp_path / "power_hour_mark.json"
    save_baseline(
        nifty=_band(),
        sensex=None,
        nifty_spot=23100.0,
        sensex_spot=None,
        slot=_at("2026-09-16 14:30:00"),
        path=path,
    )
    assert todays_mark(path, now=_at("2026-09-17 15:00:00")) == {}
    assert should_capture_baseline(_at("2026-09-17 14:30:00"), path=path)


def test_power_hour_report_is_vs_1430_not_last_scan(tmp_path):
    path = tmp_path / "power_hour_mark.json"
    nifty_open = _band(call_oi=100_000, put_oi=200_000, call_ltp=100.0, put_ltp=80.0)
    sensex_open = _band(
        expiry="2026-09-17",
        atm=76000.0,
        lot_size=20,
        call_oi=40_000,
        put_oi=50_000,
        call_ltp=200.0,
        put_ltp=180.0,
    )
    save_baseline(
        nifty=nifty_open,
        sensex=sensex_open,
        nifty_spot=23100.0,
        sensex_spot=76000.0,
        slot=_at("2026-09-17 14:30:00"),
        path=path,
    )
    mark = todays_mark(path, now=_at("2026-09-17 15:00:00"))

    # After 14:30: Nifty CE WRITE, PE BUY; Sensex CE BUY, PE WRITE
    nifty_now = _band(call_oi=113_000, put_oi=213_000, call_ltp=90.0, put_ltp=95.0)
    sensex_now = _band(
        expiry="2026-09-17",
        atm=76000.0,
        lot_size=20,
        call_oi=40_000 + 2_000,
        put_oi=50_000 + 4_000,
        call_ltp=220.0,
        put_ltp=160.0,
    )
    nifty_flow = classify_band_flow(nifty_now, mark=mark["indices"]["NIFTY"])
    sensex_flow = classify_band_flow(sensex_now, mark=mark["indices"]["SENSEX"])
    assert nifty_flow.total_lots("CE", "WRITE") == 200  # 13000/65
    assert nifty_flow.total_lots("PE", "BUY") == 200
    assert sensex_flow.total_lots("CE", "BUY") == 100  # 2000/20
    assert sensex_flow.total_lots("PE", "WRITE") == 200

    text = format_power_hour_report(
        end_label="15:00",
        nifty=nifty_now,
        sensex=sensex_now,
        nifty_spot=23110.0,
        sensex_spot=76020.0,
        mark=mark,
    )
    assert "POWER HOUR 14:30 → 15:00 IST" in text
    assert "extra scan" in text
    assert "NIFTY since 14:30  CE BUY 0 / WRITE 200   PE BUY 200 / WRITE 0  (lots)" in text
    assert "SENSEX since 14:30  CE BUY 100 / WRITE 0   PE BUY 0 / WRITE 200  (lots)" in text
    html = scan_message_html(text)
    assert "<b>NIFTY since 14:30  CE BUY 0 / WRITE 200   PE BUY 200 / WRITE 0  (lots)</b>" in html
    assert "<b>SENSEX since 14:30  CE BUY 100 / WRITE 0   PE BUY 0 / WRITE 200  (lots)</b>" in html
    text_1510 = format_power_hour_report(
        end_label="15:10",
        nifty=nifty_now,
        sensex=sensex_now,
        nifty_spot=23110.0,
        sensex_spot=76020.0,
        mark=mark,
    )
    assert "POWER HOUR 14:30 → 15:10 IST" in text_1510
    assert "NIFTY since 14:30  CE BUY 0 / WRITE 200" in text_1510
    assert "SENSEX since 14:30  CE BUY 100 / WRITE 0" in text_1510
