"""Year-over-year Item-1A risk-factor diff (pure, dependency-free leaf).

Surfaces risk-factor blocks present in the current 10-K's Item 1A but NOT in the
prior year's. Each Item 1A is split into blocks on blank-line boundaries. A block is
"added" only when its normalized lead clause matches no prior block AND its text is not
contained in the whole prior section (`containment_threshold` of its 8-word runs, or the
whole text for a block too short to have one). The second test is needed because page
breaks split paragraphs at different points each year, so a carried-over block can start
mid-sentence and miss on its prefix. Digits and punctuation are ignored, so a changed
figure or year does not read as new. Used by the research layer only; the
prior-year text is a diff INPUT and is never shown to the model whole."""
from __future__ import annotations

import difflib
import re

_DEFAULTS = {"similarity_threshold": 0.5, "containment_threshold": 0.9, "max_blocks": 4,
             "max_chars": 12000}
_KEY_PREFIX_CHARS = 160
_SHINGLE_WORDS = 8


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


def _letters(text: str) -> str:
    return " ".join(re.sub(r"[^a-z]+", " ", text.lower()).split())


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _letters(text).split()
    n = _SHINGLE_WORDS
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


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
    prior_shingles = _shingles(prior_1a)
    prior_letters = f" {_letters(prior_1a)} "
    contained = cfg["containment_threshold"]
    threshold = cfg["similarity_threshold"]
    added: list[str] = []
    for block in cur_blocks:
        if len(added) >= cfg["max_blocks"]:
            break
        k = _key(block)
        if not k:
            continue
        best = max((difflib.SequenceMatcher(None, k, pk).ratio() for pk in prior_keys),
                   default=0.0)
        if best >= threshold:
            continue
        shingles = _shingles(block)
        if shingles:
            if len(shingles & prior_shingles) / len(shingles) >= contained:
                continue
        elif not _letters(block) or f" {_letters(block)} " in prior_letters:
            continue
        added.append(block)
    out = "\n\n".join(added)
    return out[: cfg["max_chars"]]
