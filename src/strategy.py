"""Index 1-hour RSI + 5-strike change-in-PCR futures signals."""

from __future__ import annotations

from dataclasses import dataclass

from src.pcr import BandPcr


@dataclass(frozen=True)
class StrategyRules:
    rsi_short: float = 70.0
    rsi_long: float = 30.0
    rsi_cover_short: float = 35.0  # book remaining short from here if ATM ΔPCR confirms
    rsi_cover_long: float = 65.0  # book remaining long from here if ATM ΔPCR confirms
    change_pcr_short_max: float = 0.8
    change_pcr_long_min: float = 1.0
    min_trading_days_to_expiry: int = 10
    rsi_period: int = 14
    first_target_pct: float = 1.5
    stop_loss_pct: float = 0.75
    lots: int = 2


def first_target_price(direction: str, entry: float, pct: float) -> float:
    """Price where the 1.5% scale-out lot is booked."""
    fraction = pct / 100.0
    if direction == "SHORT":
        return entry * (1 - fraction)
    return entry * (1 + fraction)


def favourable_move_pct(direction: str, entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    move = (price - entry) / entry * 100.0
    return -move if direction == "SHORT" else move


def scale_lot_hit(direction: str, entry: float, price: float, pct: float) -> bool:
    target = first_target_price(direction, entry, pct)
    if direction == "SHORT":
        return price <= target + 1e-9
    return price >= target - 1e-9


def stop_loss_price(direction: str, entry: float, pct: float) -> float:
    """Price 0.75% against the position, measured from entry."""
    fraction = pct / 100.0
    if direction == "SHORT":
        return entry * (1 + fraction)
    return entry * (1 - fraction)


def stop_loss_hit(direction: str, entry: float, price: float, pct: float) -> bool:
    stop = stop_loss_price(direction, entry, pct)
    if direction == "SHORT":
        return price >= stop - 1e-9
    return price <= stop + 1e-9


def remaining_lot_exit_rsi_only(
    direction: str,
    rsi: float | None,
    rules: StrategyRules,
) -> str | None:
    """Opposite RSI band with no OI filter: 35-30 after a short, 65-70 after a long."""
    if rsi is None:
        return None
    if direction == "SHORT" and rsi <= rules.rsi_cover_short:
        return f"1H RSI {rsi:.1f} <= {rules.rsi_cover_short:g}"
    if direction == "LONG" and rsi >= rules.rsi_cover_long:
        return f"1H RSI {rsi:.1f} >= {rules.rsi_cover_long:g}"
    return None


def split_lots(n: int) -> tuple[int, int]:
    """How many lots book at 1.5% vs the opposite RSI boundary.

    Even n → half / half. Odd n → more lots at 1.5%, remainder at RSI.
    """
    if n <= 0:
        return 0, 0
    if n % 2 == 0:
        half = n // 2
        return half, half
    scale = n // 2 + 1
    return scale, n - scale


def remaining_lot_exit(
    direction: str,
    rsi: float | None,
    atm_change_pcr: float | None,
    rules: StrategyRules,
) -> str | None:
    """RSI near the other boundary, timed by ATM ΔPCR.

    If this fires while both lots are still open (1.5% not hit yet), the
    whole position is booked. After a 1.5% scale-out it closes the rest.

    Short entered ~70: book around 35 if ATM ΔPCR > 1, or at 30 anyway.
    Long entered ~30: book around 65 if ATM ΔPCR < 0.8, or at 70 anyway.
    """
    if rsi is None:
        return None

    if direction == "SHORT":
        if rsi <= rules.rsi_long:
            return (
                f"1H RSI {rsi:.1f} <= {rules.rsi_long:g} "
                f"(opposite boundary after short)"
            )
        if rsi <= rules.rsi_cover_short:
            if atm_change_pcr is not None and atm_change_pcr > rules.change_pcr_long_min:
                return (
                    f"1H RSI {rsi:.1f} in {rules.rsi_long:g}-{rules.rsi_cover_short:g} "
                    f"and ATM ΔPCR {atm_change_pcr:.2f} > {rules.change_pcr_long_min:g}"
                )
        return None

    if direction == "LONG":
        if rsi >= rules.rsi_short:
            return (
                f"1H RSI {rsi:.1f} >= {rules.rsi_short:g} "
                f"(opposite boundary after long)"
            )
        if rsi >= rules.rsi_cover_long:
            if atm_change_pcr is not None and atm_change_pcr < rules.change_pcr_short_max:
                return (
                    f"1H RSI {rsi:.1f} in {rules.rsi_cover_long:g}-{rules.rsi_short:g} "
                    f"and ATM ΔPCR {atm_change_pcr:.2f} < {rules.change_pcr_short_max:g}"
                )
        return None

    return None


def evaluate_signal(
    rsi: float | None,
    band: BandPcr | None,
    rules: StrategyRules,
) -> tuple[str | None, str]:
    """Return (SHORT|LONG|None, reason)."""
    if rsi is None:
        return None, "hourly RSI unavailable"
    if band is None:
        return None, "5-strike PCR unavailable"
    if band.change_pcr is None:
        return None, "change in PCR unavailable (need prior OI on all 5 strikes)"

    change_pcr = band.change_pcr
    pcr_text = f"PCR {band.pcr:.2f}" if band.pcr is not None else "PCR n/a"
    delta_text = f"ΔPCR {change_pcr:.2f}"

    if rsi >= rules.rsi_short:
        if change_pcr < rules.change_pcr_short_max:
            return (
                "SHORT",
                f"1H RSI {rsi:.1f} >= {rules.rsi_short:g} and {delta_text} < "
                f"{rules.change_pcr_short_max:g} ({pcr_text})",
            )
        return (
            None,
            f"1H RSI {rsi:.1f} is >= {rules.rsi_short:g} but {delta_text} is not < "
            f"{rules.change_pcr_short_max:g} ({pcr_text})",
        )

    if rsi <= rules.rsi_long:
        if change_pcr > rules.change_pcr_long_min:
            return (
                "LONG",
                f"1H RSI {rsi:.1f} <= {rules.rsi_long:g} and {delta_text} > "
                f"{rules.change_pcr_long_min:g} ({pcr_text})",
            )
        return (
            None,
            f"1H RSI {rsi:.1f} is <= {rules.rsi_long:g} but {delta_text} is not > "
            f"{rules.change_pcr_long_min:g} ({pcr_text})",
        )

    return (
        None,
        f"1H RSI {rsi:.1f} is between {rules.rsi_long:g} and {rules.rsi_short:g} "
        f"({pcr_text}, {delta_text})",
    )
