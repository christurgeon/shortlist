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

from ..config import ConfigError, load_config
from ..env import load_env, redact_secrets
from ..validation import no_data, partition_format, valid_format
from ._caption import _caption  # noqa: F401  (light leaf; re-exported, tests import bot._caption)
from .models import RunManifest

_KNOWN = {"screen", "deep", "portfolio", "help", "start", "explain",
          "add", "thesis", "hold", "remove", "sold"}
_SPLIT = re.compile(r"[,\s]+")


@dataclass(frozen=True)
class Command:
    name: str                  # "screen" | "deep" | "portfolio" | "help" | "start" | "explain" | "unknown"
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


def explain_term(raw: str) -> str:
    """The /explain argument, verbatim (terms are case- and space-bearing;
    Command.tickers uppercases + dedups, so re-derive from raw)."""
    parts = raw.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _strip_cmd(raw: str) -> str:
    """Everything after the leading /command token (mirrors explain_term's split)."""
    parts = raw.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def parse_add(raw: str) -> tuple[list[str], float | None, str | None]:
    """(tickers, shares, error). Comma anywhere => bulk bare tickers. Else ticker + optional
    NUMERIC shares. A non-numeric second token is rejected (it is almost certainly a thesis
    typed in the wrong command)."""
    args = _strip_cmd(raw)
    if not args:
        return [], None, "Usage: /add NVDA [shares]  or  /add NVDA, MSFT, LMT"
    if "," in args:
        seen: list[str] = []
        for tok in args.split(","):
            t = tok.strip().upper()
            if not t:
                continue
            if not valid_format(t):
                return [], None, f"Invalid ticker: {t}. Use US symbols like NVDA, BRK.B."
            if t not in seen:
                seen.append(t)
        if not seen:
            return [], None, "Usage: /add NVDA, MSFT, LMT"
        return seen, None, None
    toks = args.split()
    ticker = toks[0].upper()
    if not valid_format(ticker):
        return [], None, f"Invalid ticker: {ticker}. Use US symbols like NVDA, BRK.B."
    if len(toks) == 1:
        return [ticker], None, None
    try:
        shares = float(toks[1])
    except ValueError:
        return [], None, ("Usage: /add NVDA [shares]. Set a thesis separately with "
                          "/thesis NVDA <why you own it>.")
    return [ticker], shares, None


def parse_thesis(raw: str) -> tuple[str | None, str | None, str | None]:
    """(ticker, thesis_text, error). Ticker upper-cased; thesis prose keeps its case."""
    args = _strip_cmd(raw)
    parts = args.split(maxsplit=1)
    if not parts:
        return None, None, "Usage: /thesis NVDA <why you own it>"
    ticker = parts[0].upper()
    if not valid_format(ticker):
        return None, None, f"Invalid ticker: {ticker}."
    if len(parts) == 1:
        return ticker, None, "Usage: /thesis NVDA <why you own it>"
    return ticker, parts[1].strip(), None


def parse_ticker_note(raw: str) -> tuple[str | None, str | None, str | None]:
    """(ticker, note, error) for /hold and /remove. Note prose keeps its case."""
    args = _strip_cmd(raw)
    parts = args.split(maxsplit=1)
    if not parts:
        return None, None, "Usage: TICKER [reason]"
    ticker = parts[0].upper()
    if not valid_format(ticker):
        return None, None, f"Invalid ticker: {ticker}."
    note = parts[1].strip() if len(parts) > 1 else None
    return ticker, note, None


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
    "/add NVDA 12 — track a holding (shares optional; paste several: /add NVDA, MSFT)\n"
    "/thesis NVDA <why you own it> — record your thesis for a holding\n"
    "/portfolio — view your holdings: exposure, sectors, per-name scores\n"
    "/hold NVDA <note> — after an alert, log that you looked and held\n"
    "/remove NVDA <reason> — stop tracking (recoverable)\n"
    "/screen NVDA, LMT — score tickers (seconds), reply with the dashboard\n"
    "/deep TSLA — score + Claude 10-K research brief (slower)\n"
    "/explain 13d — what a term in these reports means\n"
    "/help — this message\n"
    "(type your note/reason right after the command)"
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


