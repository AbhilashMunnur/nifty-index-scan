"""Loka OI 60 paper book on the Nifty future. Live orders are never placed.

The side is the Loka OI 60 decision. A buy closes at the next session's
09:15 open. A short closes at the next session's 14:00 open. Two points
of slippage sit in the points, and the futures charge schedule is applied.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.loka_kalyanam import CAPITAL_PER_LOT, STARTING_CAPITAL, _money, lots_for
from src.paths import ROOT

NAME = "Loka OI 60 futures"
BOOK_PATH = ROOT / "data" / "loka_oi_60_futures_book.json"
JOURNAL_NAME = "loka_oi_60_futures_journal.xlsx"
SLIP_POINTS = 2.0
ENTRY_CLOCK = "15:00"
BUY_EXIT = "09:15"
SHORT_EXIT = "14:00"


def exit_clock(side: str) -> str:
    return BUY_EXIT if side == "Buy" else SHORT_EXIT


def stt_rate(on: date) -> float:
    """STT on the sell side of a Nifty future."""
    if on < date(2023, 4, 1):
        return 0.0001
    if on < date(2024, 10, 1):
        return 0.000125
    if on < date(2026, 4, 1):
        return 0.0002
    return 0.0005


def futures_points(side: str, entry: float, exit_price: float) -> float:
    raw = (exit_price - entry) if side == "Buy" else (entry - exit_price)
    return round(raw - SLIP_POINTS, 2)


def futures_charges(
    side: str,
    entry: float,
    exit_price: float,
    qty: int,
    entry_on: date,
    exit_on: date,
) -> dict[str, float]:
    """STT on the sale, stamp on the buy, exchange and SEBI on both sides."""
    sell_px = exit_price if side == "Buy" else entry
    buy_px = entry if side == "Buy" else exit_price
    sell_on = exit_on if side == "Buy" else entry_on
    sell_to = sell_px * qty
    buy_to = buy_px * qty
    stt = _money(sell_to * stt_rate(sell_on))
    stamp = _money(buy_to * 0.00002)
    exchange_sebi = _money((buy_to + sell_to) * 193 / 1e7)
    brokerage = 40.0
    gst = _money(0.18 * (brokerage + exchange_sebi))
    parts = {
        "stt": stt,
        "stamp": stamp,
        "exchange_sebi": exchange_sebi,
        "gst": gst,
        "brokerage": brokerage,
    }
    parts["total"] = _money(sum(parts.values()))
    return parts


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _iso(day: date) -> str:
    return day.isoformat()


class LokaFuturesBook:
    def __init__(self, path: Path | None = None):
        self.path = path or BOOK_PATH
        self.name = NAME
        self.journal_name = JOURNAL_NAME
        self.started_on = "2026-11-14"
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
        self.name = str(raw.get("name") or self.name)
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
            print(f"{self.name} journal was not written ({exc})")

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
        price: float,
        tradingsymbol: str,
        token: str,
        expiry: str,
        lot_size: int,
        signal: str = "",
        pcr: float | None = None,
        pcr_rank: float | None = None,
    ) -> str:
        lots = lots_for(self.capital)
        if lots < 1 or lot_size < 1:
            return f"{self.name}: capital does not cover one lot."
        self.position = {
            "side": side,
            "entry_date": _iso(day),
            "entry_time": ENTRY_CLOCK,
            "exit_clock": exit_clock(side),
            "signal": signal,
            "pcr": None if pcr is None else round(float(pcr), 3),
            "pcr_rank": None if pcr_rank is None else round(float(pcr_rank), 1),
            "tradingsymbol": tradingsymbol,
            "token": str(token),
            "expiry": expiry,
            "price": round(float(price), 2),
            "lots": lots,
            "lot_size": int(lot_size),
            "capital_before": _money(self.capital),
            "capital_used": _money(lots * CAPITAL_PER_LOT),
            "entry_stamp": _now(),
        }
        self.save()
        marked = f", {signal}" if signal else ""
        return (
            f"{self.name} entry {day.strftime('%d-%m-%Y')} 15:00 {side}{marked}: "
            f"{tradingsymbol} @ {price:,.2f}, {lots} lots"
        )

    def close(self, *, exit_on: date, price: float, quote: str = "Market") -> str:
        pos = self.position
        if not pos:
            return f"{self.name}: nothing open."
        side = str(pos["side"])
        entry = float(pos["price"])
        points = futures_points(side, entry, price)
        lots = int(pos["lots"])
        qty = lots * int(pos["lot_size"])
        gross = _money(points * qty)
        costs = futures_charges(
            side,
            entry,
            price,
            qty,
            date.fromisoformat(str(pos["entry_date"])),
            exit_on,
        )
        capital_before = float(pos["capital_before"])
        capital_after = _money(capital_before + gross - costs["total"])
        self.closed.append(
            {
                **pos,
                "exit_date": _iso(exit_on),
                "exit_time": pos["exit_clock"],
                "exit_price": round(float(price), 2),
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
            f"{self.name} exit {exit_on.strftime('%d-%m-%Y')} {pos['exit_clock']} {side}: "
            f"{pos['tradingsymbol']} @ {price:,.2f}, {points:+.2f} pts, "
            f"charges {costs['total']:,.2f}, capital {capital_after:,.2f}"
        )


def _dmy(value) -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}-{text[5:7]}-{text[0:4]}"
    return text


def _write_journal(book: LokaFuturesBook) -> Path:
    path = book.path.with_name(book.journal_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "When",
        "Detail",
        "Price",
        "Points",
        "Profit",
        "Charges",
        "Capital",
        "Lots",
        "Capital before",
        "Capital used",
    ]
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Journal"
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    body = Font(name="Calibri", size=11)
    exit_fill = PatternFill("solid", fgColor="EAF1F8")
    thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    rows: list[tuple[dict, bool]] = []
    for trade in book.closed:
        if isinstance(trade, dict):
            rows.append((trade, False))
            rows.append((trade, True))
    if book.is_open and isinstance(book.position, dict):
        rows.append((book.position, False))
    for trade, is_exit in rows:
        day = _dmy(trade.get("entry_date"))
        if is_exit:
            when = f"exit {trade.get('exit_time') or ''} -- {_dmy(trade.get('exit_date'))}"
            detail = (
                f"entry {day} · closed {_dmy(trade.get('exit_date'))} · {trade.get('side')} · "
                f"{trade.get('signal') or ''} · {trade.get('tradingsymbol')}"
            )
            price = trade.get("exit_price")
            points = trade.get("points")
            profit = trade.get("profit")
            charges = trade.get("charges")
            capital = trade.get("capital_after")
            before = None
            used = None
        else:
            when = f"entry {trade.get('entry_time') or '15:00'} -- {day}"
            detail = (
                f"entry {day} · {trade.get('side')} · {trade.get('signal') or ''} · "
                f"{trade.get('tradingsymbol')} · closes {trade.get('exit_clock')}"
            )
            price = trade.get("price")
            points = profit = charges = capital = None
            before = trade.get("capital_before")
            used = trade.get("capital_used")
        sheet.append([when, detail, price, points, profit, charges, capital, trade.get("lots"), before, used])
        for cell in sheet[sheet.max_row]:
            cell.font = body
            cell.border = thin
            if is_exit:
                cell.fill = exit_fill
            if cell.column_letter in {"C", "E", "F", "G", "I", "J"} and isinstance(cell.value, (int, float)):
                cell.number_format = '"₹"#,##0.00'
            if cell.column_letter == "D" and isinstance(cell.value, (int, float)):
                cell.number_format = "0.00"
    for letter, width in {
        "A": 28, "B": 72, "C": 14, "D": 12, "E": 16, "F": 14, "G": 18, "H": 10, "I": 18, "J": 18,
    }.items():
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(sheet.max_row, 1)}"
    wb.save(path)
    return path
