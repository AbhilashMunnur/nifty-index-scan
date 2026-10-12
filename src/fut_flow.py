"""Nifty and Sensex futures 15-minute reading from futures price and open interest.

One label and one lot count per interval. Open interest up is an equal number
of new longs and new shorts; open interest down is an equal number of long
exits and short covers. The price names which side pushed the bar.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FUTURES_SYMBOLS = ("NIFTY", "SENSEX")


def mark_path(symbol: str) -> Path:
    key = symbol.strip().upper()
    if key == "NIFTY":
        return Path("data/nifty_fut_flow_mark.json")
    return Path(f"data/{key.lower()}_fut_flow_mark.json")


def day_open_path(symbol: str) -> Path:
    return Path(f"data/{symbol.strip().lower()}_fut_day_open.json")


DAY_LABELS = {
    "NEW BUYING": "LONG BUILDUP",
    "NEW SELLING": "SHORT BUILDUP",
    "SHORT COVERING": "SHORT COVERING",
    "LONG EXIT": "LONG UNWIND",
}


MARKER = mark_path("NIFTY")
# A smaller futures-price move does not name a side. Ten paise is noise.
MIN_PRICE_MOVE = 1.0


@dataclass(frozen=True)
class FutReading:
    label: str
    lots: int | None
    price: float
    prev_price: float | None
    oi: int
    prev_oi: int | None
    lot_size: int
    compared_to: str | None
    tradingsymbol: str
    expiry: str


def load_mark(path: Path = MARKER) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_mark(
    *,
    tradingsymbol: str,
    token: str,
    expiry: str,
    lot_size: int,
    price: float,
    oi: int,
    path: Path = MARKER,
) -> None:
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tradingsymbol": tradingsymbol,
        "token": token,
        "expiry": expiry,
        "lot_size": lot_size,
        "price": price,
        "oi": oi,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    from src.scan_history import append_scan, history_kind

    kind = history_kind(path)
    if kind:
        append_scan(kind, payload)


def save_day_open_once(
    *,
    tradingsymbol: str,
    token: str,
    expiry: str,
    lot_size: int,
    price: float,
    oi: int,
    path: Path,
) -> bool:
    """Keep the first futures quote of the session. Later slots must not replace it."""
    existing = load_mark(path)
    today = datetime.now().strftime("%Y-%m-%d")
    if str(existing.get("updated_at", "")).startswith(today):
        return False
    save_mark(
        tradingsymbol=tradingsymbol,
        token=token,
        expiry=expiry,
        lot_size=lot_size,
        price=price,
        oi=oi,
        path=path,
    )
    return True


def oi_change_pct(start_oi: int, end_oi: int) -> float | None:
    if start_oi <= 0:
        return None
    return (end_oi - start_oi) / start_oi * 100.0


def _lots(oi_change: int, lot_size: int) -> int | None:
    if lot_size <= 0:
        return None
    return int(oi_change / lot_size)


def classify_fut_flow(
    *,
    price: float,
    oi: int,
    lot_size: int,
    prev_price: float | None,
    prev_oi: int | None,
    tradingsymbol: str,
    expiry: str,
    compared_to: str | None,
) -> FutReading:
    if prev_price is None or prev_oi is None or compared_to is None:
        return FutReading(
            "BASELINE",
            None,
            price,
            prev_price,
            oi,
            prev_oi,
            lot_size,
            None,
            tradingsymbol,
            expiry,
        )

    oi_lots = _lots(oi - prev_oi, lot_size)
    price_up = price >= prev_price + MIN_PRICE_MOVE
    price_down = price <= prev_price - MIN_PRICE_MOVE
    if oi_lots is None or oi_lots == 0:
        if price_up:
            label = "PRICE UP"
        elif price_down:
            label = "PRICE DOWN"
        else:
            label = "UNCHANGED"
        return FutReading(
            label, 0, price, prev_price, oi, prev_oi, lot_size, compared_to, tradingsymbol, expiry
        )

    lots = abs(oi_lots)
    if oi_lots > 0 and price_up:
        label = "NEW BUYING"
    elif oi_lots > 0 and price_down:
        label = "NEW SELLING"
    elif oi_lots < 0 and price_up:
        label = "SHORT COVERING"
    elif oi_lots < 0 and price_down:
        label = "LONG EXIT"
    elif oi_lots > 0:
        label = "OI UP"
    else:
        label = "OI DOWN"
    return FutReading(
        label, lots, price, prev_price, oi, prev_oi, lot_size, compared_to, tradingsymbol, expiry
    )


def reading_from_mark(
    mark: dict,
    *,
    price: float,
    oi: int,
    lot_size: int,
    tradingsymbol: str,
    expiry: str,
) -> FutReading:
    same_contract = (
        mark.get("tradingsymbol") == tradingsymbol
        and mark.get("price") is not None
        and mark.get("oi") is not None
    )
    if not same_contract:
        return classify_fut_flow(
            price=price,
            oi=oi,
            lot_size=lot_size,
            prev_price=None,
            prev_oi=None,
            tradingsymbol=tradingsymbol,
            expiry=expiry,
            compared_to=None,
        )
    return classify_fut_flow(
        price=price,
        oi=oi,
        lot_size=lot_size,
        prev_price=float(mark["price"]),
        prev_oi=int(mark["oi"]),
        tradingsymbol=tradingsymbol,
        expiry=expiry,
        compared_to=str(mark.get("updated_at") or ""),
    )


def _fmt_lots(lots: int | None) -> str:
    return "n/a" if lots is None else f"{lots:,}"


def format_fut_alert(reading: FutReading, *, when: datetime, name: str = "NIFTY") -> str:
    lines = [
        f"{name.strip().upper()} FUT 15m — {when:%d %b %Y %H:%M}",
        f"{reading.tradingsymbol}  expiry {reading.expiry}",
    ]
    if reading.label == "BASELINE":
        lines.append(
            "Baseline stored. The next 15-minute alert will show new buying, "
            "new selling, short covering, or long exits."
        )
    elif reading.compared_to:
        lines.append(f"Since {reading.compared_to}  {reading.label}  {_fmt_lots(reading.lots)} lots")
    else:
        lines.append(f"{reading.label}  {_fmt_lots(reading.lots)} lots")

    if reading.prev_price is None:
        lines.append(f"Price {reading.price:,.2f}")
    else:
        lines.append(
            f"Price {reading.prev_price:,.2f} → {reading.price:,.2f}  "
            f"({reading.price - reading.prev_price:+,.2f})"
        )
    oi_lots_now = _lots(reading.oi, reading.lot_size)
    if reading.prev_oi is None:
        lines.append(f"Open interest {_fmt_lots(oi_lots_now)} lots")
    else:
        oi_lots_prev = _lots(reading.prev_oi, reading.lot_size)
        lines.append(
            f"Open interest {_fmt_lots(oi_lots_prev)} → {_fmt_lots(oi_lots_now)} lots"
        )
    lines.append("Futures price and futures open interest. One label for this interval.")
    return "\n".join(lines)


def format_fut_day_alert(
    reading: FutReading,
    *,
    when: datetime,
    name: str,
    start_at: str,
    end_at: str,
) -> str:
    label = DAY_LABELS.get(reading.label, reading.label)
    signed = None
    pct = None
    if reading.prev_oi is not None:
        signed = _lots(reading.oi - reading.prev_oi, reading.lot_size)
        pct = oi_change_pct(reading.prev_oi, reading.oi)
    pct_text = "n/a" if pct is None else f"{pct:+.2f}%"
    lots_text = "n/a" if signed is None else f"{signed:+,}"
    start_lots = _lots(reading.prev_oi or 0, reading.lot_size) if reading.prev_oi is not None else None
    end_lots = _lots(reading.oi, reading.lot_size)
    lines = [
        f"{name.strip().upper()} FUT DAY — {when:%d %b %Y %H:%M}",
        f"{reading.tradingsymbol}  expiry {reading.expiry}",
        f"{start_at} → {end_at}  {label}",
        (
            f"Lots {_fmt_lots(start_lots)} → {_fmt_lots(end_lots)}  "
            f"({lots_text}, {pct_text})"
        ),
    ]
    if reading.prev_price is None:
        lines.append(f"Price {reading.price:,.2f}")
    else:
        lines.append(
            f"Price {reading.prev_price:,.2f} → {reading.price:,.2f}  "
            f"({reading.price - reading.prev_price:+,.2f})"
        )
    lines.append("Whole session. Futures price and futures open interest.")
    return "\n".join(lines)


def format_fut_day_alert_html(
    reading: FutReading,
    *,
    when: datetime,
    name: str,
    start_at: str,
    end_at: str,
) -> str:
    text = format_fut_day_alert(
        reading, when=when, name=name, start_at=start_at, end_at=end_at
    )
    lines = []
    for line in text.split("\n"):
        escaped = html.escape(line)
        if "→" in line and any(name in line for name in DAY_LABELS.values()):
            lines.append(f"<b>{escaped}</b>")
        else:
            lines.append(escaped)
    return "\n".join(lines)


def format_fut_alert_html(reading: FutReading, *, when: datetime, name: str = "NIFTY") -> str:
    text = format_fut_alert(reading, when=when, name=name)
    lines = []
    for line in text.split("\n"):
        escaped = html.escape(line)
        if line.startswith("Since "):
            lines.append(f"<b>{escaped}</b>")
        else:
            lines.append(escaped)
    return "\n".join(lines)
