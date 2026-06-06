"""Interactive Telegram bot for the scout (inbound long-poll side).

Design: docs/superpowers/specs/2026-06-06-scout-telegram-bot-design.md.
The poll loop validates the allowlist, advances the offset, and enqueues commands;
a single worker thread runs the existing run_harness -> build_report -> deliver chain.
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..env import load_env, redact_secrets
from ..models import rank_key
from .models import RunManifest

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


_HELP = (
    "Shortlist scout bot. Commands:\n"
    "/screen NVDA, LMT, MSFT — score tickers (seconds), reply with the dashboard\n"
    "/deep TSLA — score + Claude 10-K research brief (slower)\n"
    "/help — this message"
)


def _soft_cap(tickers: tuple[str, ...], cap: int) -> tuple[list[str], int]:
    kept = list(tickers[:cap])
    return kept, max(0, len(tickers) - cap)


def _interactive_manifest(n_requested: int, n_cards: int, command: str,
                          researched: list[str]) -> RunManifest:
    # signals=[] is the interactive marker that suppresses the funnel/coverage
    # footer (Task 4). Funnel counts are a clean passthrough; only the caption
    # ("{screened} screened from {raw} raw") reads them.
    return RunManifest(
        session=datetime.now(timezone.utc).date(),
        signals=[], raw=n_requested, after_dedup=n_requested,
        after_prefilter=n_requested, screened=n_cards, dropped_for_budget=0,
        researched=list(researched), notes=[f"interactive /{command} request"])


# NOTE: intentional copy of daily._caption — avoids importing the heavy `daily`
# module (and its eager imports) onto the always-on bot path. Keep in sync manually
# if the caption format changes, or extract to a shared helper.
def _caption(manifest, cards, top_n: int = 3) -> str:
    ordered = sorted(cards, key=rank_key, reverse=True)
    top = " · ".join(f"{c.ticker} {c.composite:.0f}" for c in ordered[:top_n])
    return (f"Scout — {manifest.session.isoformat()}\nTop: {top}\n"
            f"{manifest.screened} screened from {manifest.raw} raw")[:1024]


class TelegramBot:
    def __init__(self, notifier, config, *, screen_fn=None, report_fn=None,
                 research_fn=None, deliver_fn=None, client=None):
        self.notifier = notifier
        self.config = config
        self.scout_cfg = config.get("scout", {})
        self.bot_cfg = self.scout_cfg.get("bot", {})
        self.poll_timeout = int(self.bot_cfg.get("poll_timeout_s", 25))
        self.max_screen = int(self.bot_cfg.get("max_screen", 10))
        self.max_deep = int(self.bot_cfg.get("max_deep", 3))
        self.sources = self.scout_cfg.get(
            "deep_screen_sources", ["yahoo", "fmp", "finnhub", "edgar", "finra"])
        self._screen = screen_fn
        self._report = report_fn
        self._research = research_fn
        self._deliver = deliver_fn
        self._client = client
        self._queue: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._offset = 0
        self._conflict_alerted = False

    # --- lazy real-implementation resolvers (overridden by injected fakes) ---
    def _screen_fn(self):
        if self._screen:
            return self._screen
        from ..screen import run_harness
        return run_harness

    def _report_fn(self):
        if self._report:
            return self._report
        from .report import build_report
        return build_report

    def _deliver_fn(self):
        if self._deliver:
            return self._deliver
        from .notify import deliver
        return deliver

    def _research_fn(self):
        if self._research:
            return self._research
        from .daily import _research_phase
        return _research_phase

    # --- handlers ---
    def _handle(self, cmd: Command) -> None:
        if cmd.name == "screen":
            self._do_screen(cmd.tickers)
        elif cmd.name == "deep":
            self._do_deep(cmd.tickers)
        elif cmd.name in ("help", "start"):
            self.notifier.send_message(_HELP)
        else:
            self.notifier.send_message("Unknown command. " + _HELP)

    def _do_screen(self, tickers: tuple[str, ...]) -> None:
        kept, dropped = _soft_cap(tickers, self.max_screen)
        if not kept:
            self.notifier.send_message("Usage: /screen NVDA, LMT, MSFT")
            return
        # "Heard you" feedback. Runs on the WORKER thread (this handler), never the
        # poll thread — a slow/flaky chat-action POST must not stall getUpdates.
        self.notifier.send_chat_action("upload_photo")
        cards = self._screen_fn()(kept, self.sources, self.config)
        manifest = _interactive_manifest(len(kept), len(cards), "screen", [])
        art = self._report_fn()(cards, manifest, assessments={})
        self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                           caption=_caption(manifest, cards), session=manifest.session.isoformat())
        if dropped:
            self.notifier.send_message(
                f"(screened first {len(kept)}; {dropped} more not run — re-send them)")

    def _do_deep(self, tickers: tuple[str, ...]) -> None:
        kept, dropped = _soft_cap(tickers, self.max_deep)
        if not kept:
            self.notifier.send_message("Usage: /deep TSLA")
            return
        self.notifier.send_message(
            f"Researching {', '.join(kept)} — this can take a minute…")
        cards = self._screen_fn()(kept, self.sources, self.config)
        _briefs, assessments, researched, note = self._research_fn()(
            cards, self.config, self.scout_cfg, require_passed=False, top_n=len(kept))
        manifest = _interactive_manifest(len(kept), len(cards), "deep", researched)
        if note:
            manifest.notes.append(note)
        art = self._report_fn()(cards, manifest, assessments=assessments)
        self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                           caption=_caption(manifest, cards), session=manifest.session.isoformat())
        if dropped:
            self.notifier.send_message(
                f"(researched first {len(kept)}; {dropped} more not run — re-send them)")
