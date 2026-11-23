"""Public scan desk. Built from saved readings. No keys, tokens, or secrets."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.fut_flow import (
    DAY_LABELS,
    classify_fut_flow,
    day_open_path,
    format_fut_day_alert,
    mark_path,
    reading_from_mark,
)
from src.oi_flow import classify_band_flow, interval_totals_line
from src.master_strategy import BOOK_PATH as MASTER_BOOK_PATH
from src.master_strategy import NAME as MASTER_NAME
from src.option_buy_strategy import BOOK_PATH as OPTION_BUY_BOOK_PATH
from src.option_buy_strategy import NAME as OPTION_BUY_NAME
from src.paper_decision import latest_decision
from src.paths import ROOT
from src.pcr import BandPcr, StrikePcr
from src.power_hour import is_report_slot
from src.scan_history import day_is_current, prune_old_scans, _parse_day
from src.scan_slots import IST

HISTORY_KEYS = ("nifty_oi", "sensex_oi", "nifty_fut", "sensex_fut")
SESSIONS = "fut_sessions.json"
SESSION_NAMES = ("NIFTY", "SENSEX")


def merge_published_desk(current: dict | None, incoming: dict) -> dict:
    """Keep every slot already on the public page.

    A futures publish from the standby repo must not wipe a newer option scan.
    """
    if not isinstance(current, dict) or not current:
        return incoming
    merged = dict(incoming)
    for key in HISTORY_KEYS:
        merged[key] = _merge_cards(current.get(key), incoming.get(key))
    current_at = str(current.get("generated_at") or "")
    incoming_at = str(incoming.get("generated_at") or "")
    merged["generated_at"] = max(current_at, incoming_at)
    return merged


def _merge_cards(older, newer) -> list:
    chosen: dict[tuple[str, str], dict] = {}
    for row in list(older or []) + list(newer or []):
        if not isinstance(row, dict):
            continue
        key = (str(row.get("day") or ""), str(row.get("clock") or ""))
        previous = chosen.get(key)
        if previous is None or str(row.get("saved_at") or "") >= str(previous.get("saved_at") or ""):
            chosen[key] = row
    return sorted(
        chosen.values(),
        key=lambda row: (str(row.get("day") or ""), str(row.get("clock") or "")),
        reverse=True,
    )


def build_desk(root: Path = ROOT, today: date | None = None) -> dict:
    """Public desk: OI/fut scans + the two Master paper strategies only."""
    history_dir = root / "data" / "scan_history"
    prune_old_scans(history_dir, today)
    history = _load_history(history_dir, today)
    desk = {
        "generated_at": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST"),
        "nifty_oi": _option_cards(history.get("nifty_oi") or []),
        "sensex_oi": _option_cards(history.get("sensex_oi") or []),
        "nifty_fut": _future_cards(history.get("nifty_fut") or []),
        "sensex_fut": _future_cards(history.get("sensex_fut") or []),
        "master_strategy": _master_synth(root / "data" / MASTER_BOOK_PATH.name),
        "option_buy": _option_buy(root / "data" / OPTION_BUY_BOOK_PATH.name),
        "paper_decision": latest_decision(root / "data" / "paper_decision.json"),
        "fut_days": _fut_days(root, today),
    }
    return _strip_tokens(desk)


def session_card(
    name: str,
    opened: dict,
    closed: dict,
    *,
    when: datetime | None = None,
) -> dict | None:
    """The whole-session futures alert, or None when the day is not closed yet."""
    start_at = str(opened.get("updated_at") or "")
    end_at = str(closed.get("updated_at") or "")
    if len(start_at) < 16 or len(end_at) < 16:
        return None
    if start_at[:10] != end_at[:10] or end_at[11:16] < "15:00":
        return None
    try:
        reading = reading_from_mark(
            opened,
            price=float(closed["price"]),
            oi=int(closed["oi"]),
            lot_size=int(closed["lot_size"]),
            tradingsymbol=str(closed["tradingsymbol"]),
            expiry=str(closed["expiry"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if reading.prev_price is None or reading.prev_oi is None:
        return None
    if when is None:
        when = datetime.strptime(end_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    lines = format_fut_day_alert(
        reading,
        when=when,
        name=name,
        start_at=start_at,
        end_at=end_at,
    ).split("\n")
    return {
        "name": name.strip().upper(),
        "day": start_at[:10],
        "label": DAY_LABELS.get(reading.label, reading.label),
        "lines": lines,
    }


def save_session(root: Path, card: dict) -> None:
    """Keep the last two sessions so tomorrow morning still shows today's alert."""
    path = root / "data" / SESSIONS
    sessions = [
        row
        for row in _load_sessions(path)
        if (row.get("name"), row.get("day")) != (card.get("name"), card.get("day"))
    ]
    sessions.append(card)
    anchor = _parse_day(str(card.get("day") or "")) or date.today()
    sessions = [row for row in sessions if _session_visible(row, anchor)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": sessions}, indent=2) + "\n", encoding="utf-8")


def _fut_days(root: Path, today: date | None) -> list[dict]:
    cards: dict[tuple[str, str], dict] = {}
    for row in _load_sessions(root / "data" / SESSIONS):
        if _session_visible(row, today):
            cards[(str(row.get("name")), str(row.get("day")))] = row
    for name in SESSION_NAMES:
        opened = _read_json(root / day_open_path(name))
        closed = _read_json(root / mark_path(name))
        if not opened or not closed:
            continue
        card = session_card(name, opened, closed)
        if card and _session_visible(card, today):
            cards.setdefault((card["name"], card["day"]), card)
    return sorted(
        cards.values(),
        key=lambda row: (str(row.get("day")), 0 if row.get("name") == "SENSEX" else 1),
        reverse=True,
    )


def _session_visible(row: dict, today: date | None) -> bool:
    scanned = _parse_day(str(row.get("day") or ""))
    return scanned is not None and day_is_current(scanned, today)


def _load_sessions(path: Path) -> list[dict]:
    body = _read_json(path)
    rows = body.get("sessions") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("lines")]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


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


