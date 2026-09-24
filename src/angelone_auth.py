"""Angel One SmartAPI login, reused from the OI with stocks project."""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import logzero
import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

logzero.loglevel(logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_VARS = (
    "ANGEL_API_KEY",
    "ANGEL_CLIENT_CODE",
    "ANGEL_PIN",
    "ANGEL_TOTP_SECRET",
)

# Angel One error codes that mean the session must be re-established.
AUTH_ERROR_CODES = {"AG8001", "AG8002", "AG8003", "AB1010", "AB1011", "AB8050", "AB8051"}


class CredentialsError(RuntimeError):
    """Raised when Angel One credentials are missing or rejected."""


class AngelOneAuth:
    """Logs in with API key + client code + PIN + TOTP and keeps a live session."""

    def __init__(self, env_path: Path | None = None):
        load_dotenv(env_path or ROOT / ".env")
        api_key = os.getenv("ANGEL_API_KEY", "").strip()
        client_code = os.getenv("ANGEL_CLIENT_CODE", "").strip()
        pin = os.getenv("ANGEL_PIN", "").strip()
        totp_secret = os.getenv("ANGEL_TOTP_SECRET", "").strip()

        if not all([api_key, client_code, pin, totp_secret]):
            missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
            raise CredentialsError(
                "Angel One needs ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and "
                f"ANGEL_TOTP_SECRET in .env. Missing: {', '.join(missing)}"
            )

        self.client_code = client_code
        self._api_key = api_key
        self._pin = pin
        self._totp_secret = totp_secret
        self.client: SmartConnect | None = None
        self.login()

    def login(self) -> dict:
        """Open a fresh session. Tokens expire daily, so long runs re-login."""
        self.client = SmartConnect(api_key=self._api_key)
        session = self.client.generateSession(
            self.client_code, self._pin, pyotp.TOTP(self._totp_secret).now()
        )

        if not session or not session.get("status"):
            message = (session or {}).get("message", "unknown error")
            raise CredentialsError(f"Angel One login failed: {message}")

        self._session_date = date.today()
        return session

    def ensure_session(self) -> SmartConnect:
        """Re-login if the calendar day has rolled over."""
        if self.client is None or getattr(self, "_session_date", None) != date.today():
            self.login()
        assert self.client is not None
        return self.client

    @property
    def jwt_token(self) -> str:
        client = self.ensure_session()
        return getattr(client, "access_token", "") or ""

    @property
    def feed_token(self) -> str:
        client = self.ensure_session()
        return client.getfeedToken() or getattr(client, "feed_token", "") or ""

    def close(self) -> None:
        if self.client is None:
            return
        try:
            self.client.terminateSession(self.client_code)
        except Exception:
            pass
        self.client = None

    def __enter__(self) -> AngelOneAuth:
        return self

    def __exit__(self, *args) -> None:
        self.close()
