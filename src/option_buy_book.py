"""Option-buy paper ledger (BASE ONLY). Live orders are never placed."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.loka_kalyanam import leg_costs
from src.option_buy_strategy import (
    BOOK_PATH,
    JOURNAL_NAME,
    NAME,
    STARTING_CAPITAL,
    size_from_premium,
)
from src.paths import ROOT


def _money(v: float) -> float:
    return round(float(v) + 1e-9, 2)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _iso(day: date) -> str:
    return day.isoformat()


class OptionBuyBook:
    def __init__(self, path: Path | None = None):
        self.path = path or BOOK_PATH
        self.name = NAME
        self.started_on = date.today().isoformat()
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
        self.name = str(raw.get("name") or NAME)
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
            _write_journal(self)
        except Exception as exc:
            print(f"Option-buy journal was not written ({exc})")

    @property
    def is_open(self) -> bool:
        return isinstance(self.position, dict)

    def entered_on(self, day: date) -> bool:
        if self.is_open and self.position.get("entry_date") == _iso(day):
            return True
        return any(t.get("entry_date") == _iso(day) for t in self.closed)

    def enter(
        self,
        *,
        script: str,
        signal_side: str,
        day: date,
        spot: float,
        pcr: float,
        pcr_rank: float,
        opt_side: str,
        symbol: str,
        token: str,
        raw_open: float,
        strike: int,
        expiry: str,
        lot_size: int,
        entry_clock: str = "14:05",
    ) -> str:
        sized = size_from_premium(raw_open, lot_size, script, STARTING_CAPITAL)
        if not sized:
            return (
                f"{self.name}: cannot size {script} {opt_side} "
                f"(raw={raw_open:.2f}, lot={lot_size})."
            )
        self.position = {
            "script": script,
            "tier": "base",
            "signal_side": signal_side,
            "opt_side": opt_side,
            "action": "Buy",
            "entry_date": _iso(day),
            "entry_clock": entry_clock,
            "exit_clock": "15:00",
            "spot_entry": round(float(spot), 2),
            "pcr": round(float(pcr), 4),
            "pcr_rank": round(float(pcr_rank), 4),
            "symbol": symbol,
            "token": token,
            "raw_open": round(float(raw_open), 2),
            "entry": sized["entry"],
            "slip": sized["slip"],
            "strike": int(strike),
            "expiry": expiry,
            "lots": sized["lots"],
            "lot_size": int(lot_size),
            "qty": sized["qty"],
            "per_lot": sized["per_lot"],
            "premium_budget": _money(STARTING_CAPITAL),
            "capital_before": _money(self.capital),
        }
        self.save()
        return (
            f"{self.name}: BASE Buy {opt_side} {script} {sized['lots']} lots @ {strike} "
            f"fill {sized['entry']:.2f} (raw {raw_open:.2f}+slip {sized['slip']:.2f}) "
            f"| PCR rank {pcr_rank:.2%} | paper only"
        )

    def exit(
        self,
        *,
        day: date,
        raw_open: float,
        clock: str = "15:00",
        why: str = "time",
    ) -> str:
        pos = self.position
        if not pos:
            return f"{self.name}: nothing open."
        slip = float(pos["slip"])
        exit_px = round(max(0.05, float(raw_open) - slip), 2)
        qty = int(pos["qty"])
        entry = float(pos["entry"])
        points = exit_px - entry
        gross = points * qty
        costs = leg_costs(day, exit_px, entry, qty)
        net = gross - float(costs["total"])
        self.capital = _money(self.capital + net)
        self.realised_pnl = _money(self.realised_pnl + net)
        trade = {
            **pos,
            "exit_date": _iso(day),
            "exit_clock": clock,
            "exit_why": why,
            "exit_raw": round(float(raw_open), 2),
            "exit": exit_px,
            "points": round(points, 2),
            "gross": _money(gross),
            "stt": costs["stt"],
            "stamp": costs["stamp"],
            "exchange_sebi": costs["exchange_sebi"],
            "gst": costs["gst"],
            "brokerage": costs["brokerage"],
            "charges": costs["total"],
            "net": _money(net),
            "capital_after": _money(self.capital),
        }
        self.closed.append(trade)
        self.position = None
        self.save()
        return (
            f"{self.name}: EXIT Buy {trade['opt_side']} {trade['script']} "
            f"{points:+.2f} pts net ₹{trade['net']:,.0f} "
            f"(charges ₹{costs['total']:,.0f}, {why}) | paper only"
        )


def _write_journal(book: OptionBuyBook) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    path = ROOT / "data" / JOURNAL_NAME
    wb = Workbook()
    ws = wb.active
    ws.title = "Trades"
    hdr = [
        "entry_date",
        "exit_date",
        "script",
        "signal_side",
        "opt_side",
        "strike",
        "expiry",
        "lots",
        "entry",
        "exit",
        "slip",
        "points",
        "gross",
        "charges",
        "net",
        "pcr_rank",
        "exit_why",
    ]
    fill = PatternFill("solid", fgColor="1F4E79")
    font = Font(bold=True, color="FFFFFF")
    ws.append(hdr)
    for c in ws[1]:
        c.fill = fill
        c.font = font
    for t in book.closed:
        ws.append(
            [
                t.get("entry_date"),
                t.get("exit_date"),
                t.get("script"),
                t.get("signal_side"),
                t.get("opt_side"),
                t.get("strike"),
                t.get("expiry"),
                t.get("lots"),
                t.get("entry"),
                t.get("exit"),
                t.get("slip"),
                t.get("points"),
                t.get("gross"),
                t.get("charges"),
                t.get("net"),
                t.get("pcr_rank"),
                t.get("exit_why"),
            ]
        )
    sm = wb.create_sheet("Summary", 0)
    sm.append(["name", book.name])
    sm.append(["capital", book.capital])
    sm.append(["realised_pnl", book.realised_pnl])
    sm.append(["closed", len(book.closed)])
    sm.append(["open", bool(book.is_open)])
    sm.append(["updated_at", book.updated_at])
    wb.save(path)
