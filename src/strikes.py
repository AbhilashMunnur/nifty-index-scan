"""ATM ± 2 strike band used for Nifty PCR."""

from __future__ import annotations


STRIKE_LABELS = ("ITM2", "ITM1", "ATM", "OTM1", "OTM2")


def pick_atm(strikes: list[float], spot: float) -> float | None:
    if not strikes or spot <= 0:
        return None
    return min(strikes, key=lambda strike: (abs(strike - spot), strike))


def strike_step(sorted_strikes: list[float], atm: float) -> float | None:
    """Local gap around ATM (Nifty is 50)."""
    if atm not in sorted_strikes:
        return None
    index = sorted_strikes.index(atm)
    gaps: list[float] = []
    if index > 0:
        gaps.append(sorted_strikes[index] - sorted_strikes[index - 1])
    if index + 1 < len(sorted_strikes):
        gaps.append(sorted_strikes[index + 1] - sorted_strikes[index])
    gaps = [gap for gap in gaps if gap > 0]
    return min(gaps) if gaps else None


def _label_for_offset(offset: int) -> str:
    if offset == 0:
        return "ATM"
    if offset < 0:
        return f"ITM{-offset}"
    return f"OTM{offset}"


def band_around(
    strikes: list[float], spot: float, *, wings: int = 2
) -> list[tuple[str, float]] | None:
    """Strikes from `wings` below the spot ATM through `wings` above it.

    wings=2 is the everyday five-strike band. wings=6 is the Tuesday and
    Thursday afternoon band: 6 below (ITM1–ITM6), the ATM, and 6 above.
    """
    unique = sorted({float(strike) for strike in strikes if strike > 0})
    atm = pick_atm(unique, spot)
    if atm is None:
        return None
    step = strike_step(unique, atm)
    if not step:
        return None

    found: list[tuple[str, float]] = []
    for offset in range(-wings, wings + 1):
        strike = atm + offset * step
        if strike not in unique:
            if wings == 2:
                return None
            continue
        found.append((_label_for_offset(offset), strike))
    if wings == 2:
        return found if len(found) == 5 else None
    labels = {label for label, _strike in found}
    if not {"ITM2", "ITM1", "ATM", "OTM1", "OTM2"} <= labels:
        return None
    return found


def atm_band(strikes: list[float], spot: float) -> list[tuple[str, float]] | None:
    """Five strikes: ITM2, ITM1, ATM, OTM1, OTM2 (call-side labels vs spot)."""
    return band_around(strikes, spot, wings=2)
