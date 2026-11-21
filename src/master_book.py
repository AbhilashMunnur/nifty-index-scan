"""Master Strategy paper ledger. Live orders are never placed."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.loka_kalyanam import pair_costs
from src.master_strategy import (
    BOOK_PATH,
    JOURNAL_NAME,
    NAME,
    PARTIAL_FRAC,
    STARTING_CAPITAL,
    lots_for,
    margin_per_lot,
    opening_orders,
    synth_points,
    tp1_for,
)
from src.paths import ROOT


def _money(v: float) -> float:
    return round(float(v) + 1e-9, 2)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _iso(day: date) -> str:
    return day.isoformat()


class MasterBook:
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
            print(f"Master Strategy journal was not written ({exc})")

    @property
    def is_open(self) -> bool:
        return isinstance(self.position, dict) and float(self.position.get("remaining_frac") or 0) > 1e-9

    def entered_on(self, day: date) -> bool:
        if self.is_open and self.position.get("entry_date") == _iso(day):
            return True
        return any(t.get("entry_date") == _iso(day) for t in self.closed)

    def enter(
        self,
        *,
        script: str,
        tier: str,
        side: str,
        day: date,
        spot: float,
        pcr: float,
        pcr_rank: float,
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
        mpl = margin_per_lot(script, spot, lot_size)
        lots = lots_for(self.capital, mpl)
        if lots < 1 or lot_size < 1:
            return f"{self.name}: capital does not cover one {script} lot (mpl≈₹{mpl:,.0f})."
        call_action, put_action = opening_orders(side)
        self.position = {
            "script": script,
            "tier": tier,
            "side": side,
            "entry_date": _iso(day),
            "entry_clock": "14:05",
            "exit_clock": "15:00",
            "spot_entry": round(float(spot), 2),
            "pcr": round(float(pcr), 4),
            "pcr_rank": round(float(pcr_rank), 4),
            "call_action": call_action,
            "call_symbol": call_symbol,
            "call_token": call_token,
            "call_entry": round(float(call_entry), 2),
            "put_action": put_action,
            "put_symbol": put_symbol,
            "put_token": put_token,
            "put_entry": round(float(put_entry), 2),
            "strike": int(strike),
            "expiry": expiry,
            "lots": int(lots),
            "lot_size": int(lot_size),
            "qty": int(lots) * int(lot_size),
            "remaining_frac": 1.0,
            "partial_done": False,
            "tp1": tp1_for(script),
            "realized_points": 0.0,
            "capital_before": _money(self.capital),
            "margin_per_lot": _money(mpl),
        }
        self.save()
        return (
            f"{self.name}: {tier.upper()} {side} {script} {lots} lots @ {strike} "
            f"CE {call_entry:.2f} / PE {put_entry:.2f} | PCR rank {pcr_rank:.2%} | paper only"
        )

    def mark_points(self, call_px: float, put_px: float) -> float:
        pos = self.position
        if not pos:
            return 0.0
        return synth_points(pos["side"], pos["call_entry"], pos["put_entry"], call_px, put_px)

    def partial_book(self, *, day: date, call_px: float, put_px: float, why: str = "partial_tp1") -> str:
        pos = self.position
        if not pos or pos.get("partial_done") or pos.get("tier") != "filler":
            return f"{self.name}: no filler partial due."
        pts = self.mark_points(call_px, put_px)
        if pts < float(pos["tp1"]):
            return f"{self.name}: partial wait — mark {pts:.1f} < TP1 {pos['tp1']:.0f}."
        frac = PARTIAL_FRAC
        pos["realized_points"] = float(pos.get("realized_points") or 0) + pts * frac
        pos["remaining_frac"] = float(pos["remaining_frac"]) - frac
        pos["partial_done"] = True
        pos["partial"] = {
            "date": _iso(day),
            "points": round(pts, 2),
            "frac": frac,
            "call_px": round(float(call_px), 2),
            "put_px": round(float(put_px), 2),
            "why": why,
        }
        # Cash PnL for booked slice
        qty_slice = int(round(pos["qty"] * frac))
        costs = pair_costs(
            pos["side"],
            date.fromisoformat(pos["entry_date"]),
            day,
            pos["call_entry"],
            call_px,
            pos["put_entry"],
            put_px,
            qty_slice,
        )
        gross = pts * qty_slice
        net = gross - float(costs["total"])
        self.capital = _money(self.capital + net)
        self.realised_pnl = _money(self.realised_pnl + net)
        pos["partial"]["net"] = _money(net)
        self.save()
        return (
            f"{self.name}: PARTIAL {frac:.0%} {pos['script']} @ {pts:.1f} pts "
            f"net ₹{net:,.0f} | rem {pos['remaining_frac']:.0%} | paper only"
        )

    def exit(
        self,
        *,
        day: date,
        call_px: float,
        put_px: float,
        clock: str = "15:00",
        why: str = "time",
    ) -> str:
        pos = self.position
        if not pos:
            return f"{self.name}: nothing open."
        rem = float(pos.get("remaining_frac") or 0)
        pts_mark = self.mark_points(call_px, put_px)
        realized = float(pos.get("realized_points") or 0) + pts_mark * rem
        qty_rem = int(round(pos["qty"] * rem))
        costs = pair_costs(
            pos["side"],
            date.fromisoformat(pos["entry_date"]),
            day,
            pos["call_entry"],
            call_px,
            pos["put_entry"],
            put_px,
            max(qty_rem, 0),
        )
        gross = pts_mark * qty_rem
        net = (gross - float(costs["total"])) if qty_rem > 0 else 0.0
        # If partial already booked cash, only add remaining net
        self.capital = _money(self.capital + net)
        self.realised_pnl = _money(self.realised_pnl + net)
        trade = {
            **{k: pos[k] for k in pos if k not in ("remaining_frac",)},
            "exit_date": _iso(day),
            "exit_clock": clock,
            "exit_why": why,
            "call_exit": round(float(call_px), 2),
            "put_exit": round(float(put_px), 2),
            "points": round(realized, 2),
            "points_final_leg": round(pts_mark, 2),
            "net_final_leg": _money(net),
            "capital_after": _money(self.capital),
        }
        # total net including prior partial
        partial_net = float((pos.get("partial") or {}).get("net") or 0)
        trade["net"] = _money(partial_net + net)
        self.closed.append(trade)
        self.position = None
        self.save()
        return (
            f"{self.name}: EXIT {trade['tier']} {trade['side']} {trade['script']} "
            f"{realized:.1f} pts net ₹{trade['net']:,.0f} ({why}) | paper only"
        )


def _write_journal(book: MasterBook) -> None:
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
        "tier",
        "side",
        "strike",
        "expiry",
        "lots",
        "pcr_rank",
        "points",
        "net",
        "exit_why",
        "partial",
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
                t.get("tier"),
                t.get("side"),
                t.get("strike"),
                t.get("expiry"),
                t.get("lots"),
                t.get("pcr_rank"),
                t.get("points"),
                t.get("net"),
                t.get("exit_why"),
                json.dumps(t.get("partial")) if t.get("partial") else "",
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
