from pathlib import Path

from src.oi_flow import (
    classify_atm_flow,
    classify_band_flow,
    classify_leg,
    format_band_flow,
    format_flow_caption,
    save_premium_mark,
)
from src.pcr import BandPcr, StrikePcr
from src.telegram_client import scan_message_html


def _band(
    *,
    call_ltp=100.0,
    put_ltp=80.0,
    call_oi=100_000,
    put_oi=200_000,
    call_d=6500,
    put_d=13000,
    strikes: list[StrikePcr] | None = None,
) -> BandPcr:
    if strikes is None:
        strikes = [
            StrikePcr(
                label="ATM",
                strike=23100.0,
                call_oi=call_oi,
                put_oi=put_oi,
                call_oi_change=call_d,
                put_oi_change=put_d,
                call_ltp=call_ltp,
                put_ltp=put_ltp,
            )
        ]
    return BandPcr(
        expiry="2026-09-22",
        atm=23100.0,
        step=50.0,
        lot_size=65,
        strikes=strikes,
    )


def test_tiny_premium_move_stays_oi_build():
    # 1.0 on a ₹100 premium is only 1% (< 1.5% deadzone) → not BUY
    flow = classify_leg(
        leg="PE",
        strike=23100,
        oi_change=13_000,
        premium=101.0,
        prev_premium=100.0,
        lot_size=65,
    )
    assert flow.label == "OI BUILD"
    assert flow.premium_change == 1.0


def test_premium_just_above_deadzone_is_buy():
    # max(₹2, 1.5% of 100) = ₹2 → Δ2.0 clears
    flow = classify_leg(
        leg="PE",
        strike=23100,
        oi_change=13_000,
        premium=102.0,
        prev_premium=100.0,
        lot_size=65,
    )
    assert flow.label == "BUY"


def test_cheap_option_needs_rupee_floor():
    # 1.5% of ₹20 = ₹0.30, but floor is ₹2 — Δ1.50 stays OI BUILD
    flow = classify_leg(
        leg="CE",
        strike=23500,
        oi_change=6_500,
        premium=18.5,
        prev_premium=20.0,
        lot_size=65,
    )
    assert flow.label == "OI BUILD"
    flow2 = classify_leg(
        leg="CE",
        strike=23500,
        oi_change=6_500,
        premium=17.5,
        prev_premium=20.0,
        lot_size=65,
    )
    assert flow2.label == "WRITE"


def test_oi_up_premium_down_is_write():
    flow = classify_leg(
        leg="CE",
        strike=23100,
        oi_change=6_500,
        premium=90.0,
        prev_premium=100.0,
        lot_size=65,
    )
    assert flow.label == "WRITE"
    assert flow.oi_lots == 100


def test_without_premium_mark_shows_oi_build(tmp_path: Path):
    mark = tmp_path / "mark.json"
    band = _band()
    flows = classify_atm_flow(band, path=mark)
    assert flows is not None
    call, put = flows
    assert call.label == "OI BUILD"
    assert put.label == "OI BUILD"

    save_premium_mark(band, path=mark)
    later = _band(call_ltp=110.0, put_ltp=95.0, call_oi=113_000, put_oi=219_500)
    flows2 = classify_atm_flow(later, path=mark)
    assert flows2 is not None
    assert flows2[0].label == "BUY"
    assert flows2[1].label == "BUY"
    assert flows2[0].oi_lots == 200  # 13000/65
    assert flows2[1].oi_lots == 300  # 19500/65


def test_per_strike_scan_totals(tmp_path: Path):
    mark = tmp_path / "mark.json"
    first = _band(
        strikes=[
            StrikePcr(
                label="ITM1",
                strike=23050.0,
                call_oi=50_000,
                put_oi=80_000,
                call_oi_change=0,
                put_oi_change=0,
                call_ltp=120.0,
                put_ltp=40.0,
            ),
            StrikePcr(
                label="ATM",
                strike=23100.0,
                call_oi=100_000,
                put_oi=200_000,
                call_oi_change=0,
                put_oi_change=0,
                call_ltp=100.0,
                put_ltp=80.0,
            ),
        ]
    )
    save_premium_mark(first, path=mark)

    # ITM1: CE OI↑ prem↓ = WRITE; PE OI↑ prem↑ = BUY
    # ATM: CE OI↑ prem↑ = BUY; PE OI↑ prem↓ = WRITE
    second = BandPcr(
        expiry="2026-09-22",
        atm=23100.0,
        step=50.0,
        lot_size=65,
        strikes=[
            StrikePcr(
                label="ITM1",
                strike=23050.0,
                call_oi=50_000 + 6_500,
                put_oi=80_000 + 13_000,
                call_oi_change=0,
                put_oi_change=0,
                call_ltp=110.0,
                put_ltp=50.0,
            ),
            StrikePcr(
                label="ATM",
                strike=23100.0,
                call_oi=100_000 + 13_000,
                put_oi=200_000 + 6_500,
                call_oi_change=0,
                put_oi_change=0,
                call_ltp=110.0,
                put_ltp=70.0,
            ),
        ],
    )
    flow = classify_band_flow(second, path=mark)
    assert flow.compared_to is not None
    assert flow.total_lots("CE", "BUY") == 200
    assert flow.total_lots("CE", "WRITE") == 100
    assert flow.total_lots("PE", "BUY") == 200
    assert flow.total_lots("PE", "WRITE") == 100
    text = format_band_flow(second, flow)
    assert "CE BUY 200 / WRITE 100" in text
    assert "PE BUY 200 / WRITE 100" in text
    assert "Lbl    Strike Side Flow" in text
    html = scan_message_html(text)
    assert "<b>New this interval  CE BUY 200 / WRITE 100   PE BUY 200 / WRITE 100  (lots)</b>" in html
    assert "<pre>" in html
    assert "ITM1   23,050 CE   WRITE" in text
    assert "ITM1   23,050 PE   BUY" in text
    assert "ATM    23,100 CE   BUY" in text
    assert "ATM    23,100 PE   WRITE" in text
    caption = format_flow_caption(flow)
    assert caption.startswith("New this interval")
    assert "23050 ITM1 CE WRITE · PE BUY" in caption
    assert "23100 ATM CE BUY · PE WRITE" in caption
