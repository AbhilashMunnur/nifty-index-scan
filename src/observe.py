from __future__ import annotations

from datetime import datetime

from src.models import IndexSnapshot


def format_oi(snapshot: IndexSnapshot, open_interest: int) -> str:
    lots = snapshot.contracts(open_interest)
    if lots is None:
        return f"{open_interest:,} shares"
    return f"{lots:,} lots"


def format_snapshot(snapshot: IndexSnapshot) -> str:
    lines = [
        f"{snapshot.symbol}  {snapshot.ltp:,.2f}  "
        f"expiry {snapshot.expiry}  lot {snapshot.lot_size}",
        f"  Max Call OI   {snapshot.max_call_oi_strike:,.0f}  "
        f"({format_oi(snapshot, snapshot.max_call_oi)})",
        f"  Max Put OI    {snapshot.max_put_oi_strike:,.0f}  "
        f"({format_oi(snapshot, snapshot.max_put_oi)})",
    ]
    if snapshot.futures_symbol:
        lines.append(
            f"  Future        {snapshot.futures_symbol}  ({snapshot.futures_expiry})"
        )
    return "\n".join(lines)


def format_digest(snapshots: list[IndexSnapshot], *, mode: str) -> str:
    title = "Nifty / Sensex observe" if mode == "observe" else "Nifty / Sensex paper"
    lines = [f"{title} — {datetime.now():%d %b %Y %H:%M}", ""]
    for snapshot in snapshots:
        lines.append(format_snapshot(snapshot))
        lines.append("")
    lines.append("Universe locked to NIFTY and SENSEX. Live orders are off.")
    return "\n".join(lines).rstrip()
