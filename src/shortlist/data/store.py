from __future__ import annotations

import json
import os
from pathlib import Path

from .models import TickerSnapshot

# One JSON file per ticker per fetch day: <root>/<TICKER>/<YYYY-MM-DD>.json
# Keeping the raw payloads here is what gives us a point-in-time record to learn
# from later — the data as it actually looked when the assessment was made.


def save(snapshot: TickerSnapshot, root: str | Path) -> Path:
    """Persist a snapshot atomically. The write goes to a temp file in the target
    directory and is then os.replace()'d into place, so a process killed mid-write
    (e.g. a daily accumulation job) can never leave a truncated JSON file."""
    day = snapshot.as_of[:10]
    out_dir = Path(root) / snapshot.ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}.json"
    data = json.dumps(snapshot.to_dict(), indent=2, default=str)
    # PID-unique temp so an overlapping run (e.g. manual + timer) can't clobber a
    # half-written temp. Suffix MUST stay `.tmp` (not `.json`) or the `*.json` globs
    # in load()/captured_days() would pick it up.
    tmp = out_dir / f".{day}.{os.getpid()}.json.tmp"
    tmp.write_text(data)
    os.replace(tmp, path)               # atomic on POSIX within the same directory
    return path


def load(ticker: str, root: str | Path, day: str | None = None) -> dict:
    """Load a ticker's snapshot for `day` (or the latest); FileNotFoundError if none."""
    tdir = Path(root) / ticker.upper()
    if day:
        return json.loads((tdir / f"{day}.json").read_text())
    if not tdir.is_dir():
        raise FileNotFoundError(f"no snapshots for {ticker.upper()} under {root}")
    files = list(tdir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no snapshots for {ticker.upper()} under {root}")
    # ISO-date filenames (YYYY-MM-DD) => lexicographic max == chronologically latest.
    latest = max(files, key=lambda p: p.stem)
    return json.loads(latest.read_text())


def captured_days(ticker: str, root: str | Path) -> list[str]:
    """Sorted ISO days for which a snapshot exists for this ticker (empty if none).
    The query the store didn't previously expose — needed for idempotent capture
    and accumulation status."""
    tdir = Path(root) / ticker.upper()
    if not tdir.is_dir():
        return []
    return sorted(p.stem for p in tdir.glob("*.json"))
