"""Idempotent scout ledger: cooldown, run-completed markers, held list.

Single-writer (the one-shot daily timer). Read-modify-write the whole JSON file
on each mutation — small enough that this is simpler and safer than partial writes.
"""
from __future__ import annotations

import copy
import json
import warnings
from datetime import date, timedelta
from pathlib import Path

_EMPTY: dict = {"screened": {}, "runs": [], "held": [], "picks": {}}


class ScoutState:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except json.JSONDecodeError as e:
                # A corrupt ledger must not crash the daily run; start fresh.
                warnings.warn(f"ScoutState: corrupt {self.path}, starting fresh: {e}", stacklevel=2)
        # Deep-copy so nested lists/dicts are not shared across ScoutState instances.
        return copy.deepcopy(_EMPTY)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))

    # --- cooldown ---
    def record_screened(self, tickers: list[str], session: date) -> None:
        for t in tickers:
            self._data["screened"][t.upper()] = session.isoformat()
        self._save()

    def in_cooldown(self, ticker: str, on: date, cooldown_days: int) -> bool:
        iso = self._data["screened"].get(ticker.upper())
        if not iso:
            return False
        last = date.fromisoformat(iso)
        return on - last < timedelta(days=cooldown_days)

    # --- idempotency ---
    def run_completed(self, session: date) -> bool:
        return session.isoformat() in self._data["runs"]

    def mark_run_completed(self, session: date) -> None:
        iso = session.isoformat()
        if iso not in self._data["runs"]:
            self._data["runs"].append(iso)
            self._save()

    # --- yahoo WAF cooldown ---
    def yahoo_blocked_on(self, on: date) -> bool:
        """True while a prior Yahoo WAF block is still in effect (rest-of-day cooldown).
        Absent key (old state files) reads as not-blocked — backward compatible."""
        iso = self._data.get("yahoo_blocked_until")
        return bool(iso) and on <= date.fromisoformat(iso)

    def yahoo_blocked_until(self) -> str | None:
        return self._data.get("yahoo_blocked_until")

    def mark_yahoo_blocked(self, through: date) -> None:
        """Skip Yahoo through `through` (inclusive). Pass the session date for a
        rest-of-calendar-day cooldown: same-day re-runs skip, the next day resumes."""
        self._data["yahoo_blocked_until"] = through.isoformat()
        self._save()

    # --- FINRA settlement cycle (short-interest originator emits once per new cycle) ---
    def finra_last_settlement(self) -> str | None:
        """The last FINRA settlement cycle the short-interest signal emitted on. Absent
        key (old state files) reads as None — back-compatible, no migration."""
        return self._data.get("finra_last_settlement")

    def set_finra_cycle(self, settlement: str) -> None:
        """Record that the short-interest signal has emitted on this settlement cycle, so
        the same bi-monthly cohort isn't re-surfaced daily until a newer cycle publishes."""
        self._data["finra_last_settlement"] = settlement
        self._save()

    # --- held list ---
    def set_held(self, tickers: list[str]) -> None:
        self._data["held"] = [t.upper() for t in tickers]
        self._save()

    def is_held(self, ticker: str) -> bool:
        return ticker.upper() in self._data.get("held", [])

    # --- selection ledger (picks tracked over time) ---
    def record_picks(self, picks, session: date) -> None:
        """Keyed upsert of the session's surfaced picks. `.setdefault` keeps old state
        files (which predate the "picks" key) forward-compatible; re-running a session
        updates rather than duplicates. `picks` is a list of objects with .ticker +
        .to_dict()."""
        bucket = self._data.setdefault("picks", {}).setdefault(session.isoformat(), {})
        for p in picks:
            bucket[p.ticker.upper()] = p.to_dict()
        self._save()

    def recent_picks(self, on: date, lookback_days: int) -> list[dict]:
        """All recorded picks within the trailing window (flattened, newest sessions
        first). Tolerates malformed session keys (skips them)."""
        cutoff = on - timedelta(days=lookback_days)
        out: list[dict] = []
        for sess in sorted(self._data.get("picks", {}), reverse=True):
            try:
                if date.fromisoformat(sess) < cutoff:
                    continue
            except ValueError:
                continue
            out.extend(self._data["picks"][sess].values())
        return out
