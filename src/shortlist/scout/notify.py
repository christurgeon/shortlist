"""Thin Telegram delivery. Credentials from env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

from ..env import redact_secrets

_API = "https://api.telegram.org/bot{token}/{method}"
_MSG_CAP = 4096
_CAPTION_CAP = 1024


def _chunks(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i:i + size]


class TelegramNotifier:
    """One-shot Telegram transport. configured() is the exit-code discriminator.
    Each send returns bool and redacts its own exceptions (the URL embeds the token)."""

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 client: httpx.Client | None = None, max_retries: int = 2) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self._client = client
        self.max_retries = max_retries

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _post(self, method: str, **kwargs) -> bool:
        if not self.configured():
            return False
        c = self._client or httpx.Client(timeout=30.0)
        url = _API.format(token=self.token, method=method)
        try:
            for attempt in range(self.max_retries + 1):
                resp = c.post(url, **kwargs)
                if resp.status_code == 429 and attempt < self.max_retries:
                    # Retry-After-aware backoff, mirroring FMPProvider._get's 429 idiom.
                    delay = float(resp.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(min(delay, 30.0))
                    continue
                return resp.status_code == 200
            return False
        except Exception as e:  # noqa: BLE001
            print(f"telegram {method} failed: {redact_secrets(str(e))}")
            return False
        finally:
            if self._client is None:
                c.close()

    def send_message(self, text: str) -> bool:
        ok = True
        for chunk in _chunks(text, _MSG_CAP):
            ok = self._post("sendMessage", json={"chat_id": self.chat_id, "text": chunk}) and ok
        return ok

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        return self._post("sendPhoto",
                          data={"chat_id": self.chat_id, "caption": caption[:_CAPTION_CAP]},
                          files={"photo": ("dashboard.png", png, "image/png")})

    def send_document(self, data: bytes, filename: str, caption: str = "") -> bool:
        return self._post("sendDocument",
                          data={"chat_id": self.chat_id, "caption": caption[:_CAPTION_CAP]},
                          files={"document": (filename, data, "text/html")})


@dataclass
class DeliveryResult:
    configured: bool
    all_ok: bool
    failures: list[str] = field(default_factory=list)


def deliver(notifier, *, png: bytes | None, html: str | None, text: str, caption: str,
            session: str) -> DeliveryResult:
    """Policy: photo (if any) + document (if any); fall back to a text message on any
    failure, or when nothing else was attached."""
    if not notifier.configured():
        return DeliveryResult(configured=False, all_ok=False)
    failures: list[str] = []
    sent_any = False
    if png is not None:
        sent_any = True
        if not notifier.send_photo(png, caption):
            failures.append("photo")
    if html is not None:
        sent_any = True
        if not notifier.send_document(html.encode("utf-8"), f"scout-{session}.html", caption):
            failures.append("document")
    if (failures or not sent_any) and not notifier.send_message(text):
        failures.append("message")
    return DeliveryResult(configured=True, all_ok=not failures, failures=failures)
