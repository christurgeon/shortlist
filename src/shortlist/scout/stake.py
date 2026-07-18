"""Pure stake-% extraction from Schedule 13D / 13D-A cover pages (escalation-pack leaf).

Shared by the live EdgarStakeIncreaseSignal, the initial-13D meta enrichment, and the
13d-a backfill walker (the _form4.py shared-leaf pattern). ABSTAINS (None), never guesses:
row 13 ("percent of class represented") is parsed per reporting-person cover page and the
MAX is returned as the group-aggregate proxy — values outside (0, 100] are dropped; no
parseable row -> None. The registered aggregation rule: delta computations use the same
rule on both sides, so max-vs-max is internally consistent.
"""
from __future__ import annotations

import re

SIGNAL = "edgar:13d_stake_increase"
STRENGTH = 0.6          # flat, unfitted prior (the buyback precedent); deltas ride meta
MIN_INCREASE_PP = 2.0   # material-increase floor, ABSOLUTE percentage points. The backfill
                        # cohort uses THIS constant, never a config override (config tunes
                        # live only — the buyback DEFAULT_PHRASES precedent).

# XML tier: the 13D/G-modernization structured filings (late 2024+). Element name pinned
# by the Task-1 live probe — adjust there, not ad hoc.
_XML_PCT_RE = re.compile(
    r"<(?:\w+:)?percentOfClass>\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%?\s*<", re.IGNORECASE)
# Legacy tier: text/HTML cover pages. Tag-strip first, then find every row-13 block.
_TAG_RE = re.compile(r"<[^>]+>")
_TEXT_PCT_RE = re.compile(
    r"PERCENT\s+OF\s+CLASS[^%]{0,400}?([0-9]{1,3}(?:\.[0-9]+)?)\s*%",
    re.IGNORECASE | re.DOTALL)


def _valid(vals: list[float]) -> float | None:
    ok = [v for v in vals if 0 < v <= 100]
    return max(ok) if ok else None


def extract_stake_pct(raw: str | None) -> float | None:
    """Percent of class from a 13D/13D-A document (any format), else None."""
    if not raw:
        return None
    xml_hits = [float(m) for m in _XML_PCT_RE.findall(raw)]
    if xml_hits:
        return _valid(xml_hits)
    text = _TAG_RE.sub(" ", raw)
    return _valid([float(m) for m in _TEXT_PCT_RE.findall(text)])


def pair_key(filer_cik, subject_cik) -> str | None:
    """Canonical '<filer10>|<subject10>' baseline key; None when either CIK is unusable."""
    try:
        return f"{int(str(filer_cik).strip()):010d}|{int(str(subject_cik).strip()):010d}"
    except (TypeError, ValueError):
        return None
