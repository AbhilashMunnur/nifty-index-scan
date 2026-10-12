"""Keep every scan of the day. Drop a day once it is two days old.

The latest-mark files stay the live baseline. Only the day history is removed.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.paths import ROOT

HISTORY_DIR = ROOT / "data" / "scan_history"
KEEP_DAYS = 2
IST = ZoneInfo("Asia/Kolkata")

_KINDS = {
    "band_scan_mark.json": "nifty_oi",
    "sensex_band_scan_mark.json": "sensex_oi",
    "nifty_fut_flow_mark.json": "nifty_fut",
    "sensex_fut_flow_mark.json": "sensex_fut",
}


def history_kind(path: Path) -> str | None:
    return _KINDS.get(path.name)


def slot_key(when: datetime) -> str:
    """One row per scan time. 15:10, 15:15, and 15:40 stay off the 15-minute grid."""
    minute = (when.minute // 15) * 15
    if when.hour == 15 and 10 <= when.minute <= 14:
        minute = 10
    elif when.hour == 15 and 15 <= when.minute <= 19:
        minute = 15
    elif when.hour == 15 and 40 <= when.minute <= 50:
        minute = 40
    return f"{when:%Y-%m-%d} {when.hour:02d}:{minute:02d}"


def history_path(day: str) -> Path:
    return HISTORY_DIR / f"{day}.json"


def append_scan(kind: str, reading: dict) -> None:
    """Store this reading. A second save of the same slot replaces the first."""
    updated = str(reading.get("updated_at") or "")
    try:
        when = datetime.strptime(updated, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return
    day = when.strftime("%Y-%m-%d")
    path = history_path(day)
    body: dict = {"date": day}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                body = loaded
        except (OSError, json.JSONDecodeError):
            body = {"date": day}
    rows = [row for row in (body.get(kind) or []) if isinstance(row, dict)]
    stored = {**reading, "slot": slot_key(when)}
    for index, row in enumerate(rows):
        if row.get("slot") == stored["slot"]:
            rows[index] = stored
            break
    else:
        rows.append(stored)
    rows.sort(key=lambda row: str(row.get("updated_at") or ""))
    body["date"] = day
    body[kind] = rows
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    prune_old_scans()


def today_ist(today: date | None = None) -> date:
    return today or datetime.now(IST).date()


def day_is_current(day: date, today: date | None = None) -> bool:
    """True for the scan day and the day after it. Older days are gone."""
    return (today_ist(today) - day).days < KEEP_DAYS


def prune_old_scans(directory: Path | None = None, today: date | None = None) -> list[Path]:
    """Delete history files that are two days old or older."""
    folder = directory or HISTORY_DIR
    if not folder.exists():
        return []
    removed: list[Path] = []
    for path in folder.glob("*.json"):
        scanned = _parse_day(path.stem)
        if scanned is None or day_is_current(scanned, today):
            continue
        path.unlink()
        removed.append(path)
    return removed


def _parse_day(name: str) -> date | None:
    try:
        return datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None
