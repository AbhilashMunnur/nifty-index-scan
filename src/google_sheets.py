"""Google Sheets helper, reused from the OI with stocks project."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


class SheetsError(RuntimeError):
    """Raised when the Google service account is missing or rejected."""


class GoogleSheetsClient:
    def __init__(self, env_path: Path | None = None):
        load_dotenv(env_path or ROOT / ".env")
        raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not raw:
            raise SheetsError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not set. "
                "Put a path to the service-account JSON in .env."
            )

        path = Path(raw).expanduser()
        if path.suffix == ".json" and path.exists():
            self.info = json.loads(path.read_text())
            self.source = str(path)
        else:
            self.info = json.loads(raw)
            self.source = "inline JSON"

        self.email = self.info.get("client_email", "")

    def credentials(self):
        from google.oauth2.service_account import Credentials

        return Credentials.from_service_account_info(
            self.info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

    def spreadsheet(self, sheet_id: str):
        import gspread

        return gspread.authorize(self.credentials()).open_by_key(sheet_id)
