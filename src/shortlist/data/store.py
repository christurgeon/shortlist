from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from .models import TickerSnapshot

# One file per ticker per fetch day: <root>/<TICKER>/<YYYY-MM-DD>.json.gz
# (legacy stores also contain uncompressed <YYYY-MM-DD>.json — readable forever,
# never rewritten). Keeping the raw payloads is what gives us a point-in-time
# record to learn from later — the data as it actually looked when the
# assessment was made; gzip (~8.5:1 measured) keeps that affordable at
# full-watchlist breadth (~5 MB/day vs ~40).


def _day_of(path: Path) -> str:
    """Filename -> ISO day. NOTE: Path("X.json.gz").stem is "X.json" (the
    double-suffix trap), so normalize explicitly."""
    name = path.name
    if name.endswith(".json.gz"):
        return name[: -len(".json.gz")]
    return path.stem


def _files_by_day(tdir: Path) -> dict[str, Path]:
    """day -> file, deduping a same-day .json/.json.gz twin (.json.gz wins)."""
    out: dict[str, Path] = {}
    for p in sorted(tdir.glob("*.json")) + sorted(tdir.glob("*.json.gz")):
        out[_day_of(p)] = p          # gz listed second -> wins the twin
    return out


def _read(path: Path) -> dict:
    if path.name.endswith(".json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text())


def save(snapshot: TickerSnapshot, root: str | Path) -> Path:
    """Persist a snapshot atomically as gzipped compact JSON. The write goes to
    a temp file in the target directory and is then os.replace()'d into place,
    so a process killed mid-write can never leave a truncated file. A legacy
    uncompressed twin for the same day is unlinked (e.g. a --force re-run after
    the gzip migration) so day listings never double-count."""
    day = snapshot.as_of[:10]
    out_dir = Path(root) / snapshot.ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}.json.gz"
    data = json.dumps(snapshot.to_dict(), separators=(",", ":"), default=str)
    # PID-unique temp so an overlapping run (manual + timer) can't clobber a
    # half-written temp. Suffix MUST stay `.tmp` or the data globs would see it.
    tmp = out_dir / f".{day}.{os.getpid()}.json.gz.tmp"
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            f.write(data)
        os.replace(tmp, path)           # atomic on POSIX within the same directory
    except BaseException:
        tmp.unlink(missing_ok=True)     # a failed write must not leak the temp file
        raise
    legacy = out_dir / f"{day}.json"
    if legacy.exists():
        legacy.unlink()
    return path


def load(ticker: str, root: str | Path, day: str | None = None) -> dict:
    """Load a ticker's snapshot for `day` (or the latest); FileNotFoundError if none."""
    tdir = Path(root) / ticker.upper()
    if day:
        for cand in (tdir / f"{day}.json.gz", tdir / f"{day}.json"):
            if cand.exists():
                return _read(cand)
        raise FileNotFoundError(f"no snapshot for {ticker.upper()} on {day} under {root}")
    files = _files_by_day(tdir) if tdir.is_dir() else {}
    if not files:
        raise FileNotFoundError(f"no snapshots for {ticker.upper()} under {root}")
    # ISO-date keys (YYYY-MM-DD) => lexicographic max == chronologically latest.
    return _read(files[max(files)])


def captured_days(ticker: str, root: str | Path) -> list[str]:
    """Sorted ISO days for which a snapshot exists for this ticker (empty if none)."""
    tdir = Path(root) / ticker.upper()
    if not tdir.is_dir():
        return []
    return sorted(_files_by_day(tdir))


def capture_days(root: str | Path) -> list[str]:
    """Sorted distinct ISO days present ANYWHERE in the store (empty if none).

    Store-wide history depth, as opposed to `captured_days`' per-ticker view: a
    day on which only some tickers were captured still counts, because it is a
    day the grid can observe. Same-day .json/.json.gz twins collapse to one day.
    """
    rdir = Path(root)
    if not rdir.is_dir():
        return []
    days: set[str] = set()
    for tdir in rdir.iterdir():
        if tdir.is_dir():        # the live store keeps `_runs.jsonl` beside the tickers
            days.update(_files_by_day(tdir))
    return sorted(days)
