"""Broker order adapter for Master Strategy.

PaperBroker: records intents only (default).
LiveBroker: gated — raises unless MASTER_LIVE_ORDERS=1 is set explicitly.
Live Angel One orders stay off by default for safety.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from src.paths import ROOT

INTENT_LOG = ROOT / "data" / "master_order_intents.jsonl"


@dataclass
class OrderIntent:
    action: str  # Buy | Sell
    symbol: str
    token: str
    qty: int
    price: float | None
    product: str = "CARRYFORWARD"
    order_type: str = "MARKET"
    tag: str = "master"
    note: str = ""


class BrokerAdapter(Protocol):
    name: str

    def place(self, intent: OrderIntent) -> dict: ...

    def cancel_all(self, tag: str = "master") -> None: ...


class PaperBroker:
    """No exchange orders — log intents for audit / later live wiring."""

    name = "paper"

    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path or INTENT_LOG

    def place(self, intent: OrderIntent) -> dict:
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "paper",
            **asdict(intent),
            "status": "PAPER_FILLED",
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    def cancel_all(self, tag: str = "master") -> None:
        return None


class LiveBroker:
    """Placeholder for Angel/Fyers live orders. Disabled unless env unlock."""

    name = "live"

    def __init__(self):
        unlocked = os.getenv("MASTER_LIVE_ORDERS", "").strip() == "1"
        if not unlocked:
            raise RuntimeError(
                "LiveBroker blocked: set MASTER_LIVE_ORDERS=1 only after paper soak + risk checks."
            )
        raise NotImplementedError(
            "Live order placement is not implemented yet — next development phase."
        )

    def place(self, intent: OrderIntent) -> dict:
        raise NotImplementedError

    def cancel_all(self, tag: str = "master") -> None:
        raise NotImplementedError


def get_broker(mode: str = "paper") -> BrokerAdapter:
    mode = (mode or "paper").strip().lower()
    if mode == "paper":
        return PaperBroker()
    if mode == "live":
        return LiveBroker()
    raise ValueError("broker mode must be paper|live")
