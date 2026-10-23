"""Year-over-year filing-text similarity (the "Lazy Prices" signal — pure,
dependency-free leaf).

Cohen, Malloy & Nguyen (2020), "Lazy Prices" (J. Finance 75(3)): firms that
*change* the language of their 10-K/10-Q year-over-year — especially the risk
factors and MD&A — subsequently underperform. A big YoY text change is the
signal; high similarity (the firm "lazily" copied last year's text) is benign.

A similarity SCORER, unlike `riskdiff` (a block extractor). It normalizes as riskdiff
does (lowercase, strip digits/currency/punctuation) so numeric and whitespace churn does
not register as change. The metric is a stdlib **bag-of-words cosine**.

`similarity` in [0, 1]: 1.0 == identical normalized text, 0.0 == no shared
vocabulary. `None` when there is no usable baseline (either side empty).

POOR DISCRIMINATION: retaining stopwords compresses the cosine into a narrow band near
the top of the range, so the scale barely separates "the same firm changed nothing" from
"a different company". Do not read the absolute level. The paper sorts on the
CROSS-SECTIONAL RANK of similarity, so one number without a peer distribution has no
basis. Fixing this needs IDF or stopword weighting AND a reference distribution.
docs/design/PLAN_INVENTORY_DECOMPOSITION.md §0.4."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

# Stopwords are NOT removed: dropping them would make small edits look larger.

_TOKEN_RE = re.compile(r"[a-z]+")


def normalize_tokens(text: str) -> list[str]:
    """Lowercase alphabetic tokens only, so a changed dollar figure or year is not a change."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def cosine_similarity(current: str, prior: str) -> Optional[float]:
    """Bag-of-words cosine similarity in [0, 1] between two filing sections.

    Returns None when either side has no tokens: missing text must not read as
    "0.0 = totally rewritten". Identical text -> 1.0; disjoint vocabularies -> 0.0."""
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
    """YoY similarity over Item 1A + MD&A pooled into one bag per year, so the larger
    section weighs more. Returns None only when BOTH sections lack a baseline."""
    cur = (cur_risk or "") + "\n" + (cur_mda or "")
    prior = (prior_risk or "") + "\n" + (prior_mda or "")
    return cosine_similarity(cur, prior)
