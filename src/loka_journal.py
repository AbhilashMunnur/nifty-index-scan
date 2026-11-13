"""Excel journal for Loka Kalyanam. One row is an entry, the next is its exit."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

JOURNAL_NAME = "loka_kalyanam_journal.xlsx"
HEADERS = (
    "When",
    "Detail",
    "Call",
    "Put",
    "Profit",
    "Points",
    "Charges",
    "Capital",
    "Lots",
    "Capital before",
    "Capital used",
)


def journal_path_for(book_path: Path) -> Path:
    return book_path.with_name(JOURNAL_NAME)


def write_journal(book) -> Path:
    """Rewrite the workbook from the paper book. Closed trades, then the open entry."""
    filename = getattr(book, "journal_name", None) or JOURNAL_NAME
    path = book.path.with_name(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows(book)
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Journal"
    _write_sheet(sheet, rows)
    _write_book(wb.create_sheet("Book"), book, len(rows))
    wb.save(path)
    return path


def _rows(book) -> list[dict]:
    rows: list[dict] = []
    for trade in book.closed:
        if isinstance(trade, dict):
            rows.append(_entry(trade))
            rows.append(_exit(trade))
    if book.is_open and isinstance(book.position, dict):
        rows.append(_entry(book.position))
    return rows


def _entry(trade: dict) -> dict:
    day = _dmy(trade.get("entry_date"))
    nifty = _num(trade.get("nifty_entry"))
    return {
        "when": f"entry {trade.get('entry_time') or '15:00'} -- {day}",
        "detail": f"entry {day} · {_side_label(trade)} · Nifty {_grouped(nifty)}",
        "call": _leg(trade.get("call_action"), trade.get("call_strike"), "call", trade.get("call_entry")),
        "put": _leg(trade.get("put_action"), trade.get("put_strike"), "put", trade.get("put_entry")),
        "profit": None,
        "points": None,
        "charges": None,
        "capital": None,
        "lots": trade.get("lots"),
        "before": trade.get("capital_before"),
        "used": trade.get("capital_used"),
        "exit": False,
    }


def _exit(trade: dict) -> dict:
    entered = _dmy(trade.get("entry_date"))
    closed = _dmy(trade.get("exit_date"))
    nifty = _num(trade.get("nifty_exit"))
    return {
        "when": f"exit {trade.get('exit_time') or ''} -- {closed}",
        "detail": (
            f"entry {entered} · closed {closed} · {_side_label(trade)} · Nifty {_grouped(nifty)}"
        ),
        "call": _leg(trade.get("call_exit_action"), trade.get("call_strike"), "call", trade.get("call_exit")),
        "put": _leg(trade.get("put_exit_action"), trade.get("put_strike"), "put", trade.get("put_exit")),
        "profit": trade.get("profit"),
        "points": trade.get("points"),
        "charges": trade.get("charges"),
        "capital": trade.get("capital_after"),
        "lots": trade.get("lots"),
        "before": None,
        "used": None,
        "exit": True,
    }


def _side_label(trade: dict) -> str:
    signal = str(trade.get("signal") or "")
    side = str(trade.get("side") or "")
    return f"{side} · {signal}" if signal else side


def _leg(action, strike, kind: str, price) -> str:
    try:
        number = int(float(strike))
    except (TypeError, ValueError):
        number = strike
    return f"{action} {number} {kind} @ {_grouped(_num(price))}"


def _dmy(value) -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}-{text[5:7]}-{text[0:4]}"
    return text


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _grouped(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) <= 3:
        body = whole
    else:
        head, tail = whole[:-3], whole[-3:]
        chunks: list[str] = []
        while len(head) > 2:
            chunks.append(head[-2:])
            head = head[:-2]
        if head:
            chunks.append(head)
        body = ",".join(reversed(chunks)) + "," + tail
    return f"{sign}{body}.{frac}"


def _write_sheet(sheet, rows: list[dict]) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    body = Font(name="Calibri", size=11)
    exit_fill = PatternFill("solid", fgColor="EFE8DF")
    thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    sheet.append(list(HEADERS))
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    money = {"E", "G", "H", "J", "K"}
    for row in rows:
        sheet.append(
            [
                row["when"],
                row["detail"],
                row["call"],
                row["put"],
                row["profit"],
                row["points"],
                row["charges"],
                row["capital"],
                row["lots"],
                row["before"],
                row["used"],
            ]
        )
        excel_row = sheet.max_row
        for cell in sheet[excel_row]:
            cell.font = body
            cell.border = thin
            cell.alignment = Alignment(vertical="center")
            if row["exit"]:
                cell.fill = exit_fill
            if cell.column_letter in money and isinstance(cell.value, (int, float)):
                cell.number_format = '"₹"#,##0.00'
            if cell.column_letter == "F" and isinstance(cell.value, (int, float)):
                cell.number_format = "0.00"
    widths = {
        "A": 28, "B": 62, "C": 28, "D": 28, "E": 16, "F": 12,
        "G": 14, "H": 18, "I": 10, "J": 18, "K": 18,
    }
    for letter, width in widths.items():
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(sheet.max_row, 1)}"
    sheet.sheet_view.showGridLines = False


def _write_book(sheet, book, trades: int) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    body = Font(name="Calibri", size=11)
    sheet.append(("Item", "Value"))
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
    lines = (
        ("Name", book.name),
        ("Started", _dmy(book.started_on)),
        ("Starting capital", book.starting_capital),
        ("Capital", book.capital),
        ("Profit and loss", book.realised_pnl),
        ("Rows", trades),
        ("Updated", book.updated_at),
        ("Note", "Each trade is an entry row and, once it is closed, the exit row under it."),
    )
    for item, value in lines:
        sheet.append((item, value))
        sheet.cell(sheet.max_row, 1).font = body
        cell = sheet.cell(sheet.max_row, 2)
        cell.font = body
        if isinstance(value, float):
            cell.number_format = '"₹"#,##0.00'
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 78
