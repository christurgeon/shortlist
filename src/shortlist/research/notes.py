"""Debt & liquidity statement notes for the research brief — selection + extraction.

Closes the input gap behind a SHIPPED prompt instruction: `SYSTEM_PROMPT` asks the
model to compute refinancing coverage (debt maturing within twelve months against
cash plus operating cash flow), and the 2026-08-19 live run computed nothing
because the maturity ladder lives in a statement note and `assess.py` sends Item 1,
Item 7, Item 1A and the 10-Q MD&A only.

A note IS filing text, so like the 10-Q risk update — and unlike the proxy /
reverse-DCF / similarity context lines — it enters the grounding haystack, as its
own labelled segment so "verified" never silently widens from "the 10-K" to "a
note we sliced out of it".

This reads `TenK.notes` / `TenQ.notes` (`edgar.xbrl.notes.Notes`), which is an
XBRL-derived STRUCTURED INDEX of individually addressable notes — not a text blob.
That is the whole reason this module has no heading detection and no span slicing,
and therefore none of the `_tenq_mda` item-boundary fault class.

Every rule below is traceable to a named filing in the 20-filing probe
(`docs/audits/scripts/probe_debt_notes.py`); the design + evidence is
`docs/audits/2026-08-20-debt-liquidity-notes-design.md`. Three measured
consequences drive the shape of this module:

- **`long[- ]term obligation` exists for AMT alone.** American Tower titles its
  debt note `LONG-TERM OBLIGATIONS`, with no `debt`/`borrow` token; without that
  alternative it matched 0 of 26 notes despite carrying ~$40B of debt. It is
  deliberately narrow — bare `obligation` would also match AMT's own
  `ASSET RETIREMENT OBLIGATIONS`, which is not debt.
- **`_EXCLUDE_RE` is required, not tidiness.** Duke Energy files
  `Investments in Debt and Equity Securities`, an ASSET note matching `debt`;
  unfiltered it was selected and consumed 10,127 chars of the budget.
- **The per-note cap is 16,000 because DUK needs it.** The 12-month ladder sits
  within the first 10,000 chars in 8 of 9 over-cap notes; DUK's is at 13,022. A
  10,000 cap would silently drop the ladder for a whole sector of heavy borrowers
  while appearing to work.

Never raises: any index/render failure degrades that note (or the whole form) to
nothing via `log_abstain`. No throttle of its own — it goes through edgartools
like the rest of the research layer (CLAUDE.md).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .filings import log_abstain
from .models import DebtNote

_DEFAULTS = {
    "enabled": True,
    "max_notes_per_form": 2,     # NKE and O each legitimately file two (probe §3.1)
    "max_chars_per_note": 16000,
    "max_chars_10k": 16000,      # per-form, NOT a shared pool: a shared pool lets a
    "max_chars_10q": 8000,       # heavy borrower's annual notes crowd out the fresher
                                 # quarterly note, and freshness is why the 10-Q is here
}

_TITLE_RE = re.compile(
    r"debt|borrow|credit facilit|credit agreement|financing arrangement"
    r"|notes payable|long[- ]term obligation", re.I)

_EXCLUDE_RE = re.compile(r"investment|marketable securit|available[- ]for[- ]sale", re.I)

# Marks a prefix cut so the model can tell a severed ladder from a complete one.
# Load-bearing: SYSTEM_PROMPT tells it to NAME a missing input rather than estimate
# one, which it can only do if truncation is visible.
#
# NOTE this is NOT the 8-K `_ELISION` case and does not inherit its safety argument.
# An elision SPLICES two non-adjacent spans, so a quote crossing it asserts a
# contiguity the filing never had and must fail verification. Truncation only drops a
# suffix: nothing is spliced, so a quote containing the mark is legitimately a
# substring of what the model was shown and correctly verifies. The guarantee here is
# the weaker, sufficient one — text past the cut is ABSENT from the haystack, so a
# model that reconstructs a severed figure fails verification.
TRUNCATION_MARK = " […truncated…]"

# Below this, a leftover budget buys a sliver that carries no ladder and no usable
# clause — just prompt cost and a segment label implying more than it holds. Applied
# only once a note has been emitted, so a deliberately small configured budget still
# yields its first note rather than nothing.
_MIN_USEFUL_CHARS = 200


def config_block(config: Optional[dict]) -> dict:
    """The merged `research.notes` block. An ABSENT block is NOT a no-op — the
    feature ships ON — but `enabled: false` is byte-identical."""
    block = ((config or {}).get("research") or {}).get("notes") or {}
    return {**_DEFAULTS, **block}


def _title(note: Any) -> str:
    return str(getattr(note, "title", None) or getattr(note, "name", "") or "")


def _norm_ws(text: Any) -> str:
    """Collapse intra-line whitespace and blank-line runs, keeping the newlines that
    separate markdown table rows. Worth doing BEFORE any cap: it cuts UAL 20,836 ->
    8,259 chars (0.40), so an un-normalized cap would spend its budget on padding.
    Costs nothing at verification — `assess._norm` collapses whitespace on both
    sides, so a quote that matched before still matches."""
    out = re.sub(r"[ \t]+", " ", str(text or ""))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def select(notes_index: Any, cfg: dict) -> list:
    """The debt/liquidity notes of one filing, in document order, capped at
    `max_notes_per_form`. Empty when the filer files none — which for a 10-Q is
    the NORMAL case (JPM/XOM/LLY/T/CVS all file none), not a parse failure."""
    picked: list = []
    limit = int(cfg.get("max_notes_per_form") or 0)
    for i in range(len(notes_index)):
        if len(picked) >= limit:
            break
        try:
            note = notes_index[i]
            title = _title(note)
        except Exception:
            continue                       # one unreadable entry must not lose the rest
        if title and _TITLE_RE.search(title) and not _EXCLUDE_RE.search(title):
            picked.append(note)
    return picked


def extract(note: Any, limit: int) -> tuple[str, bool]:
    """(text, truncated) for one note, whitespace-collapsed and capped at `limit`.

    A prefix cut lands on the last WHITESPACE inside the limit, never mid-token:
    the tables are the payload, and a cut through `4,100` -> `4,1` would hand a
    wrong figure to a prompt explicitly asked to do arithmetic. Token alignment
    was measured to waste 1-11 chars against up to 3,071 for row alignment (GS,
    whose note has very long lines) — design §3.4."""
    text = _norm_ws(note.to_markdown())
    if not text:
        return "", False
    if limit <= 0:
        return "", True
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    cut = max(head.rfind(" "), head.rfind("\n"))
    payload = (head[:cut] if cut > 0 else head).rstrip()
    if not payload:
        return "", True
    return payload + TRUNCATION_MARK, True


def collect(filing_obj: Any, form: str, accession: str, ticker: str,
            cfg: dict) -> list[DebtNote]:
    """The debt & liquidity notes of one parsed filing, within the per-form char
    budget. Never raises: an unreadable notes index costs this form its notes, and
    an unrenderable note costs only itself."""
    if not cfg.get("enabled", True):
        return []
    index = getattr(filing_obj, "notes", None)
    if index is None:
        return []
    try:
        picked = select(index, cfg)
    except Exception as e:
        log_abstain(f"{form} debt notes", ticker, e)
        return []

    budget = int(cfg.get("max_chars_10q" if str(form).upper().startswith("10-Q")
                         else "max_chars_10k") or 0)
    per_note = int(cfg.get("max_chars_per_note") or 0)
    out: list[DebtNote] = []
    for note in picked:
        if budget <= 0 or (out and budget < _MIN_USEFUL_CHARS):
            break
        try:
            text, truncated = extract(note, min(per_note, budget))
        except Exception as e:
            log_abstain(f"{form} debt note render", ticker, e)
            continue
        if not text:
            continue
        title = _title(note)
        out.append(DebtNote(form=str(form), accession=str(accession or ""), title=title,
                            label=f"{form} note: {title}", text=text, truncated=truncated))
        budget -= len(text) - (len(TRUNCATION_MARK) if truncated else 0)
    return out
