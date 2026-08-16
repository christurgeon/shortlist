"""Resolve a universe spec to a ticker list: a bundled named list, or ad-hoc CSV.

The bundled 'largecap' list is currently-listed constituents and is therefore
survivorship-biased (delisted names absent) — see the file header and the report
caveats. It exists to give the price-only momentum path enough cross-sectional
breadth to compute a meaningful IC today, with no API key.
"""
from __future__ import annotations

from pathlib import Path

_NAMED = {"largecap": "universe_largecap.txt", "smallmid": "universe_smallmid.txt"}


def is_named_universe(spec: str) -> bool:
    """True for a BUNDLED universe ('largecap'/'smallmid'), false for an ad-hoc CSV.

    The freshness guard applies only to the bundled lists: those are committed
    reproducibility artifacts that rot silently over years, while an ad-hoc
    `--tickers AAPL,MSFT` is the caller's own deliberate choice (and may legitimately
    name synthetic or delisted symbols). Restricting it also keeps the guard — and its
    one network call — out of every offline test that passes explicit tickers."""
    return spec in _NAMED


def stale_tickers(tickers, known: dict | None) -> list[str]:
    """Universe symbols absent from SEC's current ticker map, upper-cased and in
    first-seen order. Pure — the caller supplies the map.

    A stale symbol is NOT inert: it raises CompanyNotFoundError, contributes nothing,
    and silently shrinks the cross-section against the ~30-name IC trust floor.
    Measured 2026-08-15: 8 of 238 committed tickers had gone stale unnoticed.

    An empty or absent `known` map means "we could not check", NOT "everything is
    dead", so it returns [] — SEC being unreachable must never block a backtest
    (the abstain-never-block pattern `nasdaq_universe` uses).

    LIMITATION, deliberate: this catches symbols that VANISH. It cannot catch one
    REASSIGNED to a different issuer (`B` -> Barrick Mining), which still resolves.
    Only pinning CIKs would; see docs/audits/2026-08-14-tenq-mda-recovery-kill.md.
    """
    if not known:
        return []
    out, seen = [], set()
    for t in tickers or []:
        tk = str(t).strip().upper()
        if not tk or tk in seen or tk in known:
            continue
        seen.add(tk)
        out.append(tk)
    return out


def load_universe(spec: str) -> list[str]:
    if spec in _NAMED:
        path = Path(__file__).parent / _NAMED[spec]
        tickers = (line.strip().upper() for line in path.read_text().splitlines())
        tickers = [t for t in tickers if t and not t.startswith("#")]
        return list(dict.fromkeys(tickers))    # de-dup, preserve first-seen order
    return [t.strip().upper() for t in spec.split(",") if t.strip()]
