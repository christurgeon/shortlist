"""Interactive Telegram bot for the scout (inbound long-poll side).

Design: docs/superpowers/specs/2026-06-06-scout-telegram-bot-design.md.
The poll loop validates the allowlist, advances the offset, and enqueues commands;
a single worker thread runs the existing run_harness -> build_report -> deliver chain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_KNOWN = {"screen", "deep", "help", "start"}
_SPLIT = re.compile(r"[,\s]+")


@dataclass(frozen=True)
class Command:
    name: str                  # "screen" | "deep" | "help" | "start" | "unknown"
    tickers: tuple[str, ...]
    raw: str


def _tickers(rest: str) -> tuple[str, ...]:
    seen: list[str] = []
    for tok in _SPLIT.split(rest.strip()):
        t = tok.strip().upper()
        if t and t not in seen:
            seen.append(t)
    return tuple(seen)


def parse_command(text: str) -> Command:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return Command("unknown", (), text)
    head = parts[0].lower().lstrip("/").split("@", 1)[0]   # strip leading / and @botname
    name = head if head in _KNOWN else "unknown"
    rest = parts[1] if len(parts) > 1 else ""
    return Command(name, _tickers(rest), text)


def allowed_message(update: dict, chat_id: str | None) -> str | None:
    """Return the message text iff this update is a private text message from the
    allowlisted chat_id; otherwise None (caller silently ignores). Defends against
    edited_message / channel posts / non-text / group chats."""
    if not chat_id:
        return None
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    text = msg.get("text")
    chat = msg.get("chat") or {}
    if not isinstance(text, str):
        return None
    if chat.get("type") != "private":
        return None
    if str(chat.get("id")) != str(chat_id):
        return None
    return text
