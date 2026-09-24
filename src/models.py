from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FutureContract:
    symbol: str
    tradingsymbol: str
    token: str
    expiry: str
    lot_size: int
    trading_days_left: int
    exchange: str


@dataclass
class IndexSnapshot:
    symbol: str
    ltp: float
    expiry: str
    lot_size: int
    max_call_oi_strike: float
    max_call_oi: int
    max_put_oi_strike: float
    max_put_oi: int
    futures_symbol: str = ""
    futures_expiry: str = ""
    call_oi_change: int | None = None
    put_oi_change: int | None = None
    max_call_token: str = ""
    max_put_token: str = ""
    legs_by_strike: dict[float, dict[str, tuple[int, str]]] = field(
        default_factory=dict, compare=False, repr=False
    )

    def contracts(self, open_interest: int) -> int | None:
        if self.lot_size <= 0:
            return None
        return int(open_interest / self.lot_size)
