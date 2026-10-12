"""Public scan desk. Built from saved readings. No keys, tokens, or secrets."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.fut_flow import classify_fut_flow
from src.oi_flow import classify_band_flow, interval_totals_line
from src.paths import ROOT
from src.pcr import BandPcr, StrikePcr
from src.power_hour import is_report_slot
from src.scan_history import day_is_current, prune_old_scans, _parse_day

HISTORY_KEYS = ("nifty_oi", "sensex_oi", "nifty_fut", "sensex_fut")
BOOKS = {"nifty": "nifty_paper_book.json", "sensex": "sensex_paper_book.json"}


def build_desk(root: Path = ROOT, today: date | None = None) -> dict:
    history_dir = root / "data" / "scan_history"
    prune_old_scans(history_dir, today)
    history = _load_history(history_dir, today)
    desk = {
        "generated_at": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST"),
        "nifty_oi": _option_cards(history.get("nifty_oi") or []),
        "sensex_oi": _option_cards(history.get("sensex_oi") or []),
        "nifty_fut": _future_cards(history.get("nifty_fut") or []),
        "sensex_fut": _future_cards(history.get("sensex_fut") or []),
        "positions": {
            "nifty": _book(root / "data" / BOOKS["nifty"]),
            "sensex": _book(root / "data" / BOOKS["sensex"]),
        },
    }
    return _strip_tokens(desk)


def _load_history(directory: Path, today: date | None = None) -> dict[str, list]:
    merged: dict[str, list] = {key: [] for key in HISTORY_KEYS}
    if not directory.exists():
        return merged
    for path in sorted(directory.glob("*.json")):
        scanned = _parse_day(path.stem)
        if scanned is None or not day_is_current(scanned, today):
            continue
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(body, dict):
            continue
        for key in HISTORY_KEYS:
            rows = [row for row in (body.get(key) or []) if isinstance(row, dict)]
            merged[key].extend(rows)
    for key in HISTORY_KEYS:
        merged[key].sort(key=lambda row: str(row.get("updated_at") or row.get("slot") or ""))
    return merged


def _option_cards(rows: list[dict]) -> list[dict]:
    cards: list[dict] = []
    previous: dict | None = None
    by_day: dict[str, dict] = {}
    for row in rows:
        slot = str(row.get("slot") or "")
        if len(slot) >= 16 and slot.endswith("14:30"):
            by_day[slot[:10]] = row
        band = _band_from_row(row)
        if band is None:
            previous = row
            continue
        flow = classify_band_flow(band, mark=previous or {})
        card = _option_card(row, band, flow, versus="This interval")
        slot_at = _slot_datetime(row)
        baseline = by_day.get(card["day"]) if slot_at and is_report_slot(slot_at) else None
        if baseline and baseline is not row:
            base_band = _band_from_row(row)
            if base_band is not None:
                wide = classify_band_flow(base_band, mark=baseline)
                card["since_1430"] = _option_card(row, base_band, wide, versus="Since 14:30")
        cards.append(card)
        previous = row
    cards.reverse()
    return cards


def _option_card(row: dict, band: BandPcr, flow, *, versus: str) -> dict:
    clock, day = _clock_and_day(row)
    strikes = []
    for item in flow.strikes:
        strikes.append(
            {
                "label": item.label,
                "strike": f"{item.strike:,.0f}",
                "call": _leg(item.call),
                "put": _leg(item.put),
            }
        )
    return {
        "day": day,
        "clock": clock,
        "saved_at": str(row.get("updated_at") or ""),
        "expiry": band.expiry,
        "atm": f"{band.atm:,.0f}",
        "lot_size": band.lot_size,
        "compared_to": flow.compared_to,
        "totals": interval_totals_line(flow, prefix=versus),
        "baseline": flow.compared_to is None,
        "strikes": strikes,
    }


def _leg(flow) -> dict:
    lots = flow.oi_lots
    return {
        "label": flow.label,
        "lots": None if lots is None else f"{lots:+,}",
        "prem": None if flow.premium is None else f"{flow.premium:,.2f}",
        "dprem": None if flow.premium_change is None else f"{flow.premium_change:+.2f}",
    }


def _future_cards(rows: list[dict]) -> list[dict]:
    cards: list[dict] = []
    previous: dict | None = None
    for row in rows:
        try:
            price = float(row["price"])
            oi = int(row["oi"])
            lot_size = int(row["lot_size"])
        except (KeyError, TypeError, ValueError):
            previous = row
            continue
        reading = classify_fut_flow(
            price=price,
            oi=oi,
            lot_size=lot_size,
            prev_price=float(previous["price"]) if previous and previous.get("price") is not None else None,
            prev_oi=int(previous["oi"]) if previous and previous.get("oi") is not None else None,
            tradingsymbol=str(row.get("tradingsymbol") or ""),
            expiry=str(row.get("expiry") or ""),
            compared_to=str(previous.get("updated_at") or "") if previous else None,
        )
        clock, day = _clock_and_day(row)
        if reading.prev_price is None:
            price_line = f"Price {reading.price:,.2f}"
        else:
            price_line = (
                f"Price {reading.prev_price:,.2f} → {reading.price:,.2f}  "
                f"({reading.price - reading.prev_price:+,.2f})"
            )
        oi_now = int(reading.oi / reading.lot_size) if reading.lot_size else None
        if reading.prev_oi is None or oi_now is None:
            oi_line = f"Open interest {oi_now:,} lots" if oi_now is not None else "Open interest n/a"
        else:
            oi_prev = int(reading.prev_oi / reading.lot_size)
            oi_line = f"Open interest {oi_prev:,} → {oi_now:,} lots"
        lots = None if reading.lots is None else f"{reading.lots:,} lots"
        cards.append(
            {
                "day": day,
                "clock": clock,
                "saved_at": str(row.get("updated_at") or ""),
                "contract": reading.tradingsymbol,
                "expiry": reading.expiry,
                "label": reading.label,
                "lots": lots,
                "since": reading.compared_to,
                "price": price_line,
                "oi": oi_line,
            }
        )
        previous = row
    cards.reverse()
    return cards


def _book(path: Path) -> dict:
    if not path.exists():
        return {"saved": False, "flat": True, "realised": None, "updated_at": None, "open": None, "closed": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"saved": False, "flat": True, "realised": None, "updated_at": None, "open": None, "closed": []}
    position = raw.get("position") if isinstance(raw.get("position"), dict) else None
    open_row = None
    if position and int(position.get("lots") or 0) > 0:
        open_row = {
            "direction": position.get("direction"),
            "tradingsymbol": position.get("tradingsymbol"),
            "lots": position.get("lots"),
            "entry_price": position.get("entry_price"),
            "entry_time": position.get("entry_time"),
            "stop_loss_price": position.get("stop_loss_price"),
            "first_target_price": position.get("first_target_price"),
        }
    closed = []
    for trade in reversed(list(raw.get("closed") or [])):
        if not isinstance(trade, dict):
            continue
        closed.append(
            {
                "direction": trade.get("direction"),
                "tradingsymbol": trade.get("tradingsymbol"),
                "lots": trade.get("lots"),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "pnl": trade.get("pnl"),
                "exit_reason": trade.get("exit_reason"),
            }
        )
    realised = raw.get("realised_pnl")
    return {
        "saved": True,
        "flat": open_row is None,
        "realised": None if realised is None else float(realised),
        "updated_at": raw.get("updated_at"),
        "open": open_row,
        "closed": closed,
    }


def _band_from_row(row: dict) -> BandPcr | None:
    try:
        lot_size = int(row["lot_size"])
        atm = float(row["atm"])
    except (KeyError, TypeError, ValueError):
        return None
    strikes: list[StrikePcr] = []
    for item in row.get("strikes") or []:
        if not isinstance(item, dict):
            continue
        try:
            strikes.append(
                StrikePcr(
                    label=str(item.get("label") or ""),
                    strike=float(item["strike"]),
                    call_oi=int(item["call_oi"]),
                    put_oi=int(item["put_oi"]),
                    call_ltp=None if item.get("call_ltp") is None else float(item["call_ltp"]),
                    put_ltp=None if item.get("put_ltp") is None else float(item["put_ltp"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not strikes:
        return None
    step = abs(strikes[1].strike - strikes[0].strike) if len(strikes) > 1 else 0.0
    return BandPcr(
        expiry=str(row.get("expiry") or ""),
        atm=atm,
        step=step,
        lot_size=lot_size,
        strikes=strikes,
    )


def _clock_and_day(row: dict) -> tuple[str, str]:
    slot = str(row.get("slot") or row.get("updated_at") or "")
    if len(slot) >= 16:
        return slot[11:16], slot[:10]
    updated = str(row.get("updated_at") or "")
    if len(updated) >= 16:
        return updated[11:16], updated[:10]
    return "", ""


def _slot_datetime(row: dict) -> datetime | None:
    slot = str(row.get("slot") or "")
    try:
        return datetime.strptime(slot, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _strip_tokens(value):
    if isinstance(value, dict):
        return {key: _strip_tokens(item) for key, item in value.items() if key != "token"}
    if isinstance(value, list):
        return [_strip_tokens(item) for item in value]
    return value
