#!/usr/bin/env python3
"""Confirm Angel One credentials from .env can log in."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.angelone_auth import REQUIRED_VARS, AngelOneAuth, CredentialsError


def mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def main() -> None:
    load_dotenv(ROOT / ".env")
    missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
    if missing:
        print("Missing from .env: " + ", ".join(missing))
        sys.exit(1)

    print("Credentials found in .env")
    print(f"  ANGEL_CLIENT_CODE  {os.getenv('ANGEL_CLIENT_CODE')}")
    print(f"  ANGEL_API_KEY      {mask(os.getenv('ANGEL_API_KEY', ''))}")
    print(f"  ANGEL_PIN          {'*' * len(os.getenv('ANGEL_PIN', ''))}")
    print(f"  ANGEL_TOTP_SECRET  {mask(os.getenv('ANGEL_TOTP_SECRET', ''))}")

    try:
        auth = AngelOneAuth(ROOT / ".env")
    except CredentialsError as exc:
        print(f"\nLogin failed: {exc}")
        print("Common causes: wrong PIN, a stale TOTP secret, or the API key not")
        print("yet activated on smartapi.angelone.in.")
        sys.exit(1)

    profile = {}
    try:
        profile = auth.client.getProfile(auth.client.refresh_token) or {}
    except Exception:
        pass

    name = ""
    if isinstance(profile, dict):
        data = profile.get("data") or {}
        name = data.get("name") or data.get("clientcode") or ""

    print("\nLogged in to Angel One")
    if name:
        print(f"  Profile           {name}")
    print(f"  Feed token        {'yes' if auth.feed_token else 'no'}")

    from src.market import IndexMarket
    from src.universe import ALLOWED_SYMBOLS

    market = IndexMarket(auth)
    prices = market.get_ltps(list(ALLOWED_SYMBOLS))
    print("\nIndex quotes (NIFTY and SENSEX only)")
    for symbol in ALLOWED_SYMBOLS:
        ltp = prices.get(symbol)
        print(f"  {symbol:<8}  {'Rs ' + f'{ltp:,.2f}' if ltp else 'unavailable'}")
    missing = [symbol for symbol in ALLOWED_SYMBOLS if symbol not in prices]
    auth.close()
    if missing:
        print("Missing quotes: " + ", ".join(missing))
        sys.exit(1)


if __name__ == "__main__":
    main()
