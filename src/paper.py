"""Paper index futures books. Live Angel One orders are never placed."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.models import FutureContract
from src.paths import ROOT
from src.strategy import (
    StrategyRules,
    first_target_price,
    remaining_lot_exit,
    scale_lot_hit,
    stop_loss_hit,
    stop_loss_price,
)

LEDGER_PATH = ROOT / "data" / "nifty_paper_book.json"


def ledger_path_for(symbol: str) -> Path:
    key = str(symbol).strip().upper()
    if key == "NIFTY":
        return LEDGER_PATH
    return ROOT / "data" / f"{key.lower()}_paper_book.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class NiftyPaperBook:
    def __init__(
        self,
        path: Path | None = None,
        lots: int = 2,
        symbol: str = "NIFTY",
    ):
        self.symbol = str(symbol).strip().upper()
        self.path = path or ledger_path_for(self.symbol)
        self.lots = lots
        self.position: dict | None = None
        self.closed: list[dict] = []
        self.realised_pnl: float = 0.0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.position = raw.get("position")
        self.closed = list(raw.get("closed") or [])
        self.realised_pnl = float(raw.get("realised_pnl") or 0)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "updated_at": _now(),
                    "realised_pnl": round(self.realised_pnl, 2),
                    "position": self.position,
                    "closed": self.closed,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @property
    def is_open(self) -> bool:
        return bool(self.position) and int(self.position.get("lots") or 0) > 0

    def _pnl(self, direction: str, entry: float, exit_price: float, lot_size: int, lots: int) -> float:
        move = exit_price - entry
        if direction == "SHORT":
            move = -move
        return move * lot_size * lots

    def _close_lots(self, lots: int, price: float, reason: str) -> str:
        pos = self.position
        assert pos is not None
        lots = min(lots, int(pos["lots"]))
        pnl = self._pnl(
            pos["direction"],
            float(pos["entry_price"]),
            price,
            int(pos["lot_size"]),
            lots,
        )
        self.realised_pnl += pnl
        self.closed.append(
            {
                "direction": pos["direction"],
                "tradingsymbol": pos["tradingsymbol"],
                "expiry": pos["expiry"],
                "lots": lots,
                "entry_time": pos["entry_time"],
                "entry_price": pos["entry_price"],
                "entry_rsi": pos.get("entry_rsi"),
                "exit_time": _now(),
                "exit_price": round(price, 2),
                "pnl": round(pnl, 2),
                "exit_reason": reason,
            }
        )
        pos["lots"] = int(pos["lots"]) - lots
        unit = "lot" if lots == 1 else "lots"
        return (
            f"Booked {lots} {unit} {pos['direction']} {pos['tradingsymbol']} "
            f"@ {price:,.2f}  P&L {pnl:,.0f}  ({reason})"
        )

    def manage(
        self,
        price: float,
        rsi: float | None,
        atm_change_pcr: float | None,
        rules: StrategyRules,
    ) -> list[str]:
        """Stop 0.75% against entry closes all lots. Else RSI+ATM ΔPCR closes
        all remaining. Else 1.5% in favour books one lot.
        """
        notes: list[str] = []
        if not self.is_open:
            return notes

        pos = self.position
        assert pos is not None
        direction = str(pos["direction"])
        entry = float(pos["entry_price"])
        stop_pct = rules.stop_loss_pct

        if stop_loss_hit(direction, entry, price, stop_pct):
            stop = stop_loss_price(direction, entry, stop_pct)
            remaining = int(pos["lots"])
            notes.append(
                self._close_lots(
                    remaining,
                    stop,
                    f"SL {stop_pct:g}% against entry",
                )
            )
            pos["scale_lot_open"] = False
            pos["rsi_lot_open"] = False
        else:
            rsi_reason = remaining_lot_exit(direction, rsi, atm_change_pcr, rules)
            if rsi_reason:
                remaining = int(pos["lots"])
                notes.append(self._close_lots(remaining, price, rsi_reason))
                pos["scale_lot_open"] = False
                pos["rsi_lot_open"] = False
            elif pos.get("scale_lot_open") and scale_lot_hit(
                direction, entry, price, rules.first_target_pct
            ):
                target = first_target_price(direction, entry, rules.first_target_pct)
                notes.append(
                    self._close_lots(
                        1,
                        target,
                        f"{rules.first_target_pct:g}% in favour",
                    )
                )
                pos["scale_lot_open"] = False

        if not self.is_open:
            self.position = None
        self.save()
        return notes

    def apply(
        self,
        signal: str,
        contract: FutureContract,
        price: float,
        *,
        rsi: float | None,
        pcr: float | None,
        change_pcr: float | None,
        reason: str,
        rules: StrategyRules | None = None,
    ) -> str:
        """Enter 2 lots when flat. Does not reverse an open scale-out."""
        lots = (rules.lots if rules else None) or self.lots
        if self.is_open:
            return (
                f"Already {self.position['direction']} "
                f"{self.position['lots']} lot {self.position['tradingsymbol']} "
                f"@ {self.position['entry_price']:,.2f} — waiting on 1.5% / RSI / SL"
            )

        target_pct = rules.first_target_pct if rules else 1.5
        stop_pct = rules.stop_loss_pct if rules else 0.75
        self.position = {
            "direction": signal,
            "tradingsymbol": contract.tradingsymbol,
            "expiry": contract.expiry,
            "token": contract.token,
            "lot_size": contract.lot_size,
            "lots": lots,
            "scale_lot_open": True,
            "rsi_lot_open": True,
            "first_target_pct": target_pct,
            "first_target_price": round(
                first_target_price(signal, price, target_pct), 2
            ),
            "stop_loss_pct": stop_pct,
            "stop_loss_price": round(stop_loss_price(signal, price, stop_pct), 2),
            "entry_time": _now(),
            "entry_price": price,
            "entry_rsi": None if rsi is None else round(rsi, 1),
            "pcr": None if pcr is None else round(pcr, 2),
            "change_pcr": None if change_pcr is None else round(change_pcr, 2),
            "reason": reason,
        }
        self.save()
        return (
            f"Paper {signal} {lots} lots {contract.tradingsymbol} @ {price:,.2f} "
            f"(1 lot at {target_pct:g}% if it hits first; "
            f"RSI + ATM ΔPCR books both remaining lots; "
            f"SL {stop_pct:g}% against entry; re-enter if rules fire again; "
            f"live orders off)"
        )
