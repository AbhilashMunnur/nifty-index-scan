"""Open-interest variants of Loka Kalyanam.

A low put-call ratio follows the day against yesterday's close.
Every other day uses the Loka close. The next session closes at the
check clock when the pair is in profit, and at 14:00 otherwise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

from src.paths import ROOT

PCR_PATH = ROOT / "data" / "loka_oi_pcr.json"
MIN_PAIRS = 9
FALLBACK_CLOCK = "14:00"


@dataclass(frozen=True)
class OiVariant:
    key: str
    name: str
    book_name: str
    journal_name: str
    window: int
    cutoff: float
    check_clock: str
    fallback_clock: str = FALLBACK_CLOCK

    @property
    def book_path(self) -> Path:
        return ROOT / "data" / self.book_name


VARIANTS = (
    OiVariant(
        "loka_oi_120",
        "Loka OI 120",
        "loka_oi_120_book.json",
        "loka_oi_120_journal.xlsx",
        120,
        0.20,
        "10:00",
    ),
    OiVariant(
        "loka_oi_60",
        "Loka OI 60",
        "loka_oi_60_book.json",
        "loka_oi_60_journal.xlsx",
        60,
        0.30,
        "10:15",
    ),
)


def pcr_percentile(values: list[float], window: int) -> float | None:
    """Share of the earlier readings in the window that sit strictly below the latest one."""
    if len(values) < window // 2:
        return None
    sample = values[-window:]
    prior = sample[:-1]
    if not prior:
        return None
    latest = sample[-1]
    return sum(value < latest for value in prior) / len(prior)


def choose_side(
    loka_side: str | None,
    close: float,
    prev_close: float | None,
    percentile: float | None,
    cutoff: float,
) -> tuple[str | None, str]:
    """OI day follows yesterday's close. Otherwise the Loka side stands."""
    if percentile is not None and percentile < cutoff and prev_close is not None:
        if close > prev_close:
            return "Buy", "OI day"
        if close < prev_close:
            return "Short", "OI day"
        return None, "OI day"
    return loka_side, "Loka"


def exit_step(
    points: float | None,
    checked: bool,
    now: time,
    check: time,
    fallback: time,
) -> str:
    """close_check, mark, wait, or close_fallback."""
    if now < check and not checked:
        return "wait"
    if not checked:
        if points is None:
            return "wait" if now < fallback else "close_fallback"
        if points > 0:
            return "close_check"
        if now >= fallback:
            return "close_fallback"
        return "mark"
    if now < fallback:
        return "wait"
    return "close_fallback"


def load_pcr(path: Path = PCR_PATH) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict) and row.get("date") and row.get("pcr") is not None]


def remember_pcr(day: date, pcr: float, path: Path = PCR_PATH) -> None:
    """Keep the first ratio recorded for a session."""
    rows = load_pcr(path)
    iso = day.isoformat()
    if any(str(row.get("date")) == iso for row in rows):
        return
    rows.append({"date": iso, "pcr": round(float(pcr), 6)})
    rows.sort(key=lambda row: str(row["date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def series_ending(rows: list[dict], day: date, pcr: float) -> list[float]:
    """Prior sessions, then today's ratio. A ratio already stored for today is kept."""
    iso = day.isoformat()
    stored = next((float(row["pcr"]) for row in rows if str(row.get("date")) == iso), None)
    prior = [float(row["pcr"]) for row in rows if str(row.get("date")) < iso]
    return prior + [pcr if stored is None else stored]
