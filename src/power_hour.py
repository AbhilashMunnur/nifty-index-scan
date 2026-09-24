"""Extra 15:00 / 15:10 / 15:15 scans: Nifty + Sensex CE/PE BUY vs WRITE since 14:30 IST."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path

from src.oi_flow import classify_band_flow, format_band_flow
from src.paths import ROOT
from src.pcr import BandPcr
from src.scan_slots import now_ist

MARKER = ROOT / "data" / "power_hour_mark.json"
BASELINE_SLOT = time(14, 30)
CAPTURE_SLOTS = (time(14, 30), time(14, 45))


def slot_clock(slot: datetime | None) -> time | None:
    if slot is None:
        return None
    return time(slot.hour, slot.minute)


def is_capture_slot(slot: datetime | None) -> bool:
    clock = slot_clock(slot)
    return clock in CAPTURE_SLOTS


def is_report_slot(slot: datetime | None) -> bool:
    clock = slot_clock(slot)
    if clock == time(15, 0):
        return True
    # 15:10 and 15:15 are Tuesday and Thursday only.
    if clock in (time(15, 10), time(15, 15)) and slot is not None:
        return slot.weekday() in (1, 3)
    return False


def load_mark(path: Path = MARKER) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def todays_mark(path: Path = MARKER, now: datetime | None = None) -> dict:
    mark = load_mark(path)
    today = now_ist(now).date().isoformat()
    if mark.get("date") != today:
        return {}
    return mark


def _index_payload(band: BandPcr, spot: float | None) -> dict:
    strikes = [
        {
            "label": row.label,
            "strike": row.strike,
            "call_oi": row.call_oi,
            "put_oi": row.put_oi,
            "call_ltp": row.call_ltp,
            "put_ltp": row.put_ltp,
        }
        for row in band.strikes
    ]
    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expiry": band.expiry,
        "atm": band.atm,
        "lot_size": band.lot_size,
        "strikes": strikes,
        "spot": spot,
    }


def save_baseline(
    *,
    nifty: BandPcr | None,
    sensex: BandPcr | None,
    nifty_spot: float | None,
    sensex_spot: float | None,
    slot: datetime,
    path: Path = MARKER,
) -> dict:
    """Store 14:30 (or 14:45 fallback) Nifty + Sensex OI/premium."""
    clock = slot_clock(slot) or BASELINE_SLOT
    payload = {
        "date": slot.date().isoformat(),
        "baseline_slot": clock.strftime("%H:%M"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "indices": {},
    }
    if nifty is not None:
        payload["indices"]["NIFTY"] = _index_payload(nifty, nifty_spot)
    if sensex is not None:
        payload["indices"]["SENSEX"] = _index_payload(sensex, sensex_spot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def should_capture_baseline(slot: datetime | None, path: Path = MARKER) -> bool:
    if not is_capture_slot(slot):
        return False
    assert slot is not None
    existing = todays_mark(path, now=slot)
    if not existing:
        return True
    has_nifty = bool((existing.get("indices") or {}).get("NIFTY"))
    clock = slot_clock(slot)
    # Always refresh if the true 14:30 slot is running.
    if clock == BASELINE_SLOT:
        return True
    # 14:45 fallback only when 14:30 missed or stored no Nifty chain.
    return not (existing.get("baseline_slot") == "14:30" and has_nifty)


def format_power_hour_report(
    *,
    end_label: str,
    nifty: BandPcr | None,
    sensex: BandPcr | None,
    nifty_spot: float | None,
    sensex_spot: float | None,
    mark: dict,
) -> str:
    baseline = mark.get("baseline_slot") or "14:30"
    lines = [
        f"—— POWER HOUR {baseline} → {end_label} IST ——",
        "Nifty + Sensex CE/PE BUY vs WRITE vs the 14:30 baseline (option premium, not spot).",
        "This is an extra scan; the normal 15-min strategy scan is unchanged.",
        "",
    ]
    for symbol, band, spot in (
        ("NIFTY", nifty, nifty_spot),
        ("SENSEX", sensex, sensex_spot),
    ):
        prior = (mark.get("indices") or {}).get(symbol) or {}
        if band is None:
            lines.append(f"{symbol}: chain unavailable")
            lines.append("")
            continue
        flow = classify_band_flow(band, mark=prior)
        spot_text = "n/a" if spot is None else f"{spot:,.2f}"
        heading = f"—— {symbol} since {baseline}  spot {spot_text}  ATM {band.atm:,.0f} ——"
        prefix = f"{symbol} since {baseline}"
        lines.append(
            format_band_flow(band, flow, heading=heading, totals_prefix=prefix)
        )
        lines.append("")
    return "\n".join(lines).rstrip()
