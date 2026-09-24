#!/usr/bin/env python3
"""Confirm Telegram and Google Sheets credentials copied from OI with stocks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.google_sheets import GoogleSheetsClient, SheetsError
from src.telegram_client import TelegramClient, TelegramError


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


def check_telegram() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [
        chat_id.strip()
        for chat_id in os.getenv("TELEGRAM_CHAT_ID", "").split(",")
        if chat_id.strip()
    ]
    print("Telegram")
    print(f"  Bot token   {mask(token) if token else '(missing)'}")
    print(f"  Chat IDs    {', '.join(chat_ids) if chat_ids else '(missing)'}")

    client = TelegramClient(ROOT / ".env")
    me = client.get_me()
    username = me.get("username") or me.get("first_name") or "?"
    print(f"  Bot         @{username}")
    print(f"  Recipients  {len(client.chat_ids)}")


def check_sheets() -> None:
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    sheet_id = (config.get("google_sheets") or {}).get("sheet_id", "")
    worksheets = (config.get("google_sheets") or {}).get("worksheets") or []

    print("\nGoogle Sheets")
    client = GoogleSheetsClient(ROOT / ".env")
    print(f"  Key file    {client.source}")
    print(f"  Account     {client.email}")
    print(f"  Sheet ID    {sheet_id}")

    spreadsheet = client.spreadsheet(sheet_id)
    titles = [ws.title for ws in spreadsheet.worksheets()]
    print(f"  Opened      {spreadsheet.title}")
    for name in worksheets:
        status = "yes" if name in titles else "missing"
        print(f"  Tab         {name} ({status})")


def main() -> None:
    load_dotenv(ROOT / ".env")
    failed = False

    try:
        check_telegram()
    except TelegramError as exc:
        print(f"  FAILED: {exc}")
        failed = True
    except Exception as exc:
        print(f"  FAILED: {exc}")
        failed = True

    try:
        check_sheets()
    except SheetsError as exc:
        print(f"  FAILED: {exc}")
        failed = True
    except Exception as exc:
        print(f"  FAILED: {exc}")
        failed = True

    if failed:
        sys.exit(1)
    print("\nTelegram and Google Sheets are ready.")


if __name__ == "__main__":
    main()
