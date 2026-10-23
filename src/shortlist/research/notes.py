"""Debt & liquidity statement notes for the research brief — selection + extraction.

Supplies the maturity ladder behind `SYSTEM_PROMPT`'s refinancing-coverage instruction.
The ladder lives in a statement note, and `assess.py` otherwise sends only Item 1,
Item 7, Item 1A and the 10-Q MD&A.

A note IS filing text, so it enters the grounding haystack as its own labelled segment,
so "verified" never silently widens from "the 10-K" to "a note we sliced out of it".

This reads `TenK.notes` / `TenQ.notes` (`edgar.xbrl.notes.Notes`), an XBRL-derived
STRUCTURED INDEX of addressable notes. So there is no heading detection and no span
slicing, and none of the `_tenq_mda` item-boundary fault class.

Design and evidence: `docs/audits/2026-08-20-debt-liquidity-notes-design.md` (probe:
`docs/audits/scripts/probe_debt_notes.py`). Three rules shape this module:

- The `long[- ]term obligation` alternative exists for filers whose debt note has no
  `debt`/`borrow` token. It stays narrow: bare `obligation` also matches
  `ASSET RETIREMENT OBLIGATIONS`.
- `_EXCLUDE_RE` is required: some ASSET notes match `debt` and eat the budget.
- The per-note cap is 16,000 because a 10,000 cap silently drops the 12-month ladder
  for heavy borrowers while appearing to work.

Build any further targeted note extractor the same way. Each note family needs its own
`_EXCLUDE_RE`. None is worth building until a shipped prompt instruction waits on it.

Never raises: any index or render failure degrades that note (or the form) to nothing
via `log_abstain`. No throttle of its own: it goes through edgartools like the rest of
the research layer.
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

# Truncation is signalled in the PROMPT HEADER (assess.py), never inside the note text.
# `DebtNote.text` is a haystack segment, so any marker in it would be quotable filing
# text and could pass quote-verification as evidence. SYSTEM_PROMPT tells the model to
# NAME a missing input, which needs the truncation visible in the header. Unlike the
# 8-K `_ELISION`, truncation drops only a suffix and splices nothing: text past the cut
# is absent from the haystack, so a model reconstructing a severed figure fails.

# Below this, a leftover budget buys a sliver with no usable clause. Applied only after
# a note has been emitted, so a small configured budget still yields its first note.
_MIN_USEFUL_CHARS = 200


def config_block(config: Optional[dict]) -> dict:
    """The merged `research.notes` block. An ABSENT block is NOT a no-op — the
    feature ships ON — but `enabled: false` is byte-identical."""
    block = ((config or {}).get("research") or {}).get("notes") or {}
    return {**_DEFAULTS, **block}


_MAX_TITLE_CHARS = 200


def _title(note: Any) -> str:
    """The note title as filed, flattened and bounded.

    The flattening is NOT cosmetic. The title is FILER-CONTROLLED and goes into a
    `=== 10-K STATEMENT NOTE — {title} ===` prompt header and the segment `label`.
    Raw newlines and `===` could forge a section boundary in the prompt. Grounding is
    unaffected, since the title is not in the haystack."""
    raw = str(getattr(note, "title", None) or getattr(note, "name", "") or "")
    return re.sub(r"\s+", " ", raw).strip()[:_MAX_TITLE_CHARS]


def _norm_ws(text: Any) -> str:
    """Collapse intra-line whitespace and blank-line runs, keeping the newlines that
    separate markdown table rows. Do it BEFORE any cap, or the cap spends its budget on
    padding. Costs nothing at verification — `assess._norm` collapses whitespace on both
    sides, so a quote that matched before still matches."""
    out = re.sub(r"[ \t]+", " ", str(text or ""))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def select(notes_index: Any) -> list:
    """EVERY debt/liquidity note of one filing, in document order. Empty when the
    filer files none, which for a 10-Q is the NORMAL case.

    Deliberately UNCAPPED, so it takes no config: `max_notes_per_form` counts notes
    EMITTED, and `collect` drops candidates that render empty. Capping here would let
    an empty-rendering note consume a slot."""
    picked: list = []
    for i in range(len(notes_index)):
        try:
            note = notes_index[i]
            title = _title(note)
        except Exception:
            continue                       # one unreadable entry must not lose the rest
        if title and _TITLE_RE.search(title) and not _EXCLUDE_RE.search(title):
            picked.append(note)
    return picked


def extract(note: Any, limit: int) -> tuple[str, bool]:
    """(text, truncated) for one note, whitespace-collapsed. `text` is never longer
    than `limit` and is pure filing text — the truncation signal rides in the prompt
    header, not in here (see TRUNCATION comment above).

    A prefix cut lands on the last WHITESPACE inside the limit, never mid-token: a cut
    through `4,100` -> `4,1` would hand a wrong figure to a prompt that does arithmetic.
    Token alignment wastes far less than row alignment (design §3.4).

    When the first `limit` chars contain NO whitespace there is no safe cut, so the
    note is dropped rather than severed."""
    text = _norm_ws(note.to_markdown())
    if not text:
        return "", False
    if limit <= 0:
        return "", True
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    cut = max(head.rfind(" "), head.rfind("\n"))
    if cut <= 0:
        return "", True
    return head[:cut].rstrip(), True


def collect(filing_obj: Any, form: str, accession: str, ticker: str,
            cfg: dict) -> list[DebtNote]:
    """The debt & liquidity notes of one parsed filing, within the per-form char
    budget. Never raises: an unreadable notes index costs this form its notes, and
    an unrenderable note costs only itself."""
    if not cfg.get("enabled", True):
        return []
    if filing_obj is None:
        return []                      # no filing parsed — expected, not a failure
    try:
        return _collect(filing_obj, form, accession, ticker, cfg)
    except Exception as e:
        # The net must be outer. `.notes` is a `cached_property` that does a LIVE XBRL
        # download and parse, so errors surface on ATTRIBUTE ACCESS. The 10-K `collect`
        # call in `fetch_bundle` has no try around it, so an escape would discard the
        # whole brief. A malformed config value lands here too.
        log_abstain(f"{form} debt notes", ticker, e)
        return []


def _collect(filing_obj: Any, form: str, accession: str, ticker: str,
             cfg: dict) -> list[DebtNote]:
    """`collect`'s body, split out so the exception boundary covers all of it."""
    index = getattr(filing_obj, "notes", None)
    if index is None:
        # Log it: edgartools is pinned loosely, and a renamed `.notes` would otherwise
        # zero every brief's notes silently. Also fires for a pre-XBRL filing.
        log_abstain(f"{form} debt notes (no `.notes` on the parsed filing — "
                    f"pre-XBRL filing or edgartools API change)", ticker,
                    AttributeError("notes"))
        return []

    budget = int(cfg.get("max_chars_10q" if str(form).upper().startswith("10-Q")
                         else "max_chars_10k") or 0)
    per_note = int(cfg.get("max_chars_per_note") or 0)
    max_notes = int(cfg.get("max_notes_per_form") or 0)
    if budget <= 0 or per_note <= 0 or max_notes <= 0:
        # Log it: a config that zeroes the feature must not look like "no debt note".
        log_abstain(f"{form} debt notes disabled by config "
                    f"(budget={budget}, per_note={per_note}, max_notes={max_notes})",
                    ticker, ValueError("non-positive cap"))
        return []
    out: list[DebtNote] = []
    for note in select(index):
        # `max_notes` counts EMITTED notes.
        if len(out) >= max_notes:
            break
        if budget <= 0 or (out and budget < _MIN_USEFUL_CHARS):
            break
        try:
            # `_title` is inside the try: a second title read can raise.
            text, truncated = extract(note, min(per_note, budget))
            title = _title(note)
        except Exception as e:
            log_abstain(f"{form} debt note render", ticker, e)
            continue
        if not text:
            continue
        out.append(DebtNote(form=str(form), accession=str(accession or ""), title=title,
                            label=f"{form} note: {title}", text=text, truncated=truncated))
        budget -= len(text)            # exact: `text` is all that reaches the prompt
    return out
