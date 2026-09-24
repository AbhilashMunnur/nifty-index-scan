from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.paths import CONFIG_PATH, ROOT
from src.strategy import StrategyRules
from src.universe import ALLOWED_SYMBOLS, validate_universe


@dataclass(frozen=True)
class AppConfig:
    universe: list[str]
    mode: str  # observe | paper
    console: bool
    telegram: bool
    telegram_every_scan: bool
    interval_minutes: int
    market_start: str
    market_end: str
    paper_end: str
    google_sheet_id: str
    google_worksheets: list[str]
    strategy_symbol: str
    strategy_symbols: list[str]
    lots: int
    rules: StrategyRules


def load_config(path: Path | None = None) -> AppConfig:
    raw = yaml.safe_load((path or CONFIG_PATH).read_text()) or {}
    universe = validate_universe(raw.get("universe") or list(ALLOWED_SYMBOLS))
    mode = str(raw.get("mode") or "paper").strip().lower()
    if mode not in {"observe", "paper"}:
        raise ValueError("mode must be 'observe' or 'paper' (live orders are disabled).")

    notifications = raw.get("notifications") or {}
    sheets = raw.get("google_sheets") or {}
    strategy = raw.get("strategy") or {}
    extra_symbols = strategy.get("symbols")
    if extra_symbols:
        strategy_symbols = validate_universe(extra_symbols)
    else:
        strategy_symbols = list(universe)
    symbol = str(strategy.get("symbol") or strategy_symbols[0]).upper()
    validate_universe([symbol])
    if symbol not in strategy_symbols:
        strategy_symbols = [symbol] + strategy_symbols

    rules = StrategyRules(
        rsi_short=float(strategy.get("rsi_short", 70)),
        rsi_long=float(strategy.get("rsi_long", 30)),
        rsi_cover_short=float(strategy.get("rsi_cover_short", 35)),
        rsi_cover_long=float(strategy.get("rsi_cover_long", 65)),
        change_pcr_short_max=float(strategy.get("change_pcr_short_max", 0.8)),
        change_pcr_long_min=float(strategy.get("change_pcr_long_min", 1.0)),
        min_trading_days_to_expiry=int(strategy.get("min_trading_days_to_expiry", 10)),
        rsi_period=int(strategy.get("rsi_period", 14)),
        first_target_pct=float(strategy.get("first_target_pct", 1.5)),
        stop_loss_pct=float(strategy.get("stop_loss_pct", 0.75)),
        lots=int(strategy.get("lots", 2)),
    )
    return AppConfig(
        universe=universe,
        mode=mode,
        console=bool(notifications.get("console", True)),
        telegram=bool(notifications.get("telegram", False)),
        telegram_every_scan=bool(notifications.get("telegram_every_scan", True)),
        interval_minutes=int(notifications.get("interval_minutes", 15)),
        market_start=str(notifications.get("market_start") or "09:30"),
        market_end=str(notifications.get("market_end") or "15:40"),
        paper_end=str(notifications.get("paper_end") or "15:30"),
        google_sheet_id=str(sheets.get("sheet_id") or ""),
        google_worksheets=list(sheets.get("worksheets") or []),
        strategy_symbol=symbol,
        strategy_symbols=strategy_symbols,
        lots=rules.lots,
        rules=rules,
    )


def project_root() -> Path:
    return ROOT
