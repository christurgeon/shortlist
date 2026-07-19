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


def stake_pct_from_filing(filing) -> float | None:
    """Best-effort percent-of-class from an edgartools Filing: structured XML first
    (13D/G modernization, late 2024+), then raw HTML, then rendered text. The html
    tier recovers sibling-div legacy cover pages the text renderer drops; live-probed
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


def fetch_prior_stake(subject_cik, filer_cik10: str, before, identity: str,
                      _get_company=None) -> float | None:
    """Cold-start baseline: the latest 13D-family filing for (subject, filer) STRICTLY
    before `before`, parsed for stake %. One bounded EDGAR company-filings lookup; never
    raises -> None. `_get_company(cik, identity) -> list[Filing]` is the test seam."""
    try:
        if _get_company is None:
            def _get_company(cik, identity):
                from edgar import Company, set_identity
                set_identity(identity)
                out = []
                for form in ("SCHEDULE 13D", "SC 13D"):
                    try:
                        out.extend(list(Company(int(str(cik))).get_filings(form=form)))
                    except Exception:  # noqa: BLE001
                        continue
                return out
        from datetime import date as _date
        best = None
        for f in _get_company(subject_cik, identity):
            fd = str(getattr(f, "filing_date", "") or "")[:10]
            try:
                if _date.fromisoformat(fd) >= before:
                    continue
            except ValueError:
                continue
            try:
                filers = getattr(f.header, "filers", None)
                raw_fc = filers[0].company_information.cik if filers else None
                if not raw_fc or f"{int(raw_fc):010d}" != filer_cik10:
                    continue                    # a different holder's filing on the subject
            except Exception:  # noqa: BLE001 — unreadable filer -> can't confirm the pair
                continue
            if best is None or str(getattr(best, "filing_date", "")) < fd:
                best = f
        return stake_pct_from_filing(best) if best is not None else None
    except Exception:  # noqa: BLE001 — cold-start convenience, never a crash
        return None
