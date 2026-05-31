from __future__ import annotations

import json
from pathlib import Path

from .models import TickerSnapshot

# One JSON file per ticker per fetch day: <root>/<TICKER>/<YYYY-MM-DD>.json
# Keeping the raw payloads here is what gives us a point-in-time record to learn
# from later — the data as it actually looked when the assessment was made.


def save(snapshot: TickerSnapshot, root: str | Path) -> Path:
    day = snapshot.as_of[:10]
    out_dir = Path(root) / snapshot.ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}.json"
    path.write_text(json.dumps(snapshot.to_dict(), indent=2, default=str))
    return path


def load(ticker: str, root: str | Path, day: str | None = None) -> dict:
    tdir = Path(root) / ticker.upper()
    if day:
        return json.loads((tdir / f"{day}.json").read_text())
    latest = max(tdir.glob("*.json"), key=lambda p: p.stem)
    return json.loads(latest.read_text())
