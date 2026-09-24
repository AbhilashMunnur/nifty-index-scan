"""Classify option OI builds as WRITE vs BUY using premium direction.

Compare each strike to the *previous scan* (OI + premium):
  OI ↑ + premium ↑ → BUY
  OI ↑ + premium ↓ → WRITE
  OI ↓ + premium ↓ → LONG EXIT
  OI ↓ + premium ↑ → SHORT COVER
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.paths import ROOT
from src.pcr import BandPcr

MARKER = ROOT / "data" / "band_scan_mark.json"
# Legacy ATM-only mark (kept for one-release compatibility).
_LEGACY_MARKER = ROOT / "data" / "atm_premium_mark.json"


def mark_path_for(symbol: str) -> Path:
    """Nifty keeps the historical filename; Sensex (and others) get their own mark."""
    key = str(symbol).strip().upper()
    if key == "NIFTY":
        return MARKER
    return ROOT / "data" / f"{key.lower()}_band_scan_mark.json"

# Tiny premium moves are noise (sideways / tick chatter) — do not force BUY/WRITE.
PREMIUM_DEADZONE_PCT = 0.015  # 1.5% of prior premium
PREMIUM_DEADZONE_MIN_RS = 2.0  # at least ₹2 absolute


@dataclass(frozen=True)
class LegFlow:
    leg: str  # CE / PE
    strike: float
    label_strike: str  # ITM2 / ATM / …
    oi_change: int | None
    oi_lots: int | None
    premium: float | None
    premium_change: float | None
    label: str  # BUY / WRITE / LONG EXIT / SHORT COVER / OI BUILD / OI FALL / FLAT / n/a
    note: str


@dataclass(frozen=True)
class StrikeFlow:
    label: str
    strike: float
    call: LegFlow
    put: LegFlow


@dataclass(frozen=True)
class BandFlow:
    strikes: list[StrikeFlow]
    compared_to: str | None  # previous scan timestamp, or None if baseline

    @property
    def atm(self) -> StrikeFlow | None:
        for row in self.strikes:
            if row.label == "ATM":
                return row
        return None

    def total_lots(self, leg: str, label: str) -> int:
        total = 0
        for row in self.strikes:
            flow = row.call if leg == "CE" else row.put
            if flow.label == label and flow.oi_lots:
                total += abs(flow.oi_lots)
        return total


def premium_move_is_meaningful(
    premium_change: float,
    *,
    prev_premium: float | None,
    premium: float | None = None,
) -> bool:
    """True when |Δprem| clears the deadzone (max of ₹2 and 1.5% of prior)."""
    base = prev_premium if prev_premium not in (None, 0) else premium
    if base is None or base == 0:
        threshold = PREMIUM_DEADZONE_MIN_RS
    else:
        threshold = max(PREMIUM_DEADZONE_MIN_RS, PREMIUM_DEADZONE_PCT * abs(float(base)))
    return abs(premium_change) >= threshold - 1e-12


def _lots(oi_change: int | None, lot_size: int) -> int | None:
    if oi_change is None or lot_size <= 0:
        return None
    return int(oi_change / lot_size)


def classify_leg(
    *,
    leg: str,
    strike: float,
    label_strike: str = "",
    oi_change: int | None,
    premium: float | None,
    prev_premium: float | None,
    lot_size: int,
    vs: str = "last scan",
) -> LegFlow:
    """Classify from scan-to-scan OI Δ + that option's own premium Δ (never spot)."""
    oi_lots = _lots(oi_change, lot_size)
    premium_change = None
    if premium is not None and prev_premium is not None:
        premium_change = premium - prev_premium

    if oi_change is None:
        return LegFlow(
            leg, strike, label_strike, None, None, premium, premium_change, "n/a", "OI Δ missing"
        )

    # No premium compare, or premium barely moved → OI-only labels (sideways-safe).
    prem_usable = (
        premium_change is not None
        and premium_move_is_meaningful(
            premium_change, prev_premium=prev_premium, premium=premium
        )
    )
    if not prem_usable:
        dead = (
            premium_change is not None
            and not premium_move_is_meaningful(
                premium_change, prev_premium=prev_premium, premium=premium
            )
        )
        why = (
            f"prem Δ {premium_change:+.2f} inside deadzone "
            f"(need ≥₹{PREMIUM_DEADZONE_MIN_RS:.0f} or "
            f"{PREMIUM_DEADZONE_PCT:.1%} of prior)"
            if dead
            else f"need premium Δ vs {vs} for BUY/WRITE"
        )
        if oi_change > 0:
            return LegFlow(
                leg,
                strike,
                label_strike,
                oi_change,
                oi_lots,
                premium,
                premium_change,
                "OI BUILD",
                f"+{oi_lots:,} lots vs {vs} — {why}",
            )
        if oi_change < 0:
            return LegFlow(
                leg,
                strike,
                label_strike,
                oi_change,
                oi_lots,
                premium,
                premium_change,
                "OI FALL",
                f"{oi_lots:,} lots vs {vs} — {why}",
            )
        return LegFlow(
            leg, strike, label_strike, oi_change, oi_lots, premium, premium_change, "FLAT", "OI unchanged"
        )

    assert premium_change is not None
    if oi_change > 0 and premium_change > 0:
        label, note = "BUY", f"+{oi_lots:,} lots, prem {premium_change:+.2f} → buyers"
    elif oi_change > 0 and premium_change < 0:
        label, note = "WRITE", f"+{oi_lots:,} lots, prem {premium_change:+.2f} → writers"
    elif oi_change < 0 and premium_change < 0:
        label, note = "LONG EXIT", f"{oi_lots:,} lots, prem {premium_change:+.2f} → longs out"
    elif oi_change < 0 and premium_change > 0:
        label, note = "SHORT COVER", f"{oi_lots:,} lots, prem {premium_change:+.2f} → shorts cover"
    else:
        label, note = "MIXED", f"OI {oi_lots:,} lots, prem {premium_change:+.2f}"

    return LegFlow(
        leg, strike, label_strike, oi_change, oi_lots, premium, premium_change, label, note
    )


