# src/shortlist/data/coverage_adapt.py
"""Adapt a merged harness TickerSnapshot to the (outcomes, contributed) shape that
shortlist.coverage.build_coverage consumes, so harness-engine cards carry the same
per-source diagnostic the screener path produces.

`outcomes`: source -> "ok" | "gated_402" | "error". The harness records failures as
strings in snapshot.errors, prefixed "<source>: ..." (plain colon form),
"<source>-<phase>: ..." (e.g. "edgar-financials: ..."), or
"<source>.<section>: ..." (e.g. "fmp.profile: ...", "finnhub.metrics: ...").
A 402 substring -> gated_402; any other error -> "error"; absence -> "ok".
`contributed`: sources that supplied >=1 field, from snapshot.provenance (populated
by merge_snapshots)."""
from __future__ import annotations

from .models import TickerSnapshot


def _source_of(err: str, known: list[str]) -> str:
    head = err.split(":", 1)[0].strip()
    # Sources prefix errors as "<source>: ...", "<source>-<phase>: ..." (edgar-financials),
    # or "<source>.<section>: ..." (fmp.profile, finnhub.quote). Reduce to the base source.
    base = head.split("-", 1)[0].split(".", 1)[0]
    return base if base in known else head


def snapshot_to_coverage_inputs(snap: TickerSnapshot, sources: list[str]) -> tuple[dict, set]:
    contributed: set = set()
    for srcs in snap.provenance.values():
        contributed.update(srcs)

    err_by_source: dict[str, list[str]] = {}
    for e in snap.errors:
        err_by_source.setdefault(_source_of(e, sources), []).append(e.lower())

    outcomes: dict[str, str] = {}
    for s in sources:
        errs = err_by_source.get(s, [])
        if any("402" in e for e in errs):
            outcomes[s] = "gated_402"
        elif errs:
            outcomes[s] = "error"
        else:
            outcomes[s] = "ok"
    return outcomes, contributed