def _dmy(value: str) -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}-{text[5:7]}-{text[0:4]}"
    return text


def _empty_paper(name: str) -> dict:
    return {
        "name": name,
        "saved": False,
        "started_on": "",
        "starting_capital": None,
        "capital": None,
        "realised": None,
        "updated_at": None,
        "flat": True,
        "open": None,
        "rows": [],
    }


def _master_synth_row(trade: dict, event: str) -> dict:
    entry = event == "Entry"
    return {
        "date": _dmy(trade.get("entry_date")),
        "event": event,
        "time": trade.get("entry_clock") if entry else trade.get("exit_clock"),
        "exit_date": "" if entry else _dmy(trade.get("exit_date")),
        "script": trade.get("script"),
        "tier": trade.get("tier"),
        "side": trade.get("side"),
        "strike": trade.get("strike"),
        "expiry": trade.get("expiry"),
        "call_symbol": trade.get("call_symbol"),
        "put_symbol": trade.get("put_symbol"),
        "call_price": trade.get("call_entry") if entry else trade.get("call_exit"),
        "put_price": trade.get("put_entry") if entry else trade.get("put_exit"),
        "pcr": trade.get("pcr"),
        "pcr_rank": trade.get("pcr_rank"),
        "points": None if entry else trade.get("points"),
        "lots": trade.get("lots"),
        "net": None if entry else trade.get("net"),
        "exit_why": "" if entry else (trade.get("exit_why") or ""),
        "partial": trade.get("partial") if not entry else None,
        "capital_before": trade.get("capital_before"),
        "capital_after": None if entry else trade.get("capital_after"),
    }


def _master_synth(path: Path) -> dict:
    empty = _empty_paper(MASTER_NAME)
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, dict):
        return empty
    position = raw.get("position") if isinstance(raw.get("position"), dict) else None
    open_row = None
    rem = float((position or {}).get("remaining_frac") or 0)
    if position and rem > 1e-9:
        open_row = _master_synth_row(position, "Entry")
        open_row["exit_clock"] = position.get("exit_clock")
        open_row["remaining_frac"] = rem
        open_row["partial_done"] = bool(position.get("partial_done"))
    rows: list[dict] = []
    if open_row:
        rows.append(open_row)
    for trade in reversed(list(raw.get("closed") or [])):
        if not isinstance(trade, dict):
            continue
        rows.append(_master_synth_row(trade, "Entry"))
        rows.append(_master_synth_row(trade, "Exit"))
    return {
        "name": str(raw.get("name") or MASTER_NAME),
        "saved": True,
        "started_on": _dmy(raw.get("started_on")),
        "starting_capital": raw.get("starting_capital"),
        "capital": raw.get("capital"),
        "realised": raw.get("realised_pnl"),
        "updated_at": raw.get("updated_at"),
        "flat": open_row is None,
        "open": open_row,
        "rows": rows,
    }


def _option_buy_row(trade: dict, event: str) -> dict:
    entry = event == "Entry"
    return {
        "date": _dmy(trade.get("entry_date")),
        "event": event,
        "time": trade.get("entry_clock") if entry else trade.get("exit_clock"),
        "exit_date": "" if entry else _dmy(trade.get("exit_date")),
        "script": trade.get("script"),
        "tier": trade.get("tier") or "base",
        "signal_side": trade.get("signal_side"),
        "opt_side": trade.get("opt_side"),
        "symbol": trade.get("symbol"),
        "strike": trade.get("strike"),
        "expiry": trade.get("expiry"),
        "price": trade.get("entry") if entry else trade.get("exit"),
        "slip": trade.get("slip"),
        "pcr": trade.get("pcr"),
        "pcr_rank": trade.get("pcr_rank"),
        "points": None if entry else trade.get("points"),
        "lots": trade.get("lots"),
        "gross": None if entry else trade.get("gross"),
        "charges": None if entry else trade.get("charges"),
        "net": None if entry else trade.get("net"),
        "exit_why": "" if entry else (trade.get("exit_why") or ""),
        "capital_before": trade.get("capital_before"),
        "capital_after": None if entry else trade.get("capital_after"),
    }


def _option_buy(path: Path) -> dict:
    empty = _empty_paper(OPTION_BUY_NAME)
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, dict):
        return empty
    position = raw.get("position") if isinstance(raw.get("position"), dict) else None
    open_row = None
    if position and int(position.get("lots") or 0) > 0:
        open_row = _option_buy_row(position, "Entry")
        open_row["exit_clock"] = position.get("exit_clock")
    rows: list[dict] = []
    if open_row:
        rows.append(open_row)
    for trade in reversed(list(raw.get("closed") or [])):
        if not isinstance(trade, dict):
            continue
        rows.append(_option_buy_row(trade, "Entry"))
        rows.append(_option_buy_row(trade, "Exit"))
    return {
        "name": str(raw.get("name") or OPTION_BUY_NAME),
        "saved": True,
        "started_on": _dmy(raw.get("started_on")),
        "starting_capital": raw.get("starting_capital"),
        "capital": raw.get("capital"),
        "realised": raw.get("realised_pnl"),
        "updated_at": raw.get("updated_at"),
        "flat": open_row is None,
        "open": open_row,
        "rows": rows,
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
