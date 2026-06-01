"""Thin Telegram delivery. Credentials from env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)."""
from __future__ import annotations

import os

import httpx

from ..env import redact_secrets


def send_telegram(text: str, token: str | None = None, chat_id: str | None = None,
                  client: httpx.Client | None = None) -> bool:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    c = client or httpx.Client(timeout=15.0)
    try:
        resp = c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text})
        return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"telegram send failed: {redact_secrets(str(e))}")
        return False
    finally:
        if client is None:
            c.close()
