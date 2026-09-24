"""This project only watches Nifty 50 and Sensex — never stocks or other indices."""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_SYMBOLS = ("NIFTY", "SENSEX")


@dataclass(frozen=True)
class IndexSpec:
    symbol: str
    index_exchange: str  # cash index quote
    fno_exchange: str  # options / futures
    option_type: str
    future_type: str
    index_type: str


SPECS: dict[str, IndexSpec] = {
    "NIFTY": IndexSpec(
        symbol="NIFTY",
        index_exchange="NSE",
        fno_exchange="NFO",
        option_type="OPTIDX",
        future_type="FUTIDX",
        index_type="AMXIDX",
    ),
    "SENSEX": IndexSpec(
        symbol="SENSEX",
        index_exchange="BSE",
        fno_exchange="BFO",
        option_type="OPTIDX",
        future_type="FUTIDX",
        index_type="AMXIDX",
    ),
}


class UniverseError(ValueError):
    """Raised when a symbol outside NIFTY / SENSEX is requested."""


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def require_index(symbol: str) -> IndexSpec:
    key = normalize_symbol(symbol)
    spec = SPECS.get(key)
    if spec is None:
        raise UniverseError(
            f"{symbol!r} is not traded here. This project only observes "
            f"or paper-trades {', '.join(ALLOWED_SYMBOLS)}."
        )
    return spec


def validate_universe(symbols: list[str] | tuple[str, ...]) -> list[str]:
    if not symbols:
        raise UniverseError(
            f"Universe is empty. Use {', '.join(ALLOWED_SYMBOLS)}."
        )
    return [require_index(symbol).symbol for symbol in symbols]
