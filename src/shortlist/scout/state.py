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

_EMPTY: dict = {"screened": {}, "runs": [], "held": []}


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
                warnings.warn(f"ScoutState: corrupt {self.path}, starting fresh: {e}")
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

    # --- held list ---
    def set_held(self, tickers: list[str]) -> None:
        self._data["held"] = [t.upper() for t in tickers]
        self._save()

    def is_held(self, ticker: str) -> bool:
        return ticker.upper() in self._data.get("held", [])
