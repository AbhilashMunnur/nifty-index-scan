from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrikePcr:
    label: str
    strike: float
    call_oi: int
    put_oi: int
    call_token: str = ""
    put_token: str = ""
    call_oi_change: int | None = None
    put_oi_change: int | None = None
    call_ltp: float | None = None
    put_ltp: float | None = None

    @property
    def pcr(self) -> float | None:
        if self.call_oi <= 0:
            return None
        return self.put_oi / self.call_oi

    @property
    def change_pcr(self) -> float | None:
        if self.call_oi_change in (None, 0) or self.put_oi_change is None:
            return None
        return self.put_oi_change / self.call_oi_change


@dataclass
class BandPcr:
    expiry: str
    atm: float
    step: float
    lot_size: int
    strikes: list[StrikePcr] = field(default_factory=list)

    @property
    def call_oi(self) -> int:
        return sum(row.call_oi for row in self.strikes)

    @property
    def put_oi(self) -> int:
        return sum(row.put_oi for row in self.strikes)

    @property
    def call_oi_change(self) -> int | None:
        values = [row.call_oi_change for row in self.strikes]
        if any(value is None for value in values):
            return None
        return sum(values)  # type: ignore[arg-type]

    @property
    def put_oi_change(self) -> int | None:
        values = [row.put_oi_change for row in self.strikes]
        if any(value is None for value in values):
            return None
        return sum(values)  # type: ignore[arg-type]

    @property
    def pcr(self) -> float | None:
        if self.call_oi <= 0:
            return None
        return self.put_oi / self.call_oi

    @property
    def change_pcr(self) -> float | None:
        call_change, put_change = self.call_oi_change, self.put_oi_change
        if call_change in (None, 0) or put_change is None:
            return None
        return put_change / call_change

    def strike(self, label: str) -> StrikePcr | None:
        for row in self.strikes:
            if row.label == label:
                return row
        return None

    @property
    def atm_change_pcr(self) -> float | None:
        row = self.strike("ATM")
        return None if row is None else row.change_pcr
