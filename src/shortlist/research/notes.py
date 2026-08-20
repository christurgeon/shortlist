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

# Truncation is signalled in the PROMPT HEADER (assess.py), never inside the note
# text. That is a grounding requirement, not a style choice: `DebtNote.text` is a
# haystack segment, so anything mixed into it becomes quotable filing text. An
# earlier revision appended a " […truncated…]" marker to the text; normalized it is
# 13 chars against `assess._MIN_EVIDENCE_CHARS = 12`, so a model emitting the marker
# ALONE as its evidence got `verified=True` attributed to a real note — non-filing
# text passing quote-verification, exactly what CLAUDE.md forbids. A one-character
# margin is not a safety property. The header carries the signal instead, and the
# text stays purely filing bytes.
#
# What survives the move: SYSTEM_PROMPT tells the model to NAME a missing input
# rather than estimate one, which it can only do if truncation is visible somewhere.
# Note this was never the 8-K `_ELISION` case — an elision SPLICES two non-adjacent
# spans, so a quote crossing it asserts a contiguity the filing never had; truncation
# only drops a suffix. The guarantee here is the weaker, sufficient one: text past the
# cut is ABSENT from the haystack, so a model reconstructing a severed figure fails.

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


_MAX_TITLE_CHARS = 200


def _title(note: Any) -> str:
    """The note title as filed, flattened and bounded.

    The flattening is NOT cosmetic. This string is FILER-CONTROLLED and is
    interpolated into a `=== 10-K STATEMENT NOTE — {title} ===` prompt header and
    into the segment `label` that `report.py` renders. Left raw, a title carrying
    newlines and `===` can forge a section boundary in the prompt — a probe with an
    embedded `=== ITEM 1A — RISK FACTORS ===` produced a convincing fake section.
    Grounding itself is unaffected (the title is not in the haystack, so nothing
    smuggled through it can be quoted and verified), but this is the first
    filer-controlled string to reach a /deep prompt header and it does not get to
    define the prompt's structure."""
    raw = str(getattr(note, "title", None) or getattr(note, "name", "") or "")
    return re.sub(r"\s+", " ", raw).strip()[:_MAX_TITLE_CHARS]


def _norm_ws(text: Any) -> str:
    """Collapse intra-line whitespace and blank-line runs, keeping the newlines that
    separate markdown table rows. Worth doing BEFORE any cap: it cuts UAL 20,836 ->
    8,259 chars (0.40), so an un-normalized cap would spend its budget on padding.
    Costs nothing at verification — `assess._norm` collapses whitespace on both
    sides, so a quote that matched before still matches."""
    out = re.sub(r"[ \t]+", " ", str(text or ""))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def select(notes_index: Any) -> list:
    """EVERY debt/liquidity note of one filing, in document order. Empty when the
    filer files none — which for a 10-Q is the NORMAL case (JPM/XOM/LLY/T/CVS all
    file none), not a parse failure.

    Deliberately UNCAPPED — and so it takes no config: `max_notes_per_form` counts
    notes actually EMITTED, and `collect` drops candidates that render empty.
    Capping here instead would let a single empty-rendering note consume a slot and
    silently cost the filer a real note that was sitting right behind it."""
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

    A prefix cut lands on the last WHITESPACE inside the limit, never mid-token:
    the tables are the payload, and a cut through `4,100` -> `4,1` would hand a
    wrong figure to a prompt explicitly asked to do arithmetic. Token alignment
    was measured to waste 1-11 chars against up to 3,071 for row alignment (GS,
    whose note has very long lines) — design §3.4.

    When the first `limit` chars contain NO whitespace at all there is no safe cut,
    so the note is dropped rather than severed. Pathological for a markdown table
    (they are full of spaces), but "no output" beats "a truncated number presented
    as a whole one" in a brief whose prompt is told to do arithmetic on it."""
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
        # The outer net, and it has to be outer. `.notes` is a `cached_property`
        # whose body reaches `self.financials` and performs a LIVE XBRL download +
        # parse, so an HTTP error or a malformed instance surfaces on ATTRIBUTE
        # ACCESS here, not at fetch time. The 10-K `collect` call in `fetch_bundle`
        # sits outside every try there, so an escape would discard the ENTIRE brief
        # for a name whose narrative sections parsed perfectly, reported as a
        # generic "filing error". A malformed config value (`max_chars_10k: "16k"`)
        # lands in the same place.
        log_abstain(f"{form} debt notes", ticker, e)
        return []


def _collect(filing_obj: Any, form: str, accession: str, ticker: str,
             cfg: dict) -> list[DebtNote]:
    """`collect`'s body. Split out so the exception boundary can be the WHOLE thing
    — see the comment there for why a partial boundary was not enough."""
    index = getattr(filing_obj, "notes", None)
    if index is None:
        # Say so. `edgartools` is pinned only as `>=3.0` and has broken this repo by
        # renaming things before; a missing `.notes` would otherwise zero out every
        # brief's notes with no signal at all, which is exactly the "systematic
        # failure looks identical to no-data" trap log_abstain exists to prevent.
        # Also fires for a pre-XBRL filing, hence the non-committal wording.
        log_abstain(f"{form} debt notes (no `.notes` on the parsed filing — "
                    f"pre-XBRL filing or edgartools API change)", ticker,
                    AttributeError("notes"))
        return []

    budget = int(cfg.get("max_chars_10q" if str(form).upper().startswith("10-Q")
                         else "max_chars_10k") or 0)
    per_note = int(cfg.get("max_chars_per_note") or 0)
    max_notes = int(cfg.get("max_notes_per_form") or 0)
    if budget <= 0 or per_note <= 0 or max_notes <= 0:
        # Say so, for the same reason the `index is None` branch does: a config that
        # zeroes the feature must not look identical to "this filer files no debt
        # note". A malformed STRING already logs via the outer boundary; a value that
        # merely `int()`s to 0 (None, -500) would otherwise pass silently.
        log_abstain(f"{form} debt notes disabled by config "
                    f"(budget={budget}, per_note={per_note}, max_notes={max_notes})",
                    ticker, ValueError("non-positive cap"))
        return []
    out: list[DebtNote] = []
    for note in select(index):
        # `max_notes` counts EMITTED notes, so an empty-rendering candidate cannot
        # consume a slot a real note behind it wanted.
        if len(out) >= max_notes:
            break
        if budget <= 0 or (out and budget < _MIN_USEFUL_CHARS):
            break
        try:
            # `_title` is inside the try, not before it: `select` reading a title
            # successfully does not promise a second read cannot raise, and the
            # contract here is never-raises, not usually-raises.
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
