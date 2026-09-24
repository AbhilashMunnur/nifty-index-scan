"""Live Nifty / Sensex quotes and option-chain OI from Angel One."""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

import pandas as pd
from SmartApi import SmartConnect

from src.angelone_auth import AUTH_ERROR_CODES, AngelOneAuth
from src.calendar import trading_days_remaining
from src.indicators import calculate_rsi
from src.models import FutureContract, IndexSnapshot
from src.pcr import BandPcr, StrikePcr
from src.scrip import SCRIP_MASTER_URL, download_cached
from src.strikes import atm_band
from src.universe import SPECS, IndexSpec, require_index

MAX_TOKENS_PER_REQUEST = 50
QUOTE_INTERVAL_SECONDS = 1.05
CANDLE_INTERVAL_SECONDS = 1.1
RETRY_BACKOFF_SECONDS = (2, 5, 10)
RATE_LIMIT_BACKOFF_SECONDS = (20, 45, 90)
STRIKE_DIVISOR = 100.0


class Throttle:
    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._last_call = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_call = time.monotonic()


class IndexMarket:
    """Quotes and nearest-expiry OI for NIFTY and SENSEX only."""

    def __init__(self, auth: AngelOneAuth | None = None):
        self.auth = auth or AngelOneAuth()
        self._quote_throttle = Throttle(QUOTE_INTERVAL_SECONDS)
        self._candle_throttle = Throttle(CANDLE_INTERVAL_SECONDS)
        self._index_tokens: dict[str, str] | None = None
        self._option_rows: dict[str, list[dict]] | None = None
        self._futures_rows: dict[str, list[dict]] | None = None

    @property
    def client(self) -> SmartConnect:
        return self.auth.ensure_session()

    def close(self) -> None:
        self.auth.close()

    def __enter__(self) -> IndexMarket:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _load_instruments(self) -> None:
        if self._index_tokens is not None:
            return

        path = download_cached(SCRIP_MASTER_URL, "angelone_scrip_master.json")
        with path.open(encoding="utf-8") as handle:
            rows = json.load(handle)

        index_tokens: dict[str, str] = {}
        options: dict[str, list[dict]] = {}
        futures: dict[str, list[dict]] = {}

        for row in rows:
            name = str(row.get("name", "")).upper()
            spec = SPECS.get(name)
            if spec is None:
                continue

            segment = str(row.get("exch_seg", ""))
            instrument = str(row.get("instrumenttype", ""))

            if segment == spec.index_exchange and instrument == spec.index_type:
                index_tokens[spec.symbol] = str(row["token"])
            elif segment == spec.fno_exchange and instrument == spec.option_type:
                options.setdefault(spec.symbol, []).append(row)
            elif segment == spec.fno_exchange and instrument == spec.future_type:
                futures.setdefault(spec.symbol, []).append(row)

        self._index_tokens = index_tokens
        self._option_rows = options
        self._futures_rows = futures

    def _call(self, throttle: Throttle, method_name: str, *args):
        last_error: Exception | None = None
        rate_hits = 0
        max_attempts = 1 + len(RETRY_BACKOFF_SECONDS) + len(RATE_LIMIT_BACKOFF_SECONDS)

        for attempt in range(max_attempts):
            throttle.wait()
            try:
                response = getattr(self.client, method_name)(*args)
            except Exception as exc:
                last_error = exc
                text = str(exc).lower()
                if "access rate" in text or "too many" in text:
                    delay = RATE_LIMIT_BACKOFF_SECONDS[
                        min(rate_hits, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
                    ]
                    rate_hits += 1
                    print(f"  rate limited on {method_name}; sleeping {delay}s")
                    time.sleep(delay)
                    continue
                if attempt >= 2:
                    raise
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue

            if isinstance(response, dict) and not response.get("status"):
                code = str(response.get("errorcode", ""))
                message = str(response.get("message", ""))

                if code in AUTH_ERROR_CODES:
                    print(f"  session expired ({code}); logging in again")
                    self.auth.login()
                    last_error = RuntimeError(message or code)
                    continue

                if "rate" in message.lower() or code == "AB1021":
                    delay = RATE_LIMIT_BACKOFF_SECONDS[
                        min(rate_hits, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
                    ]
                    rate_hits += 1
                    print(f"  rate limited on {method_name}; sleeping {delay}s")
                    time.sleep(delay)
                    last_error = RuntimeError(message or code)
                    continue

                return response

            return response

        if last_error:
            raise last_error
        return None

    def get_ltps(self, symbols: list[str]) -> dict[str, float]:
        self._load_instruments()
        grouped: dict[str, list[str]] = {}
        tokens: dict[str, str] = {}

        for symbol in symbols:
            spec = require_index(symbol)
            token = (self._index_tokens or {}).get(spec.symbol)
            if not token:
                continue
            grouped.setdefault(spec.index_exchange, []).append(token)
            tokens[token] = spec.symbol

        if not grouped:
            return {}

        response = self._call(self._quote_throttle, "getMarketData", "LTP", grouped)
        prices: dict[str, float] = {}
        if not response or not response.get("status"):
            return prices

        for quote in (response.get("data") or {}).get("fetched") or []:
            symbol = tokens.get(str(quote.get("symbolToken")))
            price = quote.get("ltp")
            if symbol and price:
                prices[symbol] = float(price)
        return prices

    def listed_futures(self, symbol: str, as_of: date | None = None) -> list[FutureContract]:
        self._load_instruments()
        spec = require_index(symbol)
        as_of = as_of or date.today()
        contracts: list[FutureContract] = []
        for row in (self._futures_rows or {}).get(spec.symbol) or []:
            try:
                expiry = datetime.strptime(str(row["expiry"]), "%d%b%Y").date()
            except (KeyError, ValueError):
                continue
            if expiry < as_of:
                continue
            contracts.append(
                FutureContract(
                    symbol=spec.symbol,
                    tradingsymbol=str(row.get("symbol", "")),
                    token=str(row.get("token", "")),
                    expiry=expiry.strftime("%Y-%m-%d"),
                    lot_size=int(float(row.get("lotsize") or 0)),
                    trading_days_left=trading_days_remaining(as_of, expiry),
                    exchange=spec.fno_exchange,
                )
            )
        contracts.sort(key=lambda item: item.expiry)
        return contracts

    def nearest_future(self, symbol: str) -> tuple[str, str, int] | None:
        """Current-month index future: (tradingsymbol, expiry YYYY-MM-DD, lot)."""
        contracts = self.listed_futures(symbol)
        if not contracts:
            return None
        near = contracts[0]
        return near.tradingsymbol, near.expiry, near.lot_size

    def tradeable_future(
        self,
        symbol: str,
        min_trading_days: int = 10,
        as_of: date | None = None,
    ) -> FutureContract | None:
        """Earliest monthly future with at least `min_trading_days` left.

        Example: on 20 Aug the Aug contract has fewer than 10 sessions, so
        this returns the September future.
        """
        for contract in self.listed_futures(symbol, as_of=as_of):
            if contract.trading_days_left >= min_trading_days:
                return contract
        return None

    def get_future_ltp(self, contract: FutureContract) -> float | None:
        if not contract.token:
            return None
        response = self._call(
            self._quote_throttle,
            "getMarketData",
            "LTP",
            {contract.exchange: [contract.token]},
        )
        if not response or not response.get("status"):
            return None
        for quote in (response.get("data") or {}).get("fetched") or []:
            if str(quote.get("symbolToken")) == contract.token and quote.get("ltp"):
                return float(quote["ltp"])
        return None

    def hourly_rsi(self, symbol: str, ltp: float, period: int = 14) -> float | None:
        """Wilder RSI on 1-hour Nifty/Sensex candles, with the live bar set to LTP."""
        spec = require_index(symbol)
        self._load_instruments()
        token = (self._index_tokens or {}).get(spec.symbol)
        if not token:
            return None

        now = datetime.now()
        try:
            response = self._call(
                self._candle_throttle,
                "getCandleData",
                {
                    "exchange": spec.index_exchange,
                    "symboltoken": token,
                    "interval": "ONE_HOUR",
                    "fromdate": (now - timedelta(days=45)).strftime("%Y-%m-%d %H:%M"),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                },
            )
        except Exception as exc:
            print(f"  {spec.symbol}: hourly candles unavailable ({exc})")
            return None

        candles = (response or {}).get("data") or []
        closes: list[float] = []
        last_stamp = ""
        for row in candles:
            if len(row) < 5:
                continue
            last_stamp = str(row[0])
            closes.append(float(row[4]))
        if not closes:
            return None

        # Replace the still-forming hour with live spot when timestamps match today.
        today = f"{date.today():%Y-%m-%d}"
        if last_stamp[:10] == today:
            closes[-1] = ltp
        else:
            closes.append(ltp)
        return calculate_rsi(pd.Series(closes, dtype=float), period=period)

    def _previous_session_oi(self, exchange: str, token: str) -> int | None:
        now = datetime.now()
        try:
            response = self._call(
                self._candle_throttle,
                "getOIData",
                {
                    "exchange": exchange,
                    "symboltoken": token,
                    "interval": "ONE_DAY",
                    "fromdate": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                },
            )
        except Exception as exc:
            print(f"  OI history unavailable for token {token} ({exc})")
            return None

        today = f"{date.today():%Y-%m-%d}"
        prior = [
            int(float(row["oi"]))
            for row in (response or {}).get("data") or []
            if str(row.get("time", ""))[:10] < today
        ]
        return prior[-1] if prior else None

    def get_atm_band_pcr(self, symbol: str, ltp: float) -> BandPcr | None:
        """PCR and ΔPCR on ATM, OTM1, OTM2, ITM1, ITM2 of the nearest option expiry."""
        spec = require_index(symbol)
        nearest = self._nearest_expiry_rows(spec)
        if not nearest:
            print(f"  {spec.symbol}: no index options listed")
            return None

        expiry, contracts = nearest
        by_strike: dict[float, dict[str, dict]] = {}
        for row in contracts:
            strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
            if strike <= 0:
                continue
            name = str(row.get("symbol", ""))
            leg = "CE" if name.endswith("CE") else ("PE" if name.endswith("PE") else "")
            if not leg:
                continue
            by_strike.setdefault(strike, {})[leg] = row

        band = atm_band(list(by_strike), ltp)
        if not band:
            print(f"  {spec.symbol}: ATM ± 2 strikes not listed")
            return None

        tokens: list[str] = []
        for _label, strike in band:
            legs = by_strike.get(strike) or {}
            for leg in ("CE", "PE"):
                row = legs.get(leg)
                if row:
                    tokens.append(str(row["token"]))

        try:
            quotes = self._fetch_option_quotes(spec.fno_exchange, tokens)
        except Exception as exc:
            print(f"  {spec.symbol}: option quotes failed ({exc})")
            return None

        lot_size = int(float(contracts[0].get("lotsize") or 0))
        step = band[1][1] - band[0][1]
        rows: list[StrikePcr] = []
        for label, strike in band:
            legs = by_strike.get(strike) or {}
            call_row, put_row = legs.get("CE"), legs.get("PE")
            call_token = str(call_row["token"]) if call_row else ""
            put_token = str(put_row["token"]) if put_row else ""
            call_q = quotes.get(call_token) or {}
            put_q = quotes.get(put_token) or {}
            call_oi = int(call_q.get("oi") or 0) if call_token else 0
            put_oi = int(put_q.get("oi") or 0) if put_token else 0
            call_ltp = call_q.get("ltp") if call_token else None
            put_ltp = put_q.get("ltp") if put_token else None
            call_prev = (
                self._previous_session_oi(spec.fno_exchange, call_token)
                if call_token
                else None
            )
            put_prev = (
                self._previous_session_oi(spec.fno_exchange, put_token)
                if put_token
                else None
            )
            rows.append(
                StrikePcr(
                    label=label,
                    strike=strike,
                    call_oi=call_oi,
                    put_oi=put_oi,
                    call_token=call_token,
                    put_token=put_token,
                    call_oi_change=None if call_prev is None else call_oi - call_prev,
                    put_oi_change=None if put_prev is None else put_oi - put_prev,
                    call_ltp=None if call_ltp is None else float(call_ltp),
                    put_ltp=None if put_ltp is None else float(put_ltp),
                )
            )

        return BandPcr(
            expiry=expiry,
            atm=band[2][1],
            step=step,
            lot_size=lot_size,
            strikes=rows,
        )

    def _nearest_expiry_rows(self, spec: IndexSpec) -> tuple[str, list[dict]] | None:
        self._load_instruments()
        contracts = (self._option_rows or {}).get(spec.symbol)
        if not contracts:
            return None

        today = date.today()
        by_expiry: dict[date, list[dict]] = {}
        for row in contracts:
            try:
                expiry = datetime.strptime(str(row["expiry"]), "%d%b%Y").date()
            except (KeyError, ValueError):
                continue
            if expiry >= today:
                by_expiry.setdefault(expiry, []).append(row)

        if not by_expiry:
            return None

        nearest = min(by_expiry)
        return nearest.strftime("%Y-%m-%d"), by_expiry[nearest]

    def _fetch_option_quotes(
        self, exchange: str, tokens: list[str]
    ) -> dict[str, dict[str, float | int | None]]:
        """OI + LTP for option tokens (FULL market data)."""
        out: dict[str, dict[str, float | int | None]] = {}
        for start in range(0, len(tokens), MAX_TOKENS_PER_REQUEST):
            batch = tokens[start : start + MAX_TOKENS_PER_REQUEST]
            response = self._call(
                self._quote_throttle, "getMarketData", "FULL", {exchange: batch}
            )
            if not response or not response.get("status"):
                continue
            for quote in (response.get("data") or {}).get("fetched") or []:
                token = str(quote.get("symbolToken") or "")
                if not token:
                    continue
                ltp_raw = quote.get("ltp")
                out[token] = {
                    "oi": int(quote.get("opnInterest") or 0),
                    "ltp": float(ltp_raw) if ltp_raw not in (None, "") else None,
                }
        return out

    def _fetch_open_interest(self, exchange: str, tokens: list[str]) -> dict[str, int]:
        quotes = self._fetch_option_quotes(exchange, tokens)
        return {token: int(row.get("oi") or 0) for token, row in quotes.items()}

    def get_oi_snapshot(self, symbol: str, ltp: float = 0.0) -> IndexSnapshot | None:
        spec = require_index(symbol)
        nearest = self._nearest_expiry_rows(spec)
        if not nearest:
            print(f"  {spec.symbol}: no index options listed")
            return None

        expiry, contracts = nearest
        by_token = {str(row["token"]): row for row in contracts}

        try:
            open_interest = self._fetch_open_interest(spec.fno_exchange, list(by_token))
        except Exception as exc:
            print(f"  {spec.symbol}: option quotes failed ({exc})")
            return None

        if not open_interest:
            print(f"  {spec.symbol}: no open interest returned")
            return None

        max_call_oi = max_put_oi = -1
        max_call_strike = max_put_strike = 0.0
        max_call_token = max_put_token = ""
        legs_by_strike: dict[float, dict[str, tuple[int, str]]] = {}

        for token, oi in open_interest.items():
            row = by_token.get(token)
            if not row:
                continue
            strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
            if strike <= 0:
                continue
            symbol_name = str(row.get("symbol", ""))
            if symbol_name.endswith("CE"):
                legs_by_strike.setdefault(strike, {})["CE"] = (oi, token)
                if oi > max_call_oi:
                    max_call_oi, max_call_strike, max_call_token = oi, strike, token
            elif symbol_name.endswith("PE"):
                legs_by_strike.setdefault(strike, {})["PE"] = (oi, token)
                if oi > max_put_oi:
                    max_put_oi, max_put_strike, max_put_token = oi, strike, token

        if max_call_oi < 0 or max_put_oi < 0:
            return None

        future = self.nearest_future(spec.symbol)
        return IndexSnapshot(
            symbol=spec.symbol,
            ltp=ltp,
            expiry=expiry,
            lot_size=int(float(contracts[0].get("lotsize") or 0)),
            max_call_oi_strike=max_call_strike,
            max_call_oi=max_call_oi,
            max_put_oi_strike=max_put_strike,
            max_put_oi=max_put_oi,
            futures_symbol=future[0] if future else "",
            futures_expiry=future[1] if future else "",
            max_call_token=max_call_token,
            max_put_token=max_put_token,
            legs_by_strike=legs_by_strike,
        )
