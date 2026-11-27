"""Why today's paper desks did or did not take a position.

Written once the 14:00 rank check has run. Later scans the same day replace
it with the latest check. The public page reads this file.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from src.master_strategy import BASE, FILLER_HI, FILLER_LO
from src.paths import ROOT

PATH = ROOT / "data" / "paper_decision.json"
KEEP_DAYS = 20


def script_reason(script: str, rank: float | None, tier: str | None, side: str | None) -> str:
    name = script[:1] + script[1:].lower() if script != "NIFTY" else "Nifty"
    if script == "SENSEX":
        name = "Sensex"
    if script == "BANKNIFTY":
        name = "BankNifty"
    if rank is None:
        return f"{name} rank is not ready."
    shown = f"{float(rank) * 100:.1f}%"
    if tier == "base" and side:
        return f"{name} {shown} is a base {side}."
    if tier == "filler" and side:
        return f"{name} {shown} is a filler {side}."
    rule = BASE.get(script.upper())
    if not rule:
        return f"{name} {shown} is not a traded script."
    _, lo, hi = rule
    return (
        f"{name} {shown} is outside base {lo * 100:.0f}/{hi * 100:.0f} "
        f"and filler {FILLER_LO * 100:.0f}/{FILLER_HI * 100:.0f}."
    )


def summarise(book_name: str, taken: bool, readings: list[dict], trade: dict | None, note: str) -> str:
    if taken and trade:
        rank = trade.get("pcr_rank")
        shown = f"{float(rank) * 100:.1f}%" if rank is not None else "n/a"
        script = str(trade.get("script") or "")
        if book_name == "option_buy":
            return (
                f"Position taken. Entry {trade.get('entry_clock') or '14:05'}. "
                f"Base {trade.get('signal_side') or ''} bought {trade.get('opt_side') or ''} {script} at rank {shown}."
            )
        return (
            f"Position taken. Entry {trade.get('entry_clock') or '14:05'}. "
            f"{str(trade.get('tier') or '').title()} {trade.get('side') or ''} {script} at rank {shown}."
        )
    if note:
        return note
    reasons = [script_reason(row["script"], row.get("rank"), row.get("tier"), row.get("side")) for row in readings]
    if book_name == "option_buy":
        bases = [row for row in readings if row.get("tier") == "base"]
        if not bases and any(row.get("tier") == "filler" for row in readings):
            return "No position. Option-buy takes a base rank only. " + " ".join(reasons)
        return "No position. " + " ".join(reasons)
    return "No position. " + " ".join(reasons)


def record_decision(
    day: date,
    checked_at: datetime,
    readings: list[dict],
    *,
    synthetic_taken: bool,
    synthetic_trade: dict | None,
    synthetic_note: str,
    option_taken: bool,
    option_trade: dict | None,
    option_note: str,
    path: Path = PATH,
) -> dict:
    """Store this session's check. A later check on the same day replaces it."""
    body = _load(path)
    days = [row for row in body.get("days") or [] if isinstance(row, dict)]
    iso = day.isoformat()
    row = {
        "day": iso,
        "checked_at": checked_at.strftime("%Y-%m-%d %H:%M:%S"),
        "scripts": [
            {
                "script": item.get("script"),
                "spot": item.get("spot"),
                "pcr": item.get("pcr"),
                "rank": item.get("rank"),
                "tier": item.get("tier") or "",
                "side": item.get("side") or "",
                "expiry": item.get("expiry") or "",
                "reason": script_reason(
                    str(item.get("script") or ""),
                    item.get("rank"),
                    item.get("tier"),
                    item.get("side"),
                ),
            }
            for item in readings
        ],
        "synthetic": {
            "taken": bool(synthetic_taken),
            "summary": summarise("synthetic", synthetic_taken, readings, synthetic_trade, synthetic_note),
        },
        "option_buy": {
            "taken": bool(option_taken),
            "summary": summarise("option_buy", option_taken, readings, option_trade, option_note),
        },
    }
    days = [item for item in days if item.get("day") != iso]
    days.append(row)
    days.sort(key=lambda item: str(item.get("day") or ""))
    body = {"updated_at": row["checked_at"], "days": days[-KEEP_DAYS:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return row


def note_from_lines(lines: list[str]) -> str:
    text = "\n".join(lines)
    if "not published" in text or "entry held" in text:
        return "A signal was present. The 14:05 option open was not out yet, so no position was booked."
    if "entry failed" in text or "quote missing" in text or "legs missing" in text:
        return "A signal was present. The option quote was missing, so no position was booked."
    return ""


def trade_opened_on(book, day: date) -> dict | None:
    iso = day.isoformat()
    position = getattr(book, "position", None)
    if getattr(book, "is_open", False) and isinstance(position, dict) and position.get("entry_date") == iso:
        return position
    for trade in reversed(list(getattr(book, "closed", []) or [])):
        if isinstance(trade, dict) and trade.get("entry_date") == iso:
            return trade
    return None


def add_second_clock(
    at: datetime,
    readings: list[dict],
    synthetic_book,
    option_book,
    synthetic_lines: list[str],
    option_lines: list[str],
    path: Path = PATH,
) -> dict | None:
    """Record the 15:00 base check on today's card. The 14:00 lines stay."""
    if not readings or not (time(15, 0) <= at.time() <= time(15, 25)):
        return None
    body = _load(path)
    days = [row for row in body.get("days") or [] if isinstance(row, dict)]
    iso = at.date().isoformat()
    current = next((row for row in days if row.get("day") == iso), None)
    morning = list((current or {}).get("scripts") or [])
    synthetic_trade = trade_opened_on(synthetic_book, at.date())
    option_trade = trade_opened_on(option_book, at.date())
    row = record_decision(
        at.date(),
        at,
        morning,
        synthetic_taken=synthetic_trade is not None,
        synthetic_trade=synthetic_trade,
        synthetic_note="" if synthetic_trade else note_from_lines(synthetic_lines),
        option_taken=option_trade is not None,
        option_trade=option_trade,
        option_note="" if option_trade else note_from_lines(option_lines),
        path=path,
    )
    saved = _load(path)
    saved_days = [item for item in saved.get("days") or [] if isinstance(item, dict)]
    for item in saved_days:
        if item.get("day") != iso:
            continue
        item["scripts_1500"] = [
            {
                "script": reading.get("script"),
                "spot": reading.get("spot"),
                "pcr": reading.get("pcr"),
                "rank": reading.get("rank"),
                "tier": reading.get("tier") or "",
                "side": reading.get("side") or "",
                "expiry": reading.get("expiry") or "",
                "reason": script_reason(
                    str(reading.get("script") or ""),
                    reading.get("rank"),
                    reading.get("tier"),
                    reading.get("side"),
                ),
            }
            for reading in readings
        ]
        item["checked_at"] = at.strftime("%Y-%m-%d %H:%M:%S")
    saved["updated_at"] = at.strftime("%Y-%m-%d %H:%M:%S")
    saved["days"] = saved_days
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    return row


def commit_session(
    at: datetime,
    readings: list[dict],
    synthetic_book,
    option_book,
    synthetic_lines: list[str],
    option_lines: list[str],
    path: Path = PATH,
) -> dict | None:
    """Save the check when it happens between 14:00 and 14:25."""
    if not readings or not (time(14, 0) <= at.time() <= time(14, 25)):
        return None
    day = at.date()
    synthetic_trade = trade_opened_on(synthetic_book, day)
    option_trade = trade_opened_on(option_book, day)
    return record_decision(
        day,
        at,
        readings,
        synthetic_taken=synthetic_trade is not None,
        synthetic_trade=synthetic_trade,
        synthetic_note="" if synthetic_trade else note_from_lines(synthetic_lines),
        option_taken=option_trade is not None,
        option_trade=option_trade,
        option_note="" if option_trade else note_from_lines(option_lines),
        path=path,
    )


def latest_decision(path: Path = PATH) -> dict | None:
    days = _load(path).get("days") or []
    rows = [row for row in days if isinstance(row, dict)]
    return rows[-1] if rows else None


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}
