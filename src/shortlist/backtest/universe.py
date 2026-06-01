"""Resolve a universe spec to a ticker list: a bundled named list, or ad-hoc CSV.

The bundled 'largecap' list is currently-listed constituents and is therefore
survivorship-biased (delisted names absent) — see the file header and the report
caveats. It exists to give the price-only momentum path enough cross-sectional
breadth to compute a meaningful IC today, with no API key.
"""
from __future__ import annotations

from pathlib import Path

_NAMED = {"largecap": "universe_largecap.txt"}


def load_universe(spec: str) -> list[str]:
    if spec in _NAMED:
        path = Path(__file__).parent / _NAMED[spec]
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.upper())
        seen, uniq = set(), []
        for t in out:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq
    return [t.strip().upper() for t in spec.split(",") if t.strip()]
