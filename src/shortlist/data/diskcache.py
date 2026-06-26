"""Shared best-effort JSON disk-cache primitives.

A dependency-free data-layer leaf. Several keyless sources day-cache a bulk payload
on disk with the same shape — read the file if it parses, otherwise treat it as a
miss and refetch; write it back without ever letting a cache failure break a live
run. That idiom used to be copy-pasted across ``YahooSource._get_chart``,
``FinraSource``, ``GovContractsSource``, ``LobbyingSource`` and
``apewisdom.fetch_wsb_mentions``; it lives here once now.

Out of scope by design: ``macro.py`` (TTL-gated single-file fetch) and
``backtest/xbrl.py`` (a different layer, with "treat as miss" rather than "refetch"
semantics) keep their own shapes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def read_json_cache(path: Path) -> Optional[Any]:
    """Return the JSON value cached at ``path``, or ``None`` if it is absent or
    unreadable. A present-but-falsy payload (``[]``/``{}``/``0``) reads back as
    itself — only a missing file or a parse/IO error is a miss (the caller
    distinguishes ``None`` and refetches). Corrupt/partial files are swallowed."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass  # corrupt cache -> refetch
    return None


def write_json_cache(path: Path, obj: Any) -> None:
    """Best-effort write of ``obj`` as JSON to ``path``, creating parent dirs.
    A write failure is non-fatal — it is swallowed and the next run refetches."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))
    except Exception:
        pass  # cache write failure is non-fatal
