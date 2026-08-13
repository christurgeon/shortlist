"""Recent 8-K substance for the research brief — selection + extraction.

An 8-K IS filing text, so unlike the proxy / reverse-DCF / similarity context
lines this enters the grounding haystack — but as its own labelled segment, so
"verified" never silently widens from "the 10-K" to "a furnished press release".

Every rule below is traceable to a named filing in the 60-filing probe
(`docs/audits/scripts/probe_8k.py`); the design + evidence is
`docs/audits/2026-08-13-eightk-text-in-deep-design.md`. Two consequences of that
measurement drive the shape of this module:

- Selection does NOT read `m.filing_events`. `EdgarSource._index_limit = 40`
  truncates a MIXED-form index before the 90-day filter — measured on JPM, 35 of
  40 rows are SCHEDULE 13G/A, collapsing the window to 26 days and dropping its
  Item 2.02 earnings release entirely. A dedicated `form="8-K"` index call is both
  the fix and one fewer dependency.
- Extraction is value-aware and body-aware. JPM 2026-06-25 files an EX-99.1 of
  length 0; NKE 2026-06-23 puts its officer-change narrative in the 17,522-char
  body while the EX-99.1 release is 5,114.

Never raises: any fetch/parse failure degrades that filing (or the whole ticker)
to today's bare `filing_events` label via `log_abstain`. No throttle of its own —
it goes through edgartools like the rest of the research layer (CLAUDE.md).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Optional

from .filings import log_abstain
from .models import EightKText

_DEFAULTS = {
    "enabled": True,
    "lookback_days": 120,
    "max_filings": 3,
    "max_chars_total": 10000,
    "max_chars_per_filing": 6000,
    "guidance_window_chars": 1500,
    "items": ["4.02", "2.02", "2.01", "1.01", "5.02"],
}

# Marks the gap the guidance splice jumps. Safe by construction: a quote spanning
# it fails the `_norm` substring check and is correctly marked unverified.
_ELISION = " […] "

# Outlook/guidance language, verbatim from the probe that measured F4 (23 releases
# scanned; JPM at 0.45 and CVX at 0.41 of the document sit past any sane prefix).
_GUIDANCE_RE = re.compile(r"outlook|guidance|we expect|expects? (?:to|revenue|full)", re.I)

_ITEM_CODE_RE = re.compile(r"\d+\.\d+")


def config_block(config: Optional[dict]) -> dict:
    """The merged `research.eightk` block. An ABSENT block is NOT a no-op — the
    feature ships ON (spec §3.6) — but `enabled: false` is byte-identical."""
    block = ((config or {}).get("research") or {}).get("eightk") or {}
    return {**_DEFAULTS, **block}


def _norm_ws(text: Any) -> str:
    """Collapse whitespace on ingest. Recovers 47-85% of exhibit bytes (§2.3) and
    costs nothing: `assess._norm` already collapses whitespace on both sides at
    verification, so a quote that matched before still matches."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _codes(items: Any) -> list[str]:
    """The `d.dd` item codes of a filing, in document order. Measured populated and
    uniformly a comma-separated str in 60/60 probe rows, but parsed by regex so a
    list or a None cannot raise."""
    return _ITEM_CODE_RE.findall(str(items or ""))


def _priority(codes: list[str], priority: list[str]) -> tuple[int, list[str]]:
    """(rank, matched codes in priority order) for one filing. `rank` is the index
    of its best priority item; `len(priority)` means "no priority item" — the
    filing keeps today's bare label and is never fetched."""
    hits = [c for c in priority if c in codes]
    return (priority.index(hits[0]) if hits else len(priority)), hits


def _ex99_rank(document_type: str) -> tuple[int, ...]:
    """Sort key over an EX-99 attachment's numbering: EX-99 -> (), EX-99.1 -> (1,),
    EX-99.2 -> (2,). The lowest sorts first, which picks JPM's 38K-char release over
    its 115K-char financial supplement (EX-99.2) and sorts LLY's bare `EX-99` first."""
    tail = document_type.upper().replace("EX-99", "", 1)
    return tuple(int(n) for n in re.findall(r"\d+", tail))


def _exhibit_text(filing: Any) -> tuple[str, str]:
    """(document_type, normalized text) of the lowest-numbered EX-99 attachment with
    NON-EMPTY text, or ("", ""). Value-aware because empty exhibits exist — JPM
    2026-06-25 files an EX-99.1 of length 0 (F3), the same lesson `_edgar_facts.py`
    already encodes. Each attachment is failure-isolated: edgartools parses
    lazily, so one unreadable exhibit must not blank the others."""
    candidates = []
    for att in (getattr(filing, "exhibits", None) or []):
        dtype = str(getattr(att, "document_type", "") or "")
        if not dtype.upper().startswith("EX-99"):
            continue
        try:
            text = _norm_ws(att.text())
        except Exception:
            continue
        if text:
            candidates.append((_ex99_rank(dtype), dtype, text))
    if not candidates:
        return "", ""
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1], candidates[0][2]


def _body_text(filing: Any) -> str:
    try:
        return _norm_ws(filing.text())
    except Exception:
        return ""


def _label(filed: str, hits: list[str], parts: list[str]) -> str:
    """The provenance label a verified quote is attributed to, e.g.
    "8-K 2026-07-30 (Item 2.02, EX-99.1)". Only the PRIORITY items are named: the
    9.01 that rides along on almost every earnings release is a pointer to the
    exhibit list, not an event."""
    inner = ", ".join(["Item " + ", ".join(hits) if hits else "Item ?", *parts])
    return f"8-K {filed} ({inner})"


