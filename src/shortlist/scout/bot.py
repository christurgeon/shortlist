"""Interactive Telegram bot for the scout (inbound long-poll side).

Design: docs/superpowers/specs/2026-06-06-scout-telegram-bot-design.md.
The poll loop validates the allowlist, advances the offset, and enqueues commands;
a single worker thread runs the existing run_harness -> build_report -> deliver chain.
"""
from __future__ import annotations

import argparse
import queue
import re
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from ..env import load_env, redact_secrets
from ..models import rank_key
from ..validation import no_data, partition_format
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


def _no_data_note(missing) -> str:
    names = ", ".join(c.ticker for c in missing)
    return (f"⚠️ No data for: {names} — unknown symbol, or all sources "
            f"(FMP/Finnhub/EDGAR) failed. Check the symbol or retry.")


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

    def _format_filter(self, tickers: tuple[str, ...], usage: str):
        """Drop malformed tokens before any API spend. Returns (good_list, note):
        - good is the well-formed list (order preserved);
        - note is a trailing 'ignored' string when some tokens were malformed.
        If NO well-formed tokens remain, replies (invalid-format if any malformed,
        else the usage text) and returns (None, None) so the caller returns early.
        """
        good, bad = partition_format(tickers)
        if not good:
            self.notifier.send_message(
                f"Invalid ticker format: {', '.join(bad)}. "
                "Use US symbols like NVDA, BRK.B." if bad else usage)
            return None, None
        note = f"Invalid ticker format: {', '.join(bad)} (ignored)." if bad else None
        return good, note

    def _do_screen(self, tickers: tuple[str, ...]) -> None:
        good, fmt_note = self._format_filter(tickers, "Usage: /screen NVDA, LMT, MSFT")
        if good is None:
            return
        kept, dropped = _soft_cap(tuple(good), self.max_screen)
        # "Heard you" feedback. Runs on the WORKER thread (this handler), never the
        # poll thread — a slow/flaky chat-action POST must not stall getUpdates.
        self.notifier.send_chat_action("upload_photo")
        cards = self._screen_fn()(kept, self.sources, self.config)
        present = [c for c in cards if not no_data(c)]
        missing = [c for c in cards if no_data(c)]
        if present:
            manifest = _interactive_manifest(len(kept), len(present), "screen", [])
            art = self._report_fn()(present, manifest, assessments={})
            self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                               caption=_caption(manifest, present),
                               session=manifest.session.isoformat())
        if missing:
            self.notifier.send_message(_no_data_note(missing))
        if dropped:
            self.notifier.send_message(
                f"(screened first {len(kept)}; {dropped} more not run — re-send them)")
        if fmt_note:
            self.notifier.send_message(fmt_note)

    def _do_deep(self, tickers: tuple[str, ...]) -> None:
        good, fmt_note = self._format_filter(tickers, "Usage: /deep TSLA")
        if good is None:
            return
        kept, dropped = _soft_cap(tuple(good), self.max_deep)
        self.notifier.send_message(
            f"Researching {', '.join(kept)} — this can take several minutes…")
        cards = self._screen_fn()(kept, self.sources, self.config)
        present = [c for c in cards if not no_data(c)]
        missing = [c for c in cards if no_data(c)]
        if present:
            _briefs, assessments, researched, note, skipped = self._research_fn()(
                present, self.config, self.scout_cfg,
                require_passed=False, top_n=len(present))
            manifest = _interactive_manifest(len(kept), len(present), "deep", researched)
            if note:
                manifest.notes.append(note)
            art = self._report_fn()(present, manifest, assessments=assessments)
            self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                               caption=_caption(manifest, present),
                               session=manifest.session.isoformat())
            if skipped:
                lines = "\n".join(f"• {t}: {why}" for t, why in skipped.items())
                self.notifier.send_message("⚠️ research unavailable —\n" + lines)
        if missing:
            self.notifier.send_message(_no_data_note(missing))
        if dropped:
            self.notifier.send_message(
                f"(researched first {len(kept)}; {dropped} more not run — re-send them)")
        if fmt_note:
            self.notifier.send_message(fmt_note)

    # --- loop machinery ---
    def _handle_safely(self, cmd: Command) -> None:
        try:
            self._handle(cmd)
        except Exception as e:  # noqa: BLE001 — one bad command must not kill the worker
            self.notifier.send_message(f"⚠️ command failed: {redact_secrets(str(e))}")

    def _worker(self) -> None:
        # Drains to the None sentinel ONLY — never short-circuits on _stop. That keeps
        # already-queued commands from being silently dropped on shutdown AND makes the
        # backlog test deterministic (run() enqueues None after the live command, FIFO).
        while True:
            try:
                cmd = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if cmd is None:
                self._queue.task_done()
                break
            try:
                self._handle_safely(cmd)
            finally:
                self._queue.task_done()

    def _dispatch(self, update: dict) -> None:
        # Poll-thread only: validate + enqueue. NO network here (chat-action lives in the
        # worker handler) so a slow Telegram never stalls getUpdates.
        text = allowed_message(update, self.notifier.chat_id)
        if text is None:
            return
        self._queue.put(parse_command(text))

    def run(self) -> int:
        if not self.notifier.configured():
            print("telegram bot: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set",
                  file=sys.stderr)
            return 1
        if self._client is None:
            # Read timeout MUST exceed the long-poll hold or every poll races to a
            # spurious ReadTimeout. Shutdown latency ≈ one poll cycle (≈ read timeout),
            # so the unit's TimeoutStopSec must exceed poll_timeout + this slack + join.
            self._client = httpx.Client(
                timeout=httpx.Timeout(10.0, read=self.poll_timeout + 10))
        # drop_pending_updates=True clears the server-side backlog in one call; the probe
        # below then just initializes the offset cursor (belt-and-suspenders).
        self.notifier.delete_webhook(drop_pending_updates=True)
        probe = self.notifier.get_updates(offset=-1, timeout=0, client=self._client)
        self._offset = max((u.get("update_id", -1) for u in probe.updates), default=-1) + 1

        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()
        backoff = 1.0
        try:
            while not self._stop.is_set():
                res = self.notifier.get_updates(
                    offset=self._offset, timeout=self.poll_timeout, client=self._client)
                if res.status == 200:
                    backoff = 1.0
                    for u in res.updates:
                        uid = u.get("update_id")
                        if uid is None:           # malformed element: skip, never die
                            continue
                        self._offset = uid + 1    # ACK before dispatch (poison-safe)
                        try:
                            self._dispatch(u)
                        except Exception as e:    # noqa: BLE001 — loop must never die
                            print(f"bot dispatch error: {redact_secrets(str(e))}",
                                  file=sys.stderr)
                elif res.status == 409:
                    if not self._conflict_alerted:   # alert ONCE per process (no flap-spam)
                        self.notifier.send_message(
                            "⚠️ another poller is active on this bot token — "
                            "commands may be dropped.")
                        self._conflict_alerted = True
                    self._stop.wait(min(backoff, 30.0))
                    backoff = min(backoff * 2, 30.0)
                else:  # transport error (status 0) or other non-200
                    self._stop.wait(min(backoff, 30.0))
                    backoff = min(backoff * 2, 30.0)
        finally:
            self._stop.set()
            self._queue.put(None)
            worker.join(timeout=5.0)   # mid-handler work is abandoned (idempotent; re-type)
            self._client.close()
        return 0


_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config.yaml"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="shortlist-bot",
        description="Interactive Telegram bot — /screen and /deep on demand.")
    ap.add_argument("--config", default=str(_DEFAULT_CONFIG))
    args = ap.parse_args(argv)

    load_env()
    config = yaml.safe_load(Path(args.config).read_text())

    # Honour the cache block exactly like daily.py so the bot benefits from warm
    # re-screens and respects the operator's TTL/kill-switch.
    from ..cache import configure_default_cache
    cache_cfg = config.get("cache", {})
    configure_default_cache(enabled=cache_cfg.get("enabled", True),
                            path=cache_cfg.get("path"), ttls=cache_cfg.get("ttl"))

    from .notify import TelegramNotifier
    bot = TelegramBot(TelegramNotifier(), config)

    # SIGTERM (systemd stop/restart) -> set the stop Event so the loop + worker
    # wind down; an in-flight handler is abandoned (idempotent — re-type it).
    signal.signal(signal.SIGTERM, lambda *_a: bot._stop.set())
    try:
        return bot.run()
    except KeyboardInterrupt:        # SIGINT (Ctrl-C in foreground)
        bot._stop.set()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
