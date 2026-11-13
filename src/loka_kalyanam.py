"""Loka Kalyanam paper book.

Strong close at 15:00: buy the ATM call and sell the ATM put, close at 09:15.
Weak close at 15:00: sell the ATM call and buy the ATM put, close at 14:00.
A mixed close is skipped. Live orders are never placed.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time
from pathlib import Path

from src.paths import ROOT

NAME = "Loka Kalyanam"
BOOK_PATH = ROOT / "data" / "loka_kalyanam_book.json"
STARTING_CAPITAL = 10_000_000.0
CAPITAL_PER_LOT = 268_518.0
SLIP_POINTS = 4.0
STRIKE_STEP = 50
EXCHANGE_RATE = 0.0003553
ENTRY_CLOCK = "15:00"
BUY_EXIT = "09:15"
SHORT_EXIT = "14:00"


def stt_rate(on: date) -> float:
    """STT on the premium that is sold."""
    if on < date(2024, 10, 1):
        return 0.000625
    if on < date(2026, 4, 1):
        return 0.001
    return 0.0015


def classify_close(day_open: float, bar_1415: float, bar_1445: float) -> str | None:
    """Buy on a strong close, Short on a weak close, None when the day is mixed."""
    if bar_1445 > day_open and bar_1445 > bar_1415:
        return "Buy"
    if bar_1445 < day_open and bar_1445 < bar_1415:
        return "Short"
    return None


def exit_clock(side: str) -> str:
    return BUY_EXIT if side == "Buy" else SHORT_EXIT


def atm_strike(spot: float, step: int = STRIKE_STEP) -> int:
    """Nearest strike. A tie halfway between two strikes rounds up."""
    return int(math.floor(spot / step + 0.5)) * step


def opening_orders(side: str) -> tuple[str, str]:
    """Call action, put action at 15:00."""
    if side == "Buy":
        return "Buy", "Sell"
    return "Sell", "Buy"


def closing_orders(side: str) -> tuple[str, str]:
    call_action, put_action = opening_orders(side)
    flip = {"Buy": "Sell", "Sell": "Buy"}
    return flip[call_action], flip[put_action]


def pair_points(
    side: str,
    call_entry: float,
    call_exit: float,
    put_entry: float,
    put_exit: float,
) -> float:
    """Change in (call − put), signed with the position, minus 4 points."""
    synth = (call_exit - call_entry) - (put_exit - put_entry)
    raw = synth if side == "Buy" else -synth
    return round(raw - SLIP_POINTS, 2)


def _money(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def leg_costs(sell_on: date, sell_px: float, buy_px: float, qty: int) -> dict[str, float]:
    """Charges for one option. STT is on the sale. Stamp is on the purchase."""
    sell_turnover = max(sell_px, 0.0) * qty
    buy_turnover = max(buy_px, 0.0) * qty
    stt = sell_turnover * stt_rate(sell_on)
    stamp = 0.00003 * buy_turnover
    exchange = EXCHANGE_RATE * (sell_turnover + buy_turnover)
    sebi = 0.000001 * (sell_turnover + buy_turnover)
    brokerage = 20.0 + (20.0 if buy_px > 0 else 0.0)
    gst = 0.18 * (brokerage + exchange + sebi)
    parts = {
        "stt": _money(stt),
        "stamp": _money(stamp),
        "exchange_sebi": _money(exchange + sebi),
        "gst": _money(gst),
        "brokerage": _money(brokerage),
    }
    parts["total"] = _money(sum(parts.values()))
    return parts


def pair_costs(
    side: str,
    entry_on: date,
    exit_on: date,
    call_entry: float,
    call_exit: float,
    put_entry: float,
    put_exit: float,
    qty: int,
) -> dict[str, float]:
    """Both legs. A buy sells the call at the exit and the put at the entry."""
    if side == "Buy":
        call = leg_costs(exit_on, call_exit, call_entry, qty)
        put = leg_costs(entry_on, put_entry, put_exit, qty)
    else:
        call = leg_costs(entry_on, call_entry, call_exit, qty)
        put = leg_costs(exit_on, put_exit, put_entry, qty)
    keys = ("stt", "stamp", "exchange_sebi", "gst", "brokerage")
    out = {key: _money(call[key] + put[key]) for key in keys}
    out["total"] = _money(sum(out.values()))
    return out


def lots_for(capital: float, per_lot: float = CAPITAL_PER_LOT) -> int:
    if per_lot <= 0:
        return 0
    return int(capital // per_lot)


def entry_window(at: datetime) -> bool:
    """15:00 through 15:40. The 14:45 bar has closed, and a late scan can still fill."""
    current = at.time()
    return time(15, 0) <= current <= time(15, 40)


def exit_due(position: dict, at: datetime) -> bool:
    """The close is the next session, at 09:15 for a buy and 14:00 for a short."""
    entry_on = date.fromisoformat(str(position["entry_date"]))
    if at.date() <= entry_on:
        return False
    hour, minute = str(position["exit_clock"]).split(":")
    return at.time() >= time(int(hour), int(minute))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _iso(day: date) -> str:
    return day.isoformat()


class LokaBook:
    def __init__(self, path: Path | None = None):
        self.path = path or BOOK_PATH
        self.name = NAME
        self.started_on = "2026-11-13"
        self.starting_capital = STARTING_CAPITAL
        self.capital = STARTING_CAPITAL
        self.realised_pnl = 0.0
        self.position: dict | None = None
        self.closed: list[dict] = []
        self.updated_at = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.started_on = str(raw.get("started_on") or self.started_on)
        self.starting_capital = float(raw.get("starting_capital") or STARTING_CAPITAL)
        self.capital = float(raw.get("capital") or self.starting_capital)
        self.realised_pnl = float(raw.get("realised_pnl") or 0)
        self.position = raw.get("position")
        self.closed = list(raw.get("closed") or [])
        self.updated_at = str(raw.get("updated_at") or "")

    def save(self) -> None:
        self.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "started_on": self.started_on,
                    "starting_capital": self.starting_capital,
                    "capital": _money(self.capital),
                    "realised_pnl": _money(self.realised_pnl),
                    "updated_at": self.updated_at,
                    "position": self.position,
                    "closed": self.closed,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            from src.loka_journal import write_journal

            write_journal(self)
        except Exception as exc:
            print(f"Loka Kalyanam journal was not written ({exc})")

    @property
    def is_open(self) -> bool:
        return isinstance(self.position, dict) and int(self.position.get("lots") or 0) > 0

    def entered_on(self, day: date) -> bool:
        if self.is_open and self.position.get("entry_date") == _iso(day):
            return True
        return any(trade.get("entry_date") == _iso(day) for trade in self.closed)

    def enter(
        self,
        *,
        side: str,
        day: date,
        nifty: float,
        call_symbol: str,
        call_token: str,
        call_entry: float,
        put_symbol: str,
        put_token: str,
        put_entry: float,
        strike: int,
        expiry: str,
        lot_size: int,
    ) -> str:
        call_action, put_action = opening_orders(side)
        lots = lots_for(self.capital)
        if lots < 1 or lot_size < 1:
            return "Loka Kalyanam: capital does not cover one lot."
        self.position = {
            "side": side,
            "entry_date": _iso(day),
            "entry_time": ENTRY_CLOCK,
            "exit_clock": exit_clock(side),
            "nifty_entry": round(float(nifty), 2),
            "call_action": call_action,
            "call_symbol": call_symbol,
            "call_token": call_token,
            "call_strike": strike,
            "call_entry": round(float(call_entry), 2),
            "put_action": put_action,
            "put_symbol": put_symbol,
            "put_token": put_token,
            "put_strike": strike,
            "put_entry": round(float(put_entry), 2),
            "expiry": expiry,
            "lots": lots,
            "lot_size": int(lot_size),
            "capital_before": _money(self.capital),
            "capital_used": _money(lots * CAPITAL_PER_LOT),
            "entry_stamp": _now(),
        }
        self.save()
        return (
            f"Loka Kalyanam entry {day.strftime('%d-%m-%Y')} 15:00 {side}: "
            f"{call_action.lower()} {strike} call @ {call_entry:,.2f}, "
            f"{put_action.lower()} {strike} put @ {put_entry:,.2f}, "
            f"{lots} lots, Nifty {nifty:,.2f}"
        )

    def close(
        self,
        *,
        exit_on: date,
        nifty_exit: float,
        call_exit: float,
        put_exit: float,
        quote: str,
    ) -> str:
        pos = self.position
        if not pos:
            return "Loka Kalyanam: nothing open."
        side = str(pos["side"])
        points = pair_points(
            side,
            float(pos["call_entry"]),
            call_exit,
            float(pos["put_entry"]),
            put_exit,
        )
        lots = int(pos["lots"])
        lot_size = int(pos["lot_size"])
        gross = _money(points * lot_size * lots)
        costs = pair_costs(
            side,
            date.fromisoformat(str(pos["entry_date"])),
            exit_on,
            float(pos["call_entry"]),
            call_exit,
            float(pos["put_entry"]),
            put_exit,
            lots * lot_size,
        )
        capital_before = float(pos["capital_before"])
        capital_after = _money(capital_before + gross - costs["total"])
        call_close, put_close = closing_orders(side)
        self.closed.append(
            {
                **pos,
                "exit_date": _iso(exit_on),
                "exit_time": pos["exit_clock"],
                "nifty_exit": round(float(nifty_exit), 2),
                "call_exit_action": call_close,
                "call_exit": round(float(call_exit), 2),
                "put_exit_action": put_close,
                "put_exit": round(float(put_exit), 2),
                "exit_quote": quote,
                "points": points,
                "profit": gross,
                "stt": costs["stt"],
                "stamp": costs["stamp"],
                "exchange_sebi": costs["exchange_sebi"],
                "gst": costs["gst"],
                "brokerage": costs["brokerage"],
                "charges": costs["total"],
                "capital_after": capital_after,
                "exit_stamp": _now(),
            }
        )
        self.capital = capital_after
        self.realised_pnl = _money(self.capital - self.starting_capital)
        self.position = None
        self.save()
        return (
            f"Loka Kalyanam exit {exit_on.strftime('%d-%m-%Y')} {pos['exit_clock']} {side}: "
            f"{points:+.2f} pts, profit {gross:,.2f}, charges {costs['total']:,.2f}, "
            f"capital {capital_after:,.2f}"
        )
