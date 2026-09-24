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


def atm_band(strikes: list[float], spot: float) -> list[tuple[str, float]] | None:
    """Five strikes: ITM2, ITM1, ATM, OTM1, OTM2 (call-side labels vs spot)."""
    unique = sorted({float(strike) for strike in strikes if strike > 0})
    atm = pick_atm(unique, spot)
    if atm is None:
        return None
    step = strike_step(unique, atm)
    if not step:
        return None

    wanted = [atm + offset * step for offset in (-2, -1, 0, 1, 2)]
    if any(strike not in unique for strike in wanted):
        return None
    return list(zip(STRIKE_LABELS, wanted, strict=True))
