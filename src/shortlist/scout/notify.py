"""Thin Telegram delivery. Credentials from env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field

import httpx

from .._util import retry_after_seconds
from ..env import redact_secrets

_API = "https://api.telegram.org/bot{token}/{method}"
_MSG_CAP = 4096
_CAPTION_CAP = 1024


@dataclass
class PollResult:
    """getUpdates outcome. status: HTTP code, or 0 on transport error.
    updates: the parsed `result` array (empty unless status == 200)."""
    status: int
    updates: list[dict] = field(default_factory=list)


def _chunks(text: str, size: int):
    """Split `text` into chunks of at most `size` UTF-16 code units.

    Telegram's 4096 message cap counts UTF-16 code units (len(s.encode("utf-16-le")) // 2),
    NOT Python code points — an emoji-dense report chunked by code points overflows the cap
    and 400s. Astral-plane chars (ord > 0xFFFF) weigh 2 units; iterating per code point
    means a surrogate pair is never split across chunks."""
    buf: list[str] = []
    units = 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if units + w > size and buf:
            yield "".join(buf)
            buf, units = [], 0
        buf.append(ch)
        units += w
    if buf:
        yield "".join(buf)


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

    def _post(self, method: str, *, _client: httpx.Client | None = None, **kwargs) -> bool:
        if not self.configured():
            return False
        c = _client or self._client or httpx.Client(timeout=30.0)
        owns = _client is None and self._client is None
        url = _API.format(token=self.token, method=method)
        try:
            for attempt in range(self.max_retries + 1):
                resp = c.post(url, **kwargs)
                retriable = resp.status_code == 429 or 500 <= resp.status_code < 600
                if retriable and attempt < self.max_retries:
                    # Retry-After-aware backoff, mirroring FMPSource._get's 429 idiom;
                    # transient 5xx use the same capped exponential backoff. The shared
                    # parser tolerates the RFC-7231 HTTP-date header form.
                    time.sleep(retry_after_seconds(resp.headers.get("Retry-After"), 2 ** attempt))
                    continue
                if resp.status_code != 200:
                    # LOUD degradation: a silently-dropped chunk/photo is invisible. The
                    # body carries Telegram's error description; the URL (which embeds the
                    # token) is never printed, and the body is redacted anyway.
                    print(f"telegram {method} failed: HTTP {resp.status_code} "
                          f"{redact_secrets(resp.text[:200])}", file=sys.stderr)
                return resp.status_code == 200
            return False
        except Exception as e:  # noqa: BLE001
            print(f"telegram {method} failed: {redact_secrets(str(e))}", file=sys.stderr)
            return False
        finally:
            if owns:
                c.close()

    def send_message(self, text: str) -> bool:
        chunks = list(_chunks(text, _MSG_CAP))
        if not chunks:
            return True
        if not self.configured():
            return False
        # One client reused across all chunks of the message (not one per chunk).
        c = self._client or httpx.Client(timeout=30.0)
        try:
            ok = True
            for chunk in chunks:
                ok = self._post("sendMessage", _client=c,
                                json={"chat_id": self.chat_id, "text": chunk}) and ok
            return ok
        finally:
            if self._client is None:
                c.close()

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        return self._post("sendPhoto",
                          data={"chat_id": self.chat_id, "caption": caption[:_CAPTION_CAP]},
                          files={"photo": ("dashboard.png", png, "image/png")})

    def send_document(self, data: bytes, filename: str, caption: str = "") -> bool:
        return self._post("sendDocument",
                          data={"chat_id": self.chat_id, "caption": caption[:_CAPTION_CAP]},
                          files={"document": (filename, data, "text/html")})

    def get_updates(self, offset: int, timeout: int, client: httpx.Client) -> PollResult:
        """Long-poll getUpdates on a CALLER-OWNED client whose read timeout must
        exceed `timeout` (the loop owns one long-lived client). Returns a PollResult;
        never raises. status==0 signals a transport error (caller backs off);
        status==409 signals another active poller. Errors are redacted (the URL
        embeds the bot token)."""
        if not self.token:
            return PollResult(0, [])
        url = _API.format(token=self.token, method="getUpdates")
        try:
            resp = client.post(url, json={"offset": offset, "timeout": timeout})
            if resp.status_code == 200:
                return PollResult(200, resp.json().get("result", []))
            return PollResult(resp.status_code, [])
        except Exception as e:  # noqa: BLE001
            print(f"telegram getUpdates failed: {redact_secrets(str(e))}")
            return PollResult(0, [])

    def delete_webhook(self, drop_pending_updates: bool = True) -> bool:
        """Clear any webhook registration (a stale one causes 409 on getUpdates).
        `drop_pending_updates=True` ALSO clears the server-side backlog in one
        documented call — the simplest correct restart-replay guard (Telegram Bot
        API: deleteWebhook). The boot offset probe is then belt-and-suspenders."""
        return self._post("deleteWebhook",
                          json={"drop_pending_updates": drop_pending_updates})

    def send_chat_action(self, action: str = "typing") -> bool:
        return self._post("sendChatAction",
                          json={"chat_id": self.chat_id, "action": action})


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
