#!/usr/bin/env python3
"""Backfill Master paper books from 1 Nov 2026 using archived 5m opens.

Live desks keep running after the last archived session. A position is closed
only when the next session's 15:00 open is in the archive. Days still open
are not left hanging, so Monday's live tick does not exit them at the wrong price.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.master_book import MasterBook  # noqa: E402
from src.master_strategy import (  # noqa: E402
    STARTING_CAPITAL,
    classify_tier,
    load_pcr_history,
    pcr_percentile,
    pick_signal,
    synth_entry_fills,
    synth_exit_fills,
    synth_points,
    tp1_for,
)
from src.option_buy_book import OptionBuyBook  # noqa: E402
from src.option_buy_strategy import STARTING_CAPITAL as OB_START  # noqa: E402
from src.option_buy_strategy import opt_side_for, pick_base_signal  # noqa: E402

START = date(2026, 11, 1)
SCRIPTS = ("SENSEX", "NIFTY")  # same universe the live paper desk can trade
PARQUET = {
    "NIFTY": ROOT / "data/fyers/nifty_opt_atm6_5m.parquet",
    "SENSEX": ROOT / "data/fyers/sensex_opt_atm6_5m.parquet",
}


def lot_size(script: str, day: date) -> int:
    """Exchange lot after the Nov 2024 contract-size revision."""
    if script == "NIFTY":
        return 75 if day >= date(2024, 11, 20) else 25
    if script == "SENSEX":
        return 20
    if script == "BANKNIFTY":
        return 30 if day >= date(2024, 11, 20) else 15
    raise KeyError(script)


def load_script(script: str) -> pd.DataFrame:
    df = pd.read_parquet(
        PARQUET[script],
        columns=["datetime", "expiry", "strike", "side", "wing", "open", "spot"],
    )
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df[df["datetime"] >= "2026-10-28"].copy()
    df["day"] = df["datetime"].dt.date
    df["clock"] = df["datetime"].dt.strftime("%H:%M")
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    return df


def session_days(df: pd.DataFrame) -> list[date]:
    bars = df[(df["clock"] == "14:05") & (df["wing"] == 0) & (df["day"] >= START)]
    return sorted(set(bars["day"]))


def next_session(days: list[date], day: date) -> date | None:
    for d in days:
        if d > day:
            return d
    return None


def _both_sides(sub: pd.DataFrame) -> pd.DataFrame:
    counts = sub.groupby("strike")["side"].nunique()
    keep = counts[counts >= 2].index
    return sub[sub["strike"].isin(keep)]


def atm_quote(df: pd.DataFrame, day: date, clock: str, expiry: date | None = None, strike: int | None = None):
    """ATM quote. New entries use the nearest expiry that is still alive next session."""
    sub = df[(df["day"] == day) & (df["clock"] == clock)]
    if expiry is not None and strike is not None:
        sub = sub[(sub["expiry"] == expiry) & (sub["strike"] == strike)]
    else:
        alive = sub[sub["expiry"] > day]
        if alive.empty:
            return None
        expiry = min(alive["expiry"])
        sub = alive[alive["expiry"] == expiry]
        paired = _both_sides(sub)
        if paired.empty:
            return None
        wing0 = paired[paired["wing"] == 0]
        if not wing0.empty:
            strike = int(wing0["strike"].iloc[0])
            sub = wing0
        else:
            spot_vals = paired["spot"].dropna()
            spot = float(spot_vals.iloc[0]) if not spot_vals.empty else float(paired["strike"].median())
            strike = int(min(paired["strike"].unique(), key=lambda k: abs(float(k) - spot)))
            sub = paired[paired["strike"] == strike]
    if sub.empty:
        return None
    spot_vals = sub["spot"].dropna()
    spot = float(spot_vals.iloc[0]) if not spot_vals.empty else float(strike)
    ce = sub[sub["side"] == "CE"]
    pe = sub[sub["side"] == "PE"]
    if ce.empty or pe.empty:
        return None
    return {
        "expiry": expiry,
        "strike": int(strike),
        "spot": spot,
        "ce": float(ce["open"].iloc[0]),
        "pe": float(pe["open"].iloc[0]),
    }


def rank_on(hist: dict, script: str, day: date) -> tuple[float, float] | None:
    series = hist.get(script) or {}
    days = sorted(d for d in series if d <= day.isoformat())
    if day.isoformat() not in series:
        return None
    vals = [series[d] for d in days]
    rank = pcr_percentile(vals, 60)
    if rank is None:
        return None
    return float(series[day.isoformat()]), float(rank)


def candidates(hist, day: date) -> list[dict]:
    out = []
    for script in SCRIPTS:
        got = rank_on(hist, script, day)
        if not got:
            continue
        pcr, rank = got
        tier, side = classify_tier(script, rank)
        if tier and side:
            out.append(dict(script=script, tier=tier, side=side, rank=rank, pcr=pcr, day=day.isoformat()))
    return out


def symbol(script: str, expiry: date, strike: int, opt: str) -> str:
    return f"{script}{expiry.strftime('%d%b%y').upper()}{strike}{opt}"


def reset(book, started: str, capital: float) -> None:
    book.started_on = started
    book.starting_capital = capital
    book.capital = capital
    book.realised_pnl = 0.0
    book.position = None
    book.closed = []


def _exit_if_due(book, frames, days, day: date, kind: str) -> bool:
    if not book.is_open:
        return False
    pos = book.position
    entry = date.fromisoformat(pos["entry_date"])
    script = pos["script"]
    nxt = next_session(days[script], entry)
    if nxt is None or day < nxt:
        return False
    if day > nxt:
        print(f"  {kind} missed exit {nxt} for {script} entered {entry} — drop")
        book.position = None
        return True
    quote = atm_quote(
        frames[script],
        day,
        "15:00",
        expiry=date.fromisoformat(str(pos["expiry"])[:10]),
        strike=int(pos["strike"]),
    )
    if quote is None:
        print(f"  {kind} {script} entered {entry} has no 15:00 on {day} — drop")
        book.position = None
        return True
    if kind == "syn":
        if pos.get("tier") == "filler" and not pos.get("partial_done"):
            _try_partial(book, frames[script], pos, entry, day)
        ce_x, pe_x = synth_exit_fills(pos["side"], quote["ce"], quote["pe"])
        print(" ", book.exit(day=day, call_px=ce_x, put_px=pe_x, clock="15:00", why="time"))
    else:
        raw = quote["ce"] if pos["opt_side"] == "CE" else quote["pe"]
        print(" ", book.exit(day=day, raw_open=raw, clock="15:00", why="time"))
    return True


def backfill() -> None:
    hist = load_pcr_history()
    frames = {s: load_script(s) for s in SCRIPTS}
    days = {s: session_days(frames[s]) for s in SCRIPTS}
    # Union calendar so each book steps through every session either script traded.
    calendar = sorted(set().union(*days.values()))
    print("Sessions", [d.isoformat() for d in calendar])

    syn = MasterBook()
    ob = OptionBuyBook()
    reset(syn, "2026-11-01", STARTING_CAPITAL)
    reset(ob, "2026-11-01", OB_START)

    for day in calendar:
        # Exit only on that script's next session, and do not re-enter the same day.
        # Live entry is 14:05 while the prior trade is still open until 15:00.
        syn_exited = _exit_if_due(syn, frames, days, day, kind="syn")
        ob_exited = _exit_if_due(ob, frames, days, day, kind="ob")
        cands = candidates(hist, day)
        if not syn.is_open and not syn_exited:
            sig = pick_signal(cands)
            if sig:
                _enter_syn(syn, frames, sig, day)
        if not ob.is_open and not ob_exited:
            sig = pick_base_signal(cands)
            if sig:
                _enter_ob(ob, frames, sig, day)

    # Drop a position whose exit session is already in the past but missing.
    for book in (syn, ob):
        if book.is_open:
            entry = date.fromisoformat(book.position["entry_date"])
            nxt = next_session(days[book.position["script"]], entry)
            if nxt is None or nxt <= date(2026, 11, 21):
                print("Dropping unexited", book.name, book.position["script"], entry, "next", nxt)
                book.position = None

    syn.started_on = "2026-11-01"
    ob.started_on = "2026-11-01"
    syn.save()
    ob.save()
    print(
        f"Synthetic: trades={len(syn.closed)} capital=₹{syn.capital:,.0f} "
        f"pnl=₹{syn.realised_pnl:,.0f} open={syn.is_open}"
    )
    print(
        f"Option-buy: trades={len(ob.closed)} capital=₹{ob.capital:,.0f} "
        f"pnl=₹{ob.realised_pnl:,.0f} open={ob.is_open}"
    )


def _try_partial(book: MasterBook, df: pd.DataFrame, pos: dict, entry: date, exit_day: date) -> None:
    strike = int(pos["strike"])
    expiry = date.fromisoformat(str(pos["expiry"])[:10])
    bars = df[(df["expiry"] == expiry) & (df["strike"] == strike)].copy()
    bars = bars[((bars["day"] > entry) | ((bars["day"] == entry) & (bars["clock"] > "14:05")))]
    bars = bars[(bars["day"] < exit_day) | ((bars["day"] == exit_day) & (bars["clock"] <= "15:00"))]
    bars = bars.sort_values(["day", "clock"])
    ce = bars[bars["side"] == "CE"][["day", "clock", "open"]].rename(columns={"open": "ce"})
    pe = bars[bars["side"] == "PE"][["day", "clock", "open"]].rename(columns={"open": "pe"})
    m = ce.merge(pe, on=["day", "clock"])
    tp = tp1_for(pos["script"])
    for row in m.itertuples(index=False):
        pts = synth_points(pos["side"], pos["call_entry"], pos["put_entry"], row.ce, row.pe)
        if pts >= tp:
            print(" ", book.partial_book(day=row.day, call_px=float(row.ce), put_px=float(row.pe)))
            return


def _enter_syn(book: MasterBook, frames, sig: dict, day: date) -> None:
    q = atm_quote(frames[sig["script"]], day, "14:05")
    if not q:
        print("  synthetic skip, no 14:05 ATM", sig["script"], day)
        return
    ce, pe = synth_entry_fills(sig["side"], q["ce"], q["pe"])
    print(
        " ",
        book.enter(
            script=sig["script"],
            tier=sig["tier"],
            side=sig["side"],
            day=day,
            spot=q["spot"],
            pcr=sig["pcr"],
            pcr_rank=sig["rank"],
            call_symbol=symbol(sig["script"], q["expiry"], q["strike"], "CE"),
            call_token="hist",
            call_entry=ce,
            put_symbol=symbol(sig["script"], q["expiry"], q["strike"], "PE"),
            put_token="hist",
            put_entry=pe,
            strike=q["strike"],
            expiry=q["expiry"].isoformat(),
            lot_size=lot_size(sig["script"], day),
        ),
    )


def _enter_ob(book: OptionBuyBook, frames, sig: dict, day: date) -> None:
    q = atm_quote(frames[sig["script"]], day, "14:05")
    if not q:
        print("  option-buy skip, no 14:05 ATM", sig["script"], day)
        return
    opt = opt_side_for(sig["side"])
    raw = q["ce"] if opt == "CE" else q["pe"]
    print(
        " ",
        book.enter(
            script=sig["script"],
            signal_side=sig["side"],
            day=day,
            spot=q["spot"],
            pcr=sig["pcr"],
            pcr_rank=sig["rank"],
            opt_side=opt,
            symbol=symbol(sig["script"], q["expiry"], q["strike"], opt),
            token="hist",
            raw_open=raw,
            strike=q["strike"],
            expiry=q["expiry"].isoformat(),
            lot_size=lot_size(sig["script"], day),
        ),
    )


if __name__ == "__main__":
    backfill()