def _call_summary(assessments: dict) -> str | None:
    """Telegram message summarizing each researched name's screening call, or None
    if none have one. Reuses the report view-model's one-liner."""
    from .report.viewmodel import call_one_liner
    lines = []
    for ticker, rec in assessments.items():
        one = call_one_liner(rec) if isinstance(rec, dict) else None
        if one:
            lines.append(f"• {ticker} — {one}")
    if not lines:
        return None
    return "📊 Screening calls (screen only — not advice)\n" + "\n".join(lines)


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
        pf_cfg = config.get("portfolio", {})
        self.store_path = pf_cfg.get("store", "positions.json")
        self.decisions_path = pf_cfg.get("decisions", "decisions.jsonl")
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

    # --- shared handler scaffolding (screen/deep/portfolio each: fetch macro, screen,
    # split present/no-data) ---
    @staticmethod
    def _fetch_macro(config):
        from ..data.macro import fetch_macro
        return fetch_macro(config)

    @staticmethod
    def _partition_present(cards):
        """Split screened cards into (present, missing) by the no_data() predicate."""
        return [c for c in cards if not no_data(c)], [c for c in cards if no_data(c)]

    def _send_dropped_note(self, verb: str, kept: list[str], dropped: int) -> None:
        if dropped:
            self.notifier.send_message(
                f"({verb} first {len(kept)}; {dropped} more not run — re-send them)")

    # --- handlers ---
    def _handle(self, cmd: Command) -> None:
        if cmd.name == "screen":
            self._do_screen(cmd.tickers)
        elif cmd.name == "deep":
            self._do_deep(cmd.tickers)
        elif cmd.name == "portfolio":
            self._do_portfolio()
        elif cmd.name == "explain":
            self._do_explain(explain_term(cmd.raw))
        elif cmd.name == "add":
            self._do_add(cmd.raw)
        elif cmd.name == "thesis":
            self._do_thesis(cmd.raw)
        elif cmd.name == "hold":
            self._do_decision(cmd.raw, "hold")
        elif cmd.name in ("remove", "sold"):
            self._do_remove(cmd.raw)
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
        macro = self._fetch_macro(self.config)
        cards = self._screen_fn()(kept, self.sources, self.config, macro=macro)
        present, missing = self._partition_present(cards)
        if present:
            manifest = _interactive_manifest(len(kept), len(present), "screen", [])
            art = self._report_fn()(present, manifest, assessments={}, macro=macro)
            self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                               caption=_caption(manifest, present),
                               session=manifest.session.isoformat())
        if missing:
            self.notifier.send_message(_no_data_note(missing))
        self._send_dropped_note("screened", kept, dropped)
        if fmt_note:
            self.notifier.send_message(fmt_note)

    def _do_deep(self, tickers: tuple[str, ...]) -> None:
        good, fmt_note = self._format_filter(tickers, "Usage: /deep TSLA")
        if good is None:
            return
        kept, dropped = _soft_cap(tuple(good), self.max_deep)
        self.notifier.send_message(
            f"Researching {', '.join(kept)} — this can take several minutes…")
        macro = self._fetch_macro(self.config)
        cards = self._screen_fn()(kept, self.sources, self.config, macro=macro)
        present, missing = self._partition_present(cards)
        if present:
            _briefs, assessments, researched, note, skipped = self._research_fn()(
                present, self.config, self.scout_cfg,
                require_passed=False, top_n=len(present), macro=macro)
            manifest = _interactive_manifest(len(kept), len(present), "deep", researched)
            if note:
                manifest.notes.append(note)
            art = self._report_fn()(present, manifest, assessments=assessments, macro=macro)
            summary = _call_summary(assessments)
            if summary:
                self.notifier.send_message(summary)
            self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                               caption=_caption(manifest, present),
                               session=manifest.session.isoformat())
            if skipped:
                lines = "\n".join(f"• {t}: {why}" for t, why in skipped.items())
                self.notifier.send_message("⚠️ research unavailable —\n" + lines)
        if missing:
            self.notifier.send_message(_no_data_note(missing))
        self._send_dropped_note("researched", kept, dropped)
        if fmt_note:
            self.notifier.send_message(fmt_note)

    def _free_sources(self):
        from .daily import digest_sources
        base = self.scout_cfg.get("deep_screen_sources",
                                  ["yahoo", "fmp", "finnhub", "edgar"])
        return digest_sources(base, include_fmp=False)

    def _do_add(self, raw: str) -> None:
        from .. import positions as pos
        tickers, shares, err = parse_add(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        macro = self._fetch_macro(self.config)
        entry_by_ticker = {}
        # Screen (free chain) to capture entry_card + reply with the card.
        cards = self._screen_fn()(tickers, self._free_sources(), self.config, macro=macro)
        present, _missing = self._partition_present(cards)
        session = datetime.now(timezone.utc).date().isoformat()
        for c in present:
            entry_by_ticker[c.ticker] = {
                "composite": getattr(c, "composite", None),
                "sources": list(self._free_sources()),
                "as_of": session}
        for t in tickers:
            pos.add_or_update(store, t, shares=shares,
                              entry_card=entry_by_ticker.get(t))
        pos.save_store(self.store_path, store)
        n = len(store["positions"])
        nudge = ""
        if len(tickers) == 1 and not store["positions"][tickers[0]].get("thesis"):
            nudge = f"  ⚠ no thesis — /thesis {tickers[0]} <why you own it>"
        self.notifier.send_message(
            f"Tracking {', '.join(tickers)} — {n} holding(s). /portfolio to view.{nudge}")

    def _do_thesis(self, raw: str) -> None:
        from .. import positions as pos
        ticker, text, err = parse_thesis(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        if not pos.set_thesis(store, ticker, text):
            self.notifier.send_message(f"{ticker} not tracked — /add {ticker} first.")
            return
        pos.save_store(self.store_path, store)
        self.notifier.send_message(f"Thesis saved for {ticker}.")

    def _do_decision(self, raw: str, action: str) -> None:
        from .. import positions as pos
        ticker, note, err = parse_ticker_note(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        if ticker not in store.get("positions", {}):
            self.notifier.send_message(f"{ticker} not tracked — /add {ticker} first.")
            return
        pos.append_decision(self.decisions_path,
                            {"ts": datetime.now(timezone.utc).date().isoformat(),
                             "ticker": ticker, "action": action, "note": note})
        self.notifier.send_message(f"Logged: held {ticker}." if action == "hold"
                                   else f"Logged {ticker}.")

    def _do_remove(self, raw: str) -> None:
        from .. import positions as pos
        ticker, note, err = parse_ticker_note(raw)
        if err:
            self.notifier.send_message(err)
            return
        store = pos.load_store(self.store_path)
        rec = pos.remove(store, ticker)
        if rec is None:
            self.notifier.send_message(f"{ticker} not tracked.")
            return
        pos.append_decision(self.decisions_path,
                            {"ts": datetime.now(timezone.utc).date().isoformat(),
                             "ticker": ticker, "action": "remove", "note": note,
                             "position": rec})       # full record embedded => recoverable
        pos.save_store(self.store_path, store)
        self.notifier.send_message(f"Removed {ticker} (recoverable from the log).")

    def _do_portfolio(self) -> None:
        # lazy import: keep the always-on bot import path light
        from .. import portfolio as pf
        from .. import positions as pos
        store = pos.load_store(self.store_path)
        holdings = pos.holdings_view(store)
        if not holdings:
            self.notifier.send_message(
                "No holdings yet. Add one with /add NVDA (shares optional), "
                "or paste several: /add NVDA, MSFT, LMT.")
            return
        cap = int((self.config.get("portfolio") or {}).get("max_holdings", 50))
        screened_holdings, dropped = holdings[:cap], [h.ticker for h in holdings[cap:]]
        tickers = [h.ticker for h in screened_holdings]
        # NO silent truncation — dropping an owned name hides its alerts. Screen the
        # full list up to the safety cap; warn explicitly about any overflow.
        self.notifier.send_chat_action("upload_photo")
        macro = self._fetch_macro(self.config)
        cards = self._screen_fn()(tickers, self.sources, self.config, macro=macro)
        present, _missing = self._partition_present(cards)
        summary = pf.summarize(screened_holdings, present)
        manifest = _interactive_manifest(len(tickers), len(present), "portfolio", [])
        art = self._report_fn()(present, manifest, assessments={}, macro=macro,
                                portfolio=summary)
        self._deliver_fn()(self.notifier, png=art.png, html=art.html, text=art.text,
                           caption=_caption(manifest, present),
                           session=manifest.session.isoformat())
        if dropped:
            self.notifier.send_message(
                f"⚠️ {len(dropped)} holdings NOT screened (cap {cap}): {', '.join(dropped)}. "
                "Alerts for these are INCOMPLETE — raise portfolio.max_holdings or warm the cache.")

    def _do_explain(self, term: str) -> None:
        from .glossary import entry_text, index_text, lookup, suggest
        if not term:
            self.notifier.send_message(index_text())
            return
        entry = lookup(term)
        if entry is not None:
            self.notifier.send_message(entry_text(entry))
            return
        hints = suggest(term)
        hint = f" Did you mean: {', '.join(hints)}?" if hints else ""
        self.notifier.send_message(
            f"No entry for “{term}”.{hint} Send /explain for the full list.")

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
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"shortlist-bot: {e}", file=sys.stderr)
        return 2

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
