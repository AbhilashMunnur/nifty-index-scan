"""5-year Nifty 1H RSI-only backtest with profit compounding."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.market import IndexMarket
from src.paths import CACHE_DIR, ROOT
from src.strategy import (
    StrategyRules,
    first_target_price,
    remaining_lot_exit_rsi_only,
    split_lots,
    stop_loss_price,
)

CANDLE_PATH = CACHE_DIR / "nifty_1h.csv"
RESULT_PATH = ROOT / "data" / "rsi_only_5y_backtest.json"
LOT_SIZE = 65
MARGIN_PCT = 20.0
START_LOTS = 2


def download_nifty_1h(start: datetime, end: datetime) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)
    if CANDLE_PATH.exists():
        existing = pd.read_csv(CANDLE_PATH, parse_dates=["time"])
        if not existing.empty:
            first, last = existing["time"].min(), existing["time"].max()
            if first <= pd.Timestamp(start) + pd.Timedelta(days=7) and last >= pd.Timestamp(end) - pd.Timedelta(days=3):
                return existing

    market = IndexMarket()
    market._load_instruments()
    token = market._index_tokens["NIFTY"]
    rows: list = []
    cursor = start
    try:
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=90), end)
            print(f"  candles {cursor:%Y-%m-%d} → {chunk_end:%Y-%m-%d}")
            response = market._call(
                market._candle_throttle,
                "getCandleData",
                {
                    "exchange": "NSE",
                    "symboltoken": token,
                    "interval": "ONE_HOUR",
                    "fromdate": cursor.strftime("%Y-%m-%d %H:%M"),
                    "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
                },
            )
            data = (response or {}).get("data") or []
            rows.extend(data)
            cursor = chunk_end + timedelta(minutes=1)
    finally:
        market.close()

    frame = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    frame["time"] = pd.to_datetime(frame["time"])
    for col in ("open", "high", "low", "close"):
        frame[col] = frame[col].astype(float)
    frame = frame.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    frame.to_csv(CANDLE_PATH, index=False)
    return frame


def _rsi_series(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _pnl(direction: str, entry: float, exit_price: float, lots: int) -> float:
    move = exit_price - entry
    if direction == "SHORT":
        move = -move
    return move * LOT_SIZE * lots


def _margin(price: float, lots: int) -> float:
    return price * LOT_SIZE * lots * MARGIN_PCT / 100.0


@dataclass
class OpenPos:
    direction: str
    entry: float
    entry_time: str
    entry_rsi: float
    lots: int
    scale_left: int
    rsi_left: int


def run_backtest(frame: pd.DataFrame, rules: StrategyRules) -> dict:
    df = frame.copy()
    df["rsi"] = _rsi_series(df["close"], rules.rsi_period)
    first_price = float(df["close"].iloc[rules.rsi_period + 2])
    start_capital = _margin(first_price, START_LOTS)
    realised = 0.0
    pos: OpenPos | None = None
    fills: list[dict] = []
    equity_curve: list[dict] = []
    pending_signal: str | None = None

    def lots_affordable(price: float) -> int:
        equity = start_capital + realised
        per = _margin(price, 1)
        if per <= 0:
            return START_LOTS
        extra = max(0, int((equity - start_capital) // per))
        return START_LOTS + extra

    def close_lots(when, direction, entry, exit_price, lots, reason, entry_rsi, rsi_now):
        nonlocal realised, pos
        if lots <= 0:
            return
        pnl = _pnl(direction, entry, exit_price, lots)
        realised += pnl
        fills.append(
            {
                "time": str(when),
                "direction": direction,
                "lots": lots,
                "entry": round(entry, 2),
                "exit": round(exit_price, 2),
                "pnl": round(pnl, 2),
                "reason": reason,
                "entry_rsi": round(entry_rsi, 1),
                "exit_rsi": None if rsi_now != rsi_now else round(float(rsi_now), 1),
                "equity": round(start_capital + realised, 2),
            }
        )
        equity_curve.append({"time": str(when)[:10], "equity": round(start_capital + realised, 2)})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        t, o, h, l, c, rsi = row["time"], float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), row["rsi"]
        if pd.isna(rsi):
            continue

        if pending_signal and pos is None:
            lots = lots_affordable(o)
            scale, rsi_lots = split_lots(lots)
            pos = OpenPos(pending_signal, o, str(t), float(prev["rsi"]), lots, scale, rsi_lots)
            pending_signal = None

        if pos is not None:
            sl = stop_loss_price(pos.direction, pos.entry, rules.stop_loss_pct)
            target = first_target_price(pos.direction, pos.entry, rules.first_target_pct)
            sl_hit = (h >= sl) if pos.direction == "SHORT" else (l <= sl)
            tgt_hit = (l <= target) if pos.direction == "SHORT" else (h >= target)
            rsi_exit = remaining_lot_exit_rsi_only(pos.direction, float(rsi), rules)

            if sl_hit:
                remaining = pos.scale_left + pos.rsi_left
                close_lots(t, pos.direction, pos.entry, sl, remaining, f"SL {rules.stop_loss_pct:g}%", pos.entry_rsi, rsi)
                pos = None
            elif rsi_exit:
                remaining = pos.scale_left + pos.rsi_left
                close_lots(t, pos.direction, pos.entry, c, remaining, rsi_exit, pos.entry_rsi, rsi)
                pos = None
            elif tgt_hit and pos.scale_left:
                close_lots(t, pos.direction, pos.entry, target, pos.scale_left, f"{rules.first_target_pct:g}% in favour", pos.entry_rsi, rsi)
                pos.lots -= pos.scale_left
                pos.scale_left = 0
                if pos.rsi_left <= 0:
                    pos = None

        if pos is None and pending_signal is None:
            prev_rsi = prev["rsi"]
            if pd.notna(prev_rsi):
                if float(prev_rsi) >= rules.rsi_short:
                    pending_signal = "SHORT"
                elif float(prev_rsi) <= rules.rsi_long:
                    pending_signal = "LONG"

    if pos is not None:
        last = df.iloc[-1]
        remaining = pos.scale_left + pos.rsi_left
        close_lots(last["time"], pos.direction, pos.entry, float(last["close"]), remaining, "end of sample", pos.entry_rsi, last["rsi"])

    pnls = [f["pnl"] for f in fills]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    eq = start_capital
    peak = start_capital
    max_dd = 0.0
    max_dd_pct = 0.0
    running = []
    for f in fills:
        eq = f["equity"]
        peak = max(peak, eq)
        dd = peak - eq
        dd_pct = dd / peak * 100 if peak else 0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
        running.append({"time": f["time"][:10], "equity": eq, "lots": None})

    yearly: dict[str, dict] = {}
    for f in fills:
        year = f["time"][:4]
        bucket = yearly.setdefault(year, {"pnl": 0.0, "fills": 0, "wins": 0})
        bucket["pnl"] += f["pnl"]
        bucket["fills"] += 1
        if f["pnl"] > 0:
            bucket["wins"] += 1

    reasons: dict[str, int] = {}
    for f in fills:
        key = "SL" if f["reason"].startswith("SL") else (
            "1.5%" if "1.5%" in f["reason"] else (
                "RSI boundary" if "RSI" in f["reason"] else f["reason"]
            )
        )
        reasons[key] = reasons.get(key, 0) + 1

    shorts = [f for f in fills if f["direction"] == "SHORT"]
    longs = [f for f in fills if f["direction"] == "LONG"]

    # monthly equity for chart — last fill in each month
    monthly = []
    by_month: dict[str, float] = {}
    for f in fills:
        by_month[f["time"][:7]] = f["equity"]
    for month in sorted(by_month):
        monthly.append({"month": month, "equity": by_month[month]})

    end_equity = start_capital + realised
    years = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 365.25
    cagr = ((end_equity / start_capital) ** (1 / years) - 1) * 100 if years > 0 and start_capital > 0 else 0

    return {
        "start": str(df["time"].iloc[0]),
        "end": str(df["time"].iloc[-1]),
        "bars": int(len(df)),
        "lot_size": LOT_SIZE,
        "margin_pct": MARGIN_PCT,
        "start_lots": START_LOTS,
        "start_capital": round(start_capital, 2),
        "end_equity": round(end_equity, 2),
        "net_pnl": round(realised, 2),
        "return_pct": round((end_equity / start_capital - 1) * 100, 2),
        "cagr_pct": round(cagr, 2),
        "fills": len(fills),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(pnls), 1) if pnls else 0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else None,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "short_fills": len(shorts),
        "short_pnl": round(sum(f["pnl"] for f in shorts), 2),
        "long_fills": len(longs),
        "long_pnl": round(sum(f["pnl"] for f in longs), 2),
        "yearly": [
            {
                "year": y,
                "pnl": round(v["pnl"], 2),
                "fills": v["fills"],
                "win_rate_pct": round(100 * v["wins"] / v["fills"], 1) if v["fills"] else 0,
            }
            for y, v in sorted(yearly.items())
        ],
        "exit_reasons": reasons,
        "monthly_equity": monthly,
        "last_fills": fills[-25:],
        "max_lots_used": max((f["lots"] for f in fills), default=0),
        "notes": [
            "Nifty 50 1-hour candles from Angel One, used as a futures proxy (lot 65).",
            "Entries: 1H RSI >= 70 short, RSI <= 30 long. No OI / PCR filter.",
            "Start 2 lots. Extra lots from realised profit covering 20% futures margin.",
            "Even lots: half at 1.5%, half at RSI 35 (short) / 65 (long). Odd: more at 1.5%.",
            "If RSI boundary hits before 1.5%, all remaining lots exit. SL 0.75% against entry.",
            "Same-bar stop vs target: stop is taken first (conservative).",
        ],
    }


def main() -> None:
    start = datetime(2021, 8, 21, 9, 15)
    end = datetime(2026, 8, 21, 15, 30)
    print("Downloading Nifty 1H candles…")
    frame = download_nifty_1h(start, end)
    print(f"{len(frame)} bars  {frame['time'].iloc[0]} → {frame['time'].iloc[-1]}")
    result = run_backtest(frame, StrategyRules())
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in result if k not in {"last_fills", "monthly_equity", "notes"}}, indent=2))
    print("wrote", RESULT_PATH)


if __name__ == "__main__":
    main()