def load_premium_mark(path: Path = MARKER) -> dict:
    for candidate in (path, _LEGACY_MARKER):
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def save_premium_mark(band: BandPcr, path: Path = MARKER) -> None:
    """Persist full 5-strike OI + premium for the next scan compare."""
    strikes = []
    for row in band.strikes:
        strikes.append(
            {
                "label": row.label,
                "strike": row.strike,
                "call_oi": row.call_oi,
                "put_oi": row.put_oi,
                "call_ltp": row.call_ltp,
                "put_ltp": row.put_ltp,
            }
        )
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expiry": band.expiry,
        "atm": band.atm,
        "lot_size": band.lot_size,
        "strikes": strikes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _prev_by_strike(mark: dict, expiry: str) -> dict[float, dict]:
    if mark.get("expiry") != expiry:
        return {}
    out: dict[float, dict] = {}
    for row in mark.get("strikes") or []:
        try:
            strike = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        out[strike] = row
    # Legacy ATM-only mark
    if not out and mark.get("atm") is not None:
        try:
            out[float(mark["atm"])] = {
                "call_oi": mark.get("call_oi"),
                "put_oi": mark.get("put_oi"),
                "call_ltp": mark.get("call_ltp"),
                "put_ltp": mark.get("put_ltp"),
            }
        except (TypeError, ValueError):
            pass
    return out


def classify_band_flow(
    band: BandPcr, path: Path = MARKER, mark: dict | None = None
) -> BandFlow:
    if mark is None:
        mark = load_premium_mark(path)
    prev = _prev_by_strike(mark, band.expiry)
    compared_to = mark.get("updated_at") if prev else None
    rows: list[StrikeFlow] = []

    for row in band.strikes:
        prior = prev.get(row.strike) or {}
        prev_call_oi = prior.get("call_oi")
        prev_put_oi = prior.get("put_oi")
        has_call_prior = prev_call_oi is not None
        has_put_prior = prev_put_oi is not None

        if has_call_prior:
            call_d = row.call_oi - int(prev_call_oi)
            prev_call_p = (
                float(prior["call_ltp"]) if prior.get("call_ltp") is not None else None
            )
            call_vs = "last scan"
        else:
            call_d = row.call_oi_change
            prev_call_p = None
            call_vs = "prior close"

        if has_put_prior:
            put_d = row.put_oi - int(prev_put_oi)
            prev_put_p = (
                float(prior["put_ltp"]) if prior.get("put_ltp") is not None else None
            )
            put_vs = "last scan"
        else:
            put_d = row.put_oi_change
            prev_put_p = None
            put_vs = "prior close"

        call = classify_leg(
            leg="CE",
            strike=row.strike,
            label_strike=row.label,
            oi_change=call_d,
            premium=row.call_ltp,
            prev_premium=prev_call_p,
            lot_size=band.lot_size,
            vs=call_vs,
        )
        put = classify_leg(
            leg="PE",
            strike=row.strike,
            label_strike=row.label,
            oi_change=put_d,
            premium=row.put_ltp,
            prev_premium=prev_put_p,
            lot_size=band.lot_size,
            vs=put_vs,
        )
        rows.append(StrikeFlow(label=row.label, strike=row.strike, call=call, put=put))

    return BandFlow(strikes=rows, compared_to=compared_to)


