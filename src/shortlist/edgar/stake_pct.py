"""Pure stake-% extraction from Schedule 13D / 13D-A cover pages.

ABSTAINS (None), never guesses: row 13 ("percent of class represented") is parsed per
reporting-person cover page and the MAX is returned as the group-aggregate proxy — values
outside (0, 100] are dropped; no parseable row -> None. Delta computations use the same
rule on both sides, so max-vs-max is internally consistent.

Rescued from the retired `scout/stake.py` on 2026-08-10. The 13D/A *stake-increase signal*
was dropped on its INSUFFICIENT verdict (monthly alpha −2.0% raw / −4.4% scored, CIs
entirely below zero — `docs/audits/2026-07-19-13d-a-stake-increase-backfill-verdict.md`);
this extraction is not the signal, and `backtest/edgar_history.py` still uses it.
"""
from __future__ import annotations

import re

# XML tier: the 13D/G-modernization structured filings (late 2024+). Element name pinned
# by a live probe — adjust there, not ad hoc.
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


def stake_pct_from_filing(filing) -> float | None:
    """Best-effort percent-of-class from an edgartools Filing: structured XML first
    (13D/G modernization, late 2024+), then raw HTML, then rendered text. The html tier
    recovers sibling-div legacy cover pages the text renderer drops; live-probed
    2026-07-18. Never raises -> None."""
    for getter in ("xml", "html", "text"):
        try:
            raw = getattr(filing, getter)()
        except Exception:  # noqa: BLE001 — a missing/broken document tier -> next tier
            continue
        pct = extract_stake_pct(raw)
        if pct is not None:
            return pct
    return None
