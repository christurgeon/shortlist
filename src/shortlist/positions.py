"""Bot-owned position store (positions.json) — the register/remove/view foundation.

Pure + dependency-light (stdlib + Holding), the portfolio.py / _form4.py leaf pattern.
The ONLY writer of positions.json is the bot; the daily run reads it but never writes it
(see docs/POSITION_MONITOR.md §3.1). Atomic writes mirror ScoutState._save.
"""
from __future__ import annotations

import json
import os
from datetime import date, timezone, datetime
from pathlib import Path
from typing import Optional

from .portfolio import Holding


def _today() -> date:                       # seam for tests
    return datetime.now(timezone.utc).date()


def _empty() -> dict:
    return {"version": 1, "positions": {}}


def load_store(path) -> dict:
    """Parse positions.json leniently. Missing/unreadable/corrupt -> empty, never raises."""
    p = Path(path)
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("positions"), dict):
        return _empty()
    data.setdefault("version", 1)
    return data


def save_store(path, store: dict) -> None:
    """Atomic write (PID-unique sibling temp + os.replace), the ScoutState._save pattern."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def add_or_update(store: dict, ticker: str, *, shares: Optional[float] = None,
                  entry_card: Optional[dict] = None) -> None:
    """Create a position (added=today) or fill/update shares+entry_card on an existing one,
    preserving `added`, `thesis`, and the ORIGINAL `entry_card` (never overwritten)."""
    t = ticker.strip().upper()
    positions = store.setdefault("positions", {})
    if t not in positions:
        positions[t] = {"added": _today().isoformat(), "shares": shares,
                        "thesis": None, "entry_card": entry_card}
        return
    rec = positions[t]
    if shares is not None:
        rec["shares"] = shares
    if entry_card is not None and not rec.get("entry_card"):
        rec["entry_card"] = entry_card      # only fill if empty — never clobber the baseline


def set_thesis(store: dict, ticker: str, thesis: str) -> bool:
    t = ticker.strip().upper()
    rec = store.get("positions", {}).get(t)
    if rec is None:
        return False
    rec["thesis"] = thesis
    return True


def remove(store: dict, ticker: str) -> Optional[dict]:
    """Pop and return the full record (for the decision ledger), or None if absent."""
    t = ticker.strip().upper()
    return store.get("positions", {}).pop(t, None)


def holdings_view(store: dict) -> list[Holding]:
    return [Holding(t, rec.get("shares")) for t, rec in store.get("positions", {}).items()]


def no_thesis_tickers(store: dict) -> list[str]:
    return [t for t, rec in store.get("positions", {}).items() if not rec.get("thesis")]


def append_decision(path, record: dict) -> None:
    """Append one JSON line to decisions.jsonl (append-only; parent dir created)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
