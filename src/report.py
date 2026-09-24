from __future__ import annotations

from datetime import datetime

from src.models import FutureContract
from src.pcr import BandPcr
from src.strategy import first_target_price, favourable_move_pct, stop_loss_price


def _fmt_pcr(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_change(value: int | None, lot_size: int) -> str:
    if value is None:
        return "n/a"
    lots = int(value / lot_size) if lot_size else value
    sign = "+" if lots > 0 else ""
    return f"{sign}{lots:,}"


def format_band(band: BandPcr) -> str:
    lines = [
        f"Option expiry {band.expiry}  ATM {band.atm:,.0f}  step {band.step:,.0f}  "
        f"lot {band.lot_size}",
        f"{'Strike':<8} {'Label':<6} {'Call OI':>10} {'Put OI':>10} "
        f"{'PCR':>6} {'ΔCall':>8} {'ΔPut':>8} {'ΔPCR':>6}",
    ]
    for row in band.strikes:
        lines.append(
            f"{row.strike:<8.0f} {row.label:<6} "
            f"{row.call_oi / band.lot_size:10,.0f} "
            f"{row.put_oi / band.lot_size:10,.0f} "
            f"{_fmt_pcr(row.pcr):>6} "
            f"{_fmt_change(row.call_oi_change, band.lot_size):>8} "
            f"{_fmt_change(row.put_oi_change, band.lot_size):>8} "
            f"{_fmt_pcr(row.change_pcr):>6}"
        )
    lines.append(
        f"{'BAND':<8} {'5-stk':<6} "
        f"{band.call_oi / band.lot_size:10,.0f} "
        f"{band.put_oi / band.lot_size:10,.0f} "
        f"{_fmt_pcr(band.pcr):>6} "
        f"{_fmt_change(band.call_oi_change, band.lot_size):>8} "
        f"{_fmt_change(band.put_oi_change, band.lot_size):>8} "
        f"{_fmt_pcr(band.change_pcr):>6}"
    )
    lines.append("OI shown in lots. Δ is vs previous session close.")
    return "\n".join(lines)


def format_signal_report(
    *,
    spot: float,
    rsi: float | None,
    future: FutureContract | None,
    skipped: list[tuple[str, int]] | None,
    band: BandPcr | None,
    signal: str | None,
    reason: str,
    future_ltp: float | None = None,
    symbol: str = "NIFTY",
) -> str:
    rsi_text = "n/a" if rsi is None else f"{rsi:.1f}"
    title = "Nifty" if symbol.upper() == "NIFTY" else symbol.title()
    lines = [
        f"{title} 1H strategy — {datetime.now():%d %b %Y %H:%M}",
        f"Spot {spot:,.2f}  1H RSI {rsi_text}",
    ]
    if skipped:
        for name, days in skipped:
            lines.append(f"Skipped {name}: {days} trading day(s) to expiry (< 10)")
    if future:
        price = f"  LTP {future_ltp:,.2f}" if future_ltp else ""
        lines.append(
            f"Trade {future.tradingsymbol}  expiry {future.expiry}  "
            f"{future.trading_days_left} trading days left{price}"
        )
    else:
        lines.append("No future with >= 10 trading days to expiry")

    lines.append("")
    if band:
        lines.append(format_band(band))
        lines.append("")
    lines.append(f"Signal: {signal or 'NONE'}")
    lines.append(reason)
    if band and band.strike("ATM") is not None:
        atm = band.strike("ATM")
        lines.append(
            f"ATM ΔPCR { _fmt_pcr(atm.change_pcr) }  "
            f"(used for the RSI exit; books all remaining lots if 1.5% is not done)"
        )
    return "\n".join(lines)


def format_open_position(position: dict, price: float | None, rsi: float | None) -> str:
    direction = position["direction"]
    entry = float(position["entry_price"])
    lots = int(position.get("lots") or 0)
    lines = [
        f"Open {direction} {lots} lot(s) {position['tradingsymbol']} @ {entry:,.2f}"
    ]
    if position.get("scale_lot_open"):
        target = float(
            position.get("first_target_price")
            or first_target_price(direction, entry, float(position.get("first_target_pct") or 1.5))
        )
        lines.append(f"  Scale lot still open — 1.5% target {target:,.2f}")
    else:
        lines.append("  Scale lot booked at 1.5%")
    if position.get("rsi_lot_open"):
        if direction == "SHORT":
            lines.append(
                "  RSI exit still open — RSI 35-30 + ATM ΔPCR > 1 books every remaining lot "
                "(or RSI <= 30)"
            )
        else:
            lines.append(
                "  RSI exit still open — RSI 65-70 + ATM ΔPCR < 0.8 books every remaining lot "
                "(or RSI >= 70)"
            )
    else:
        lines.append("  RSI lot booked")
    stop = float(
        position.get("stop_loss_price")
        or stop_loss_price(direction, entry, float(position.get("stop_loss_pct") or 0.75))
    )
    lines.append(f"  SL {float(position.get('stop_loss_pct') or 0.75):g}% against entry @ {stop:,.2f}")
    if price:
        lines.append(
            f"  Future LTP {price:,.2f}  ({favourable_move_pct(direction, entry, price):+.2f}% vs entry)"
        )
    if rsi is not None:
        lines.append(f"  1H RSI {rsi:.1f}")
    return "\n".join(lines)


def _inr(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}₹{abs(amount):,.2f}"


def format_pnl_snapshot(
    *,
    position: dict | None,
    realised_pnl: float,
    mark: float | None,
    rsi: float | None = None,
    closed_count: int = 0,
) -> str:
    """Compact broker-style P&L block for Telegram."""
    lines = ["—— P&L SNAPSHOT ——"]
    unrealised = 0.0

    if position and int(position.get("lots") or 0) > 0:
        direction = str(position["direction"])
        entry = float(position["entry_price"])
        lots = int(position["lots"])
        lot_size = int(position.get("lot_size") or 0)
        qty = lots * lot_size
        symbol = position.get("tradingsymbol") or "FUT"
        lines.append(f"Open {direction} {lots} lot(s) {symbol}")
        lines.append(
            f"Avg {entry:,.2f}"
            + (f"  LTP {mark:,.2f}" if mark else "  LTP n/a")
            + f"  Qty {qty:,}"
        )
        if mark and lot_size:
            move = mark - entry
            if direction == "SHORT":
                move = -move
            unrealised = move * lot_size * lots
            lines.append(
                f"Move {move:+.2f} pts ({favourable_move_pct(direction, entry, mark):+.2f}%)"
            )
            lines.append(f"Unrealised  {_inr(unrealised)}")
            notional = mark * lot_size * lots
            lines.append(f"Exposure    {_inr(notional)}")
        target = float(
            position.get("first_target_price")
            or first_target_price(
                direction, entry, float(position.get("first_target_pct") or 1.5)
            )
        )
        stop = float(
            position.get("stop_loss_price")
            or stop_loss_price(
                direction, entry, float(position.get("stop_loss_pct") or 0.75)
            )
        )
        if mark:
            lines.append(
                f"Target {target:,.2f} ({target - mark:+.2f})  "
                f"SL {stop:,.2f} ({mark - stop:+.2f})"
            )
        else:
            lines.append(f"Target {target:,.2f}  SL {stop:,.2f}")
        if rsi is not None:
            lines.append(f"1H RSI {rsi:.1f}")
    else:
        lines.append("No open position")

    lines.append(f"Realised    {_inr(float(realised_pnl))}")
    if closed_count:
        lines.append(f"Closed trades {closed_count}")
    lines.append(f"Net MTM     {_inr(float(realised_pnl) + unrealised)}")
    return "\n".join(lines)