def classify_atm_flow(band: BandPcr, path: Path = MARKER) -> tuple[LegFlow, LegFlow] | None:
    """ATM CE/PE pair — used for Telegram photo caption."""
    band_flow = classify_band_flow(band, path=path)
    atm = band_flow.atm
    if atm is None:
        return None
    return atm.call, atm.put


def format_oi_flow(band: BandPcr, flows: tuple[LegFlow, LegFlow] | None = None) -> str:
    """Backward-compatible wrapper."""
    del flows
    return format_band_flow(band, classify_band_flow(band))


def _fmt_lots(oi_lots: int | None) -> str:
    return "n/a" if oi_lots is None else f"{oi_lots:+,}"


def _fmt_prem(premium: float | None) -> str:
    return "n/a" if premium is None else f"{premium:,.2f}"


def _fmt_dprem(premium_change: float | None) -> str:
    return "n/a" if premium_change is None else f"{premium_change:+.2f}"


def interval_totals_line(band_flow: BandFlow, prefix: str = "New this interval") -> str:
    return (
        f"{prefix}  CE BUY {band_flow.total_lots('CE', 'BUY'):,} / "
        f"WRITE {band_flow.total_lots('CE', 'WRITE'):,}   "
        f"PE BUY {band_flow.total_lots('PE', 'BUY'):,} / "
        f"WRITE {band_flow.total_lots('PE', 'WRITE'):,}  (lots)"
    )


def format_band_flow(
    band: BandPcr,
    band_flow: BandFlow,
    *,
    heading: str | None = None,
    totals_prefix: str = "New this interval",
) -> str:
    lines = [
        heading or "—— OI FLOW (vs last scan · option premium, not spot) ——"
    ]
    lines.append(interval_totals_line(band_flow, prefix=totals_prefix))
    if band_flow.compared_to:
        lines.append(f"Compared to scan @ {band_flow.compared_to}")
    else:
        lines.append(
            "Baseline / first mark — BUY/WRITE lots appear from the next 15-min scan."
        )
    lines.append(
        f"Premium deadzone: |Δ| < max(₹{PREMIUM_DEADZONE_MIN_RS:.0f}, "
        f"{PREMIUM_DEADZONE_PCT:.1%} of prior) → OI BUILD/FALL only"
    )
    lines.append("")
    lines.append(
        f"{'Lbl':<5} {'Strike':>7} {'Side':<4} {'Flow':<11} "
        f"{'Lots':>8} {'Prem':>7} {'Δprem':>7}"
    )
    lines.append("-" * 54)
    for row in band_flow.strikes:
        for flow in (row.call, row.put):
            lines.append(
                f"{row.label:<5} {flow.strike:>7,.0f} {flow.leg:<4} {flow.label:<11} "
                f"{_fmt_lots(flow.oi_lots):>8} {_fmt_prem(flow.premium):>7} "
                f"{_fmt_dprem(flow.premium_change):>7}"
            )

    atm = band_flow.atm
    hints: list[str] = []
    if atm is not None:
        if atm.put.label == "BUY":
            hints.append("ATM Put BUY → caution on new LONGs")
        if atm.put.label == "WRITE":
            hints.append("ATM Put WRITE → supportive for LONGs")
        if atm.call.label == "WRITE":
            hints.append("ATM Call WRITE → resistance / supportive for SHORTs")
        if atm.call.label == "BUY":
            hints.append("ATM Call BUY → caution on new SHORTs")
    if hints:
        lines.append("Hint: " + "; ".join(hints))
    elif not band_flow.compared_to:
        lines.append("Hint: store this mark; next scan prints BUY/WRITE lot counts per strike.")

    _ = band  # report is band-scoped; lot size already applied in flows
    return "\n".join(lines)


def format_flow_caption(band_flow: BandFlow | None) -> str:
    """Short 5-strike CE/PE labels for the Telegram photo caption."""
    if band_flow is None or not band_flow.strikes:
        return ""
    lines = [interval_totals_line(band_flow)]
    for row in band_flow.strikes:
        lines.append(
            f"{row.strike:.0f} {row.label} CE {row.call.label} · PE {row.put.label}"
        )
    return "\n".join(lines)
