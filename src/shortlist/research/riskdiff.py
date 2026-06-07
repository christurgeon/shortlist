"""Year-over-year Item-1A risk-factor diff (pure, dependency-free leaf).

Surfaces risk-factor blocks present in the current 10-K's Item 1A but NOT in the
prior year's — a documented alpha signal. v1 splits each Item 1A into blocks on
blank-line boundaries and compares a normalized prefix of each block (the lead
clause/caption), so cosmetic numeric/date edits do not read as new and
reordered-but-unchanged blocks are not flagged. (Dedicated bold-heading detection
is deferred — the prefix key approximates it.) Used by the research layer only;
the prior-year text is a diff INPUT and is never shown to the model whole."""
from __future__ import annotations

import difflib
import re

_DEFAULTS = {"similarity_threshold": 0.5, "max_blocks": 4, "max_chars": 12000}
_KEY_PREFIX_CHARS = 160


def _cfg(config: dict) -> dict:
    rd = (config or {}).get("research", {}).get("risk_diff", {}) or {}
    return {**_DEFAULTS, **rd}


def _split_blocks(text: str) -> list[str]:
    """Split an Item 1A section into blocks on blank-line boundaries. Works for
    both heading-structured and plain-prose sections (uniform paragraph split)."""
    if not text:
        return []
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def _key(block: str) -> str:
    """Normalized comparison key: a prefix of the block, lowercased, whitespace
    collapsed, digits/currency/punctuation stripped (so a changed dollar figure or
    year is not a 'new' risk)."""
    head = block[:_KEY_PREFIX_CHARS]
    head = re.sub(r"\s+", " ", head).strip().lower()
    return re.sub(r"[\d$%,.\-]", "", head)


def added_risk_blocks(current_1a: str, prior_1a: str, config: dict) -> str:
    """Return the verbatim current-year risk blocks that have no close match in
    the prior year, capped by max_blocks/max_chars. Empty string if none (or if
    either input is empty — no baseline to diff against)."""
    cfg = _cfg(config)
    cur_blocks = _split_blocks(current_1a)
    prior_blocks = _split_blocks(prior_1a)
    if not cur_blocks or not prior_blocks:
        return ""
    prior_keys = [_key(b) for b in prior_blocks]
    threshold = cfg["similarity_threshold"]
    added: list[str] = []
    for block in cur_blocks:
        k = _key(block)
        if not k:
            continue
        best = max((difflib.SequenceMatcher(None, k, pk).ratio() for pk in prior_keys),
                   default=0.0)
        if best < threshold:
            added.append(block)
        if len(added) >= cfg["max_blocks"]:
            break
    out = "\n\n".join(added)
    return out[: cfg["max_chars"]]
