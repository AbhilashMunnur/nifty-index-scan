"""Telegram bot helper, reused from the OI with stocks project."""

from __future__ import annotations

import html
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    """Raised when Telegram credentials are missing or the bot call fails."""


class TelegramClient:
    def __init__(self, env_path: Path | None = None):
        load_dotenv(env_path or ROOT / ".env")
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_ids = [
            chat_id.strip()
            for chat_id in os.getenv("TELEGRAM_CHAT_ID", "").split(",")
            if chat_id.strip()
        ]
        if not self.bot_token or not self.chat_ids:
            raise TelegramError(
                "Telegram needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
            )

    def _url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self.bot_token, method=method)

    def get_me(self) -> dict:
        response = requests.get(self._url("getMe"), timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramError(payload.get("description", "getMe failed"))
        return payload.get("result") or {}

    def send_message(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        monospace: bool = False,
        scan_html: bool = False,
        retries: int = 5,
    ) -> int:
        """Send to every recipient. Retry on network/API errors so a 15-min slot is not dropped."""
        if scan_html:
            body = scan_message_html(text)
            chunks = _chunks(body, 4000)
            mode = "HTML"
        else:
            chunks = _chunks(text, 3900 if monospace else 4000)
            mode = parse_mode
        delivered = 0
        for chat_id in self.chat_ids:
            ok = True
            for chunk in chunks:
                body = chunk
                chunk_mode = mode
                if monospace and not scan_html:
                    body = f"<pre>{html.escape(chunk)}</pre>"
                    chunk_mode = "HTML"
                payload: dict = {
                    "chat_id": chat_id,
                    "text": body,
                    "disable_web_page_preview": True,
                }
                if chunk_mode:
                    payload["parse_mode"] = chunk_mode
                if not self._post_send(payload, retries=retries):
                    ok = False
                    break
            if ok:
                delivered += 1
        return delivered

    def send_photo(
        self,
        image_path: str | Path,
        *,
        caption: str | None = None,
        retries: int = 5,
    ) -> int:
        """Send a JPG/PNG portfolio card to every recipient."""
        path = Path(image_path)
        if not path.exists():
            raise TelegramError(f"Photo missing: {path}")
        html_caption = ""
        if caption:
            html_caption = scan_message_html(caption[:900])[:1024]
        delivered = 0
        for chat_id in self.chat_ids:
            ok = False
            last: str | None = None
            for attempt in range(1, retries + 1):
                try:
                    with path.open("rb") as handle:
                        files = {"photo": (path.name, handle, "image/jpeg")}
                        data = {"chat_id": chat_id, "disable_notification": False}
                        if html_caption:
                            data["caption"] = html_caption
                            data["parse_mode"] = "HTML"
                        response = requests.post(
                            self._url("sendPhoto"),
                            data=data,
                            files=files,
                            timeout=40,
                        )
                    response.raise_for_status()
                    body = response.json()
                    if body.get("ok"):
                        ok = True
                        break
                    last = str(body.get("description") or "sendPhoto not ok")
                except (OSError, requests.RequestException) as exc:
                    last = str(exc)
                print(
                    f"  Telegram photo retry {attempt}/{retries} "
                    f"for chat {chat_id}: {last}"
                )
                if attempt < retries:
                    time.sleep(min(2**attempt, 16))
            if ok:
                delivered += 1
            else:
                print(f"  could not send photo to chat {chat_id}: {last}")
        return delivered

    def _post_send(self, payload: dict, *, retries: int) -> bool:
        last: str | None = None
        for attempt in range(1, retries + 1):
            try:
                response = requests.post(
                    self._url("sendMessage"), json=payload, timeout=20
                )
                response.raise_for_status()
                body = response.json()
                if body.get("ok"):
                    return True
                last = str(body.get("description") or "sendMessage not ok")
            except requests.RequestException as exc:
                last = str(exc)
            print(f"  Telegram retry {attempt}/{retries} for chat {payload.get('chat_id')}: {last}")
            if attempt < retries:
                time.sleep(min(2 ** attempt, 16))
        print(f"  could not reach chat {payload.get('chat_id')}: {last}")
        return False


def scan_message_html(text: str) -> str:
    """Bold the interval BUY/WRITE totals; keep the strike table in monospace."""
    lines = text.split("\n")
    out: list[str] = []
    table: list[str] = []

    def flush_table() -> None:
        if table:
            out.append("<pre>" + html.escape("\n".join(table)) + "</pre>")
            table.clear()

    for line in lines:
        is_header = line.startswith("Lbl") and "Strike" in line and "Flow" in line
        is_rule = set(line) <= {"-"} and len(line) >= 8
        is_row = line.startswith(("ITM", "ATM", "OTM"))
        if is_header or (table and (is_rule or is_row)):
            table.append(line)
            continue
        flush_table()
        escaped = html.escape(line)
        if line.startswith("New this interval") or " since 14:30" in line or " since 14:45" in line:
            out.append(f"<b>{escaped}</b>")
        else:
            out.append(escaped)
    flush_table()
    return "\n".join(out)


def _chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:limit])
        rest = rest[limit:]
    return parts
