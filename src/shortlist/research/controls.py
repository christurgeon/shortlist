"""Adverse internal-control conclusions (Item 9A) from a filing's own text.

A FACT rather than a composite: management's own conclusion that internal control over
financial reporting (ICFR) or disclosure controls and procedures (DCP) were not
effective.

Two rules make it work, and neither is optional.

**The phrase alone is worthless.** A bare `"material weakness"` search matches nearly
every filer, because the auditor's standard definition paragraph is in nearly every
10-K. Only the adverse-CONCLUSION phrasings in `_PHRASES` discriminate.

**Tense decides everything.** The dominant false positive is a prior-period weakness,
since remediated, discussed in a later filing. A phrase counts only when its own
sentence anchors to THIS filing's period: an "as of <date>" within `window_chars`
matching `period_of_report`, or the self-referential "end of the period covered by this
report" that filers use instead of a date.

Whitespace normalization happens HERE, not in the caller. On raw section text the
window straddles newlines and the date match fails.

`detect` takes `form` and works on a 10-Q unchanged. It is NOT wired to one on purpose:
the 10-Q path adds no lead time over the 10-K path. Evidence:
`docs/audits/2026-09-07-tenq-controls-base-rate.md`. Detection accuracy and knob
sensitivity: `docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md`. A window of
1600 chars bleeds into unrelated dates.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .models import ControlsFinding

# Adverse-conclusion phrasings only. The plural ICFR variant is not redundant: some
# filers use it alone.
_ICFR_PHRASES = (
    "internal control over financial reporting was not effective",
    "internal controls over financial reporting were not effective",
    "did not maintain effective internal control over financial reporting",
)
_DCP_PHRASE = "disclosure controls and procedures were not effective"
_PHRASES = _ICFR_PHRASES + (_DCP_PHRASE,)

# Filers who write this instead of a date assert currency in words.
#
# DO NOT widen this to accept "this quarterly report" (or "this Form 10-K"): it gained
# nothing, because the `_AS_OF` branch already rescues every case.
# `docs/audits/2026-09-07-tenq-controls-base-rate.md` §4.
_SELF_REF = re.compile(r"end of the period covered by this (annual )?report", re.I)
# Case-insensitive on "as of" because sentences can open with it; the capitalized
# month keeps it from matching prose.
_AS_OF = re.compile(r"as of ([A-Z][a-z]+ \d{1,2},? \d{4})", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

_DEFAULTS = {"enabled": True, "window_chars": 240, "tolerance_days": 20,
             "max_quote_chars": 600}

# A quote shorter than this cannot be attributed to a sentence and is not worth
# showing; `assess._MIN_EVIDENCE_CHARS` is 12 for the same reason.
_MIN_QUOTE_CHARS = 40


def config_block(config: Optional[dict]) -> dict:
    """The merged `research.controls` block. An ABSENT block is NOT a no-op — the
    feature ships ON — but `enabled: false` is byte-identical."""
    block = ((config or {}).get("research") or {}).get("controls") or {}
    return {**_DEFAULTS, **block}


def _norm(text: str) -> str:
    return re.sub(r"[\s ]+", " ", str(text or ""))


def _parse_as_of(raw: str) -> Optional[date]:
    m = re.match(r"([A-Z][a-z]+) (\d{1,2}),? (\d{4})", raw)
    if not m or m.group(1) not in _MONTHS:
        return None
    try:
        return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def _sentence_around(text: str, index: int, max_chars: int) -> str:
    """The sentence containing `index`, bounded. Pure filing text: this becomes a
    grounding segment, so nothing computed may be mixed in (see ControlsFinding)."""
    start = text.rfind(". ", max(0, index - max_chars), index)
    start = 0 if start < 0 else start + 2
    end = text.find(". ", index)
    end = len(text) if end < 0 else end + 1
    return text[start:min(end, start + max_chars)].strip()


def detect(text: str, period_end: Optional[date], cfg: dict,
           form: str = "10-K", accession: str = "") -> Optional[ControlsFinding]:
    """The adverse conclusion in `text` that anchors to `period_end`, or None.

    Pure and never raises: a caller with no period (`period_of_report` absent) or a
    disabled config gets None, which is the same thing an all-clear filing gets. The
    two are distinguishable upstream only by whether this was called at all — a
    deliberate simplification, since both render as "no finding" in the prompt.
    """
    if not cfg.get("enabled", True) or not text or period_end is None:
        return None
    flat = _norm(text)
    low = flat.lower()
    window = int(cfg.get("window_chars", _DEFAULTS["window_chars"]))
    tol = int(cfg.get("tolerance_days", _DEFAULTS["tolerance_days"]))
    max_quote = int(cfg.get("max_quote_chars", _DEFAULTS["max_quote_chars"]))

    for phrase in _PHRASES:
        start = 0
        while True:
            i = low.find(phrase, start)
            if i < 0:
                break
            start = i + 1
            ctx = flat[max(0, i - window): i + window]
            as_of: Optional[str] = None
            if _SELF_REF.search(ctx):
                as_of = period_end.isoformat()
            else:
                dates = [d for d in (_parse_as_of(x) for x in _AS_OF.findall(ctx)) if d]
                if not dates:
                    continue
                nearest = min(dates, key=lambda d: abs((d - period_end).days))
                if abs((nearest - period_end).days) > tol:
                    continue
                as_of = nearest.isoformat()
            quote = _sentence_around(flat, i, max_quote)
            if len(quote) < _MIN_QUOTE_CHARS:
                continue
            basis = "icfr" if phrase in _ICFR_PHRASES else "dcp"
            return ControlsFinding(
                form=form, accession=accession, basis=basis, as_of=as_of,
                label=f"{form} controls conclusion", quote=quote)
    return None


def context_line(finding: Optional[ControlsFinding]) -> str:
    """The PROMPT-ONLY line. The verdict and the as-of date are DERIVED, so they must
    stay out of the grounding haystack — the quote is the only part that is filing
    text, and it reaches the model as its own segment via `FilingBundle.segments`."""
    if finding is None:
        return ""
    what = ("internal control over financial reporting"
            if finding.basis == "icfr" else "disclosure controls and procedures")
    return (f"Internal controls (context only — the filing's own words are shown as a "
            f"separate labelled section): management concluded {what} were NOT "
            f"effective as of {finding.as_of}, per the {finding.form}.")