def _cap(text: str, cap: int, window: int) -> str:
    """Prefix-trim to `cap`, splicing in a `window`-char excerpt around the first
    outlook/guidance hit that the prefix WOULD LOSE, marked `[…]`.

    F4: 10-K risk factors are ordered worst-first, which is what justifies the plain
    prefix slice in `cap_sections`; earnings releases are not — 2 of 23 measured
    releases place an "Outlook" section AFTER the financial tables (JPM at 0.45,
    CVX at 0.41 of the document). This is the one heuristic in the design and the
    weakest part: it is a TRADE, not a free add, since the window displaces prefix
    characters inside the same cap. Absent a lost hit the output is exactly the
    plain prefix, so the splice can only fire where the prefix already failed."""
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    head_len = cap - window - len(_ELISION)
    if window <= 0 or head_len <= 0:
        return text[:cap]
    hit = _GUIDANCE_RE.search(text, cap)
    if hit is None:
        return text[:cap]
    return text[:head_len] + _ELISION + text[hit.start():hit.start() + window]


def extract(filing: Any, hits: list[str], cap: int, window: int) -> tuple[str, list[str]]:
    """(text, source parts) for ONE selected 8-K, capped to `cap` normalized chars.

    The body is included unless the filing's priority items are exactly {2.02}: a
    pure earnings release has a cover-boilerplate body (AAPL 2026-07-30, 4,607
    chars of it), but NKE 2026-06-23 is `2.02,5.02,7.01` and its officer-change
    narrative exists ONLY in the body (F2). With no usable EX-99 the body is all
    there is anyway (CVX 2026-04-09 files a 2.02 with no exhibit at all; XOM
    2026-07-01 carries EX-3/EX-4 only)."""
    ex_type, ex_text = _exhibit_text(filing)
    body = "" if (ex_text and set(hits) == {"2.02"}) else _body_text(filing)
    chunks, parts = [], []
    if body:
        chunks.append(body)
        parts.append("body")
    if ex_text:
        chunks.append(ex_text)
        parts.append(ex_type)
    if not chunks:
        return "", []
    return _cap(" ".join(chunks), cap, window), parts


def select(filings: Any, cfg: dict,
           today: Optional[date] = None) -> list[tuple[Any, list[str], list[str]]]:
    """The qualifying 8-Ks, best-first: (filing, codes, priority hits).

    Exact form `8-K` (an 8-K/A amends a report we already read), filed within
    `lookback_days`, carrying at least one configured priority item. Ranked by item
    priority with recency as the tie-break — a non-reliance restatement (4.02)
    unconditionally stops a thesis, so it outranks a fresher earnings release.
    Pure given an iterable of filing-like objects, so the whole ranking is testable
    without the network."""
    priority = [str(c) for c in cfg["items"]]
    cutoff = ((today or date.today()) - timedelta(days=int(cfg["lookback_days"]))).isoformat()
    rows = []
    for f in filings:
        if str(getattr(f, "form", "")) != "8-K":
            continue
        filed = str(getattr(f, "filing_date", "") or "")
        if not filed or filed < cutoff:
            continue
        codes = _codes(getattr(f, "items", None))
        rank, hits = _priority(codes, priority)
        if not hits:
            continue
        rows.append((rank, filed, f, codes, hits))
    # Two stable passes rather than one composite key: `filed` is a string, so it
    # cannot be negated into a descending component of the same sort key.
    rows.sort(key=lambda r: r[1], reverse=True)
    rows.sort(key=lambda r: r[0])
    return [(f, codes, hits) for _, _, f, codes, hits in rows[:int(cfg["max_filings"])]]


def fetch_eightks(ticker: str, config: Optional[dict] = None,
                  company_factory=None, today: Optional[date] = None) -> list[EightKText]:
    """Recent 8-K substance for `ticker`, best-first, within the total char budget.

    NEVER RAISES — the contract `_prior_year_sections` already uses: a dead SEC
    endpoint, a missing [edgar] extra or an unparseable exhibit degrades that
    filing (or the whole ticker) to today's bare `filing_events` label, with a
    stderr line so a systematic failure is not indistinguishable from "this name
    filed nothing". Returns [] when disabled or when nothing qualifies.

    The budget is walked in priority order — each filing takes
    `min(max_chars_per_filing, remaining)` and the walk stops at exhaustion — so
    the highest-priority filing is never squeezed by a rebalancing pass.

    `company_factory` exists ONLY so tests can inject a fake without patching
    `sys.modules`; production takes the lazy `edgar` import (the [edgar] extra is
    optional, so it must not be imported at module scope).
    """
    cfg = config_block(config)
    if not cfg.get("enabled", True):
        return []
    try:
        if company_factory is None:
            from edgar import Company
            company_factory = Company
        selected = select(company_factory(ticker).get_filings(form="8-K"), cfg, today)
    except Exception as e:
        log_abstain("8-K index fetch failed", ticker, e)
        return []

    out: list[EightKText] = []
    remaining = int(cfg["max_chars_total"])
    window = int(cfg["guidance_window_chars"])
    for filing, codes, hits in selected:
        if remaining <= 0:
            break
        try:
            text, parts = extract(
                filing, hits, min(int(cfg["max_chars_per_filing"]), remaining), window)
        except Exception as e:
            log_abstain("8-K text extraction failed", ticker, e)
            continue
        if not text:
            continue
        filed = str(getattr(filing, "filing_date", "") or "")
        out.append(EightKText(
            accession=str(getattr(filing, "accession_no", "") or ""),
            filed=filed, items=",".join(codes),
            label=_label(filed, hits, parts), text=text))
        remaining -= len(text)
    return out
