"""Seed master_pcr_history.json from Fyers OI archives so paper ranks work day-1."""

from __future__ import annotations

import csv
from pathlib import Path

from src.master_strategy import PCR_PATH, PRIORITY, load_pcr_history, save_pcr_history
from src.paths import ROOT

OI_DIR = ROOT / "data" / "fyers" / "oi_readable"


def _merge(hist: dict[str, dict[str, float]], symbol: str, series: dict[str, float]) -> int:
    bucket = hist.setdefault(symbol, {})
    added = 0
    for day, pcr in series.items():
        if day not in bucket:
            bucket[day] = float(pcr)
            added += 1
    return added


def _from_csv_1400(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            day = str(row.get("day") or "")[:10]
            raw = row.get("pcr_1400")
            if not day or raw in (None, ""):
                continue
            try:
                out[day] = float(raw)
            except ValueError:
                continue
    return out


def _from_nifty_atm_5m(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    if not path.exists():
        return out
    try:
        import pandas as pd
    except ImportError:
        return out
    df = pd.read_parquet(path, columns=["day", "clock", "pcr", "oi_pcr"])
    sub = df[df["clock"].astype(str) == "14:00"].copy()
    if sub.empty:
        return out
    # Prefer band PCR column used in live ATM±wings style marks
    col = "pcr" if "pcr" in sub.columns else "oi_pcr"
    for row in sub.itertuples(index=False):
        day = str(getattr(row, "day"))[:10]
        val = float(getattr(row, col))
        if day and val == val:  # not NaN
            out[day] = val
    return out


def seed_master_pcr_history(*, force: bool = False) -> dict[str, int]:
    """Fill missing days from archives. Returns counts added per script."""
    hist = load_pcr_history()
    added = {s: 0 for s in PRIORITY}

    # If already warm and not forcing, skip heavy parquet read when NIFTY has enough
    warm = all(len(hist.get(s) or {}) >= 40 for s in ("NIFTY", "SENSEX"))
    if warm and not force:
        return added

    sx = _from_csv_1400(OI_DIR / "sensex_oi_daily.csv")
    bn = _from_csv_1400(OI_DIR / "banknifty_oi_daily.csv")
    nf = _from_nifty_atm_5m(OI_DIR / "nifty_atm_oi_5m.parquet")

    added["SENSEX"] = _merge(hist, "SENSEX", sx)
    added["BANKNIFTY"] = _merge(hist, "BANKNIFTY", bn)
    added["NIFTY"] = _merge(hist, "NIFTY", nf)

    if any(added.values()) or force or not PCR_PATH.exists():
        save_pcr_history(hist)
    return added


if __name__ == "__main__":
    counts = seed_master_pcr_history(force=True)
    hist = load_pcr_history()
    print("seeded", counts)
    print({k: len(v) for k, v in hist.items()})
    print("wrote", PCR_PATH)
