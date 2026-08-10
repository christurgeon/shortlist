"""Year-over-year filing-text similarity (the "Lazy Prices" signal — pure,
dependency-free leaf).

Cohen, Malloy & Nguyen (2020), "Lazy Prices" (J. Finance 75(3)): firms that
*change* the language of their 10-K/10-Q year-over-year — especially the risk
factors and MD&A — subsequently underperform. A big YoY text change is the
signal; high similarity (the firm "lazily" copied last year's text) is benign.

This is a NEW similarity SCORER, not an extension of `riskdiff` (which is a
block-EXTRACTOR returning newly-added blocks, Item-1A-only). We do reuse
riskdiff's normalization idea (lowercase, collapse whitespace, strip digits/
currency/punctuation) so that boilerplate/numeric churn — a changed dollar
figure, a rolled-forward fiscal year, reflowed whitespace — does NOT register as
a substantive change. The metric is a stdlib **bag-of-words cosine** over the
normalized token multiset (`collections.Counter`); no new dependency.

`similarity` in [0, 1]: 1.0 == identical normalized text, 0.0 == no shared
vocabulary. `None` when there is no usable baseline (either side empty). The
caller turns a LOW similarity into the advisory `filing_text_change` flag."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

# Stopwords are NOT removed: in 10-K text the connective/boilerplate words are
# part of the "did they rewrite it" signal, and dropping them would make small
# substantive edits look larger. We normalize instead (below).

_TOKEN_RE = re.compile(r"[a-z]+")


def normalize_tokens(text: str) -> list[str]:
    """Tokenize for similarity: lowercase, drop digits / currency / punctuation
    (so a changed dollar figure or year is not a change), keep only alphabetic
    word tokens. Mirrors riskdiff._key's normalization intent, applied to the
    WHOLE section rather than a 160-char prefix."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def cosine_similarity(current: str, prior: str) -> Optional[float]:
    """Bag-of-words cosine similarity in [0, 1] between two filing sections.

    Returns None when either side normalizes to no tokens (no baseline to
    compare against — never fabricate a "0.0 = totally rewritten" from missing
    text). Identical normalized text -> 1.0; disjoint vocabularies -> 0.0.
    Whitespace / number / caption-number churn cancels out via normalize_tokens,
    so it does NOT depress the score (the boilerplate false-positive guard)."""
    a = Counter(normalize_tokens(current))
    b = Counter(normalize_tokens(prior))
    if not a or not b:
        return None
    shared = a.keys() & b.keys()
    dot = sum(a[t] * b[t] for t in shared)
    if dot == 0:
        return 0.0
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    # Clamp: float rounding can nudge an identical-text cosine just past 1.0.
    return min(1.0, dot / (norm_a * norm_b))


def combined_similarity(
    cur_risk: str, prior_risk: str, cur_mda: str, prior_mda: str
) -> Optional[float]:
    """Overall YoY similarity across Item 1A (risk factors) + MD&A — the two
    sections the Lazy-Prices paper found carry the drift.

    Pools the normalized tokens of both sections into one bag per year and takes
    a single cosine, so the score is length-weighted toward the bigger section
    (a wholesale MD&A rewrite is not diluted by an unchanged risk section).
    Returns None only when BOTH sections lack a usable baseline; a section that
    is empty on one side simply contributes no tokens for that side."""
    cur = (cur_risk or "") + "\n" + (cur_mda or "")
    prior = (prior_risk or "") + "\n" + (prior_mda or "")
    return cosine_similarity(cur, prior)
