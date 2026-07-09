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
_EIGHTK_SEEN_CAP = 500


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

    # --- 8-K originator + negative veto (shared capped-append helper) ---
    def _append_capped(self, key: str, items: list[str], cap: int) -> None:
        """Append new items to a rolling list under `key`, keeping insertion order and
        evicting the OLDEST past `cap`. One save."""
        lst = self._data.setdefault(key, [])
        for a in items:
            if a not in lst:
                lst.append(a)
        if len(lst) > cap:
            del lst[:len(lst) - cap]
        self._save()

    # --- 8-K originator: capped rolling accession-seen set (walk-back dedup) ---
    def eightk_seen_accessions(self) -> list[str]:
        """Accessions the 8-K originator has already surfaced (the today-2..today walk-back
        would otherwise re-emit a filing on 3 consecutive runs). Absent key (old state
        files) reads as [] — back-compatible, no migration."""
        return list(self._data.get("eightk_seen", []))

    def add_eightk_accessions(self, accessions: list[str],
                              cap: int = _EIGHTK_SEEN_CAP) -> None:
        """Append newly-surfaced accessions (rolling window ~83 days at the default
        daily_cap of 6 — far beyond the 3-day scan window it guards)."""
        self._append_capped("eightk_seen", accessions, cap)

    # --- buyback originator: capped rolling accession-seen set (walk-back dedup) ---
    def buyback_seen_accessions(self) -> list[str]:
        """Accessions the buyback originator has already surfaced (the session-2..session
        walk-back would otherwise re-emit a filing on 3 consecutive runs). Absent key (old
        state files) reads as [] — back-compatible, no migration."""
        return list(self._data.get("buyback_seen", []))

    def add_buyback_accessions(self, accessions: list[str],
                               cap: int = _EIGHTK_SEEN_CAP) -> None:
        """Append newly-surfaced buyback accessions (rolling window far beyond the 3-day
        scan window it guards; mirrors add_eightk_accessions)."""
        self._append_capped("buyback_seen", accessions, cap)

    # --- 8-K negative-item veto: map + swept-through cursor + note ledger + log set ---
    def eightk_negative_map(self) -> dict[str, dict]:
        """UPPER ticker -> {"last_date","items","adsh"} for names with a fresh negative-item
        8-K. Absent key reads as {} — back-compatible, no migration."""
        return dict(self._data.get("eightk_negative", {}))

    def eightk_negative_swept_through(self) -> str | None:
        """ISO date of the last day the veto sweep considers FINAL (it deliberately lags
        EFTS_LAG_DAYS behind the session — younger days are re-swept until EFTS catches
        up). None on old state files."""
        return self._data.get("eightk_negative_swept_through")

    def update_eightk_negative(self, records: list[dict], *, swept_through: str,
                               on: date, lookback_days: int = 30) -> None:
        """Merge negative-8-K records (`{"ticker","adsh","file_date","items",...}` — the
        eightk.negative_events_from_rows shape) into the veto map (newest filing per ticker
        wins), prune entries older than `lookback_days` (the veto horizon), advance the
        swept-through cursor (NEVER backwards), and prune the note-dedup ledger to
        (ticker, accession) pairs still in the map. One save."""
        m = self._data.setdefault("eightk_negative", {})
        for r in records:
            t = str(r.get("ticker", "")).upper()
            fd = str(r.get("file_date") or "")
            if not t or not fd:
                continue
            cur = m.get(t)
            if cur is None or str(cur.get("last_date") or "") <= fd:
                m[t] = {"last_date": fd, "items": list(r.get("items") or []),
                        "adsh": r.get("adsh")}
        for t in list(m):
            try:
                stale = (on - date.fromisoformat(str(m[t].get("last_date")))).days \
                    >= lookback_days
            except (TypeError, ValueError):
                stale = True                       # malformed entry: drop, never wedge
            if stale:
                del m[t]
        prev = self._data.get("eightk_negative_swept_through")
        if prev is None or str(prev) < swept_through:
            self._data["eightk_negative_swept_through"] = swept_through
        live = {f"{t}|{rec.get('adsh')}" for t, rec in m.items()}
        noted = self._data.get("eightk_veto_noted")
        if noted:
            self._data["eightk_veto_noted"] = [p for p in noted if p in live]
        self._save()

    def eightk_veto_note_seen(self, ticker: str, adsh: str) -> bool:
        """True when this (ticker, accession) veto has already been named in a manifest
        note — a vetoed name re-vetoes daily for up to lookback_days but is noted ONCE."""
        return f"{ticker.upper()}|{adsh}" in self._data.get("eightk_veto_noted", [])

    def mark_eightk_veto_noted(self, ticker: str, adsh: str) -> None:
        lst = self._data.setdefault("eightk_veto_noted", [])
        key = f"{ticker.upper()}|{adsh}"
        if key not in lst:
            lst.append(key)
            self._save()

    def eightk_neg_logged(self) -> list[str]:
        """Accessions already logged to the firehose as edgar:8k_negative (the lag-window
        days are re-swept every run and must not re-log). Absent key reads as []."""
        return list(self._data.get("eightk_neg_logged", []))

    def add_eightk_neg_logged(self, accessions: list[str],
                              cap: int = _EIGHTK_SEEN_CAP) -> None:
        """~8 negative 8-Ks/day observed -> a 500 cap is a ~60-day window, far beyond the
        2-3 re-swept lag days it guards."""
        self._append_capped("eightk_neg_logged", accessions, cap)

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

    # --- raw-signal firehose (pre-scorer discovery events; Phase-0 validation harness) ---
    def record_firehose(self, events, session: date) -> None:
        """Keyed upsert of the session's PRE-SCORER discovery events. `.setdefault` keeps
        old state files (which predate the "firehose" key) forward-compatible and makes a
        re-run of a session idempotent. `events` is a list of objects with .ticker, .signal
        and .to_dict()."""
        bucket = self._data.setdefault("firehose", {}).setdefault(session.isoformat(), {})
        for e in events:
            bucket[f"{e.signal}|{e.ticker.upper()}"] = e.to_dict()
        self._save()

    def firehose_events(self, on: date, lookback_days: int) -> list[dict]:
        """All recorded firehose events within the trailing window (flattened, newest
        sessions first). Tolerates malformed session keys (skips them)."""
        cutoff = on - timedelta(days=lookback_days)
        out: list[dict] = []
        for sess in sorted(self._data.get("firehose", {}), reverse=True):
            try:
                if date.fromisoformat(sess) < cutoff:
                    continue
            except ValueError:
                continue
            out.extend(self._data["firehose"][sess].values())
        return out
