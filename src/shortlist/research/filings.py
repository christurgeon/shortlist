from __future__ import annotations

import dataclasses
import os
import sys
from datetime import date
from typing import Any, Optional

from ..env import redact_secrets
from . import riskdiff, textsim
from .models import FilingBundle, FilingText


def _cap(s: str, n) -> str:
    """Prefix-trim `s` to `n` chars; no-op when `n` is falsy or `s` already fits."""
    return s if not n or len(s) <= n else s[:n]


def cap_sections(filing: FilingText, max_chars: dict | None) -> FilingText:
    """Prefix-trim each 10-K narrative section to its configured char cap, so the
    model prompt and the grounding haystack (filing.combined()) read identical text.
    Absent/None caps => filing returned unchanged (byte-identical). 10-K risk factors
    are ordered worst-first, so a prefix slice keeps the material content."""
    if not max_chars:
        return filing

    return dataclasses.replace(
        filing,
        business=_cap(filing.business, max_chars.get("business")),
        mda=_cap(filing.mda, max_chars.get("mda")),
        risk_factors=_cap(filing.risk_factors, max_chars.get("risk_factors")),
    )


def cap_bundle(bundle: "FilingBundle", max_chars: dict | None) -> "FilingBundle":
    """Cap the 10-K sections AND the 10-Q MD&A to their configured char limits so
    the model prompt and grounding haystack stay identical and bounded. The
    added-risk text is already capped by riskdiff and is left untouched here.
    Absent caps => bundle returned unchanged."""
    if not max_chars:
        return bundle
    capped_tenk = cap_sections(bundle.tenk, max_chars)
    tenq = _cap(bundle.tenq_mda, max_chars.get("tenq_mda"))
    return dataclasses.replace(bundle, tenk=capped_tenk, tenq_mda=tenq)


def _section(tenk: Any, name: str) -> str:
    value = getattr(tenk, name, None)
    return str(value) if value else ""


def _build_filing_text(ticker: str, accession: Any, filing_date: Any, tenk: Any) -> FilingText:
    """Map an edgartools TenK object (+ its filing's accession/date) into FilingText.
    Each section is independent; missing sections become empty strings."""
    return FilingText(
        ticker=ticker,
        accession=str(accession or ""),
        filing_date=str(filing_date or ""),
        business=_section(tenk, "business"),
        mda=_section(tenk, "management_discussion"),
        risk_factors=_section(tenk, "risk_factors"),
    )


_OVER_CAPTURE_FRACTION = 0.50


def _note_over_capture(text: str, tenq: Any, ticker: str) -> None:
    """stderr note when the extracted MD&A span is >= half the whole 10-Q: a NEIGHBOURING
    item's heading went undetected, so the span swallowed it.

    OBSERVABILITY ONLY: it must never change the returned text. Over-captured spans
    still start at a genuine MD&A heading, and the prefix kept by
    `research.max_chars.tenq_mda` is genuine MD&A prose. An absent or raising `doc`
    skips the check silently."""
    try:
        whole = len(tenq.doc.text())
        if not whole:
            return
        frac = len(text) / whole
        if frac >= _OVER_CAPTURE_FRACTION:
            print(f"research: 10-Q MD&A span looks over-captured for {ticker or '?'}: "
                  f"{len(text)} of {whole} document chars ({frac:.2f}); a neighbouring "
                  f"item heading was likely undetected", file=sys.stderr)
    except Exception:
        return


def _tenq_mda(tenq: Any, ticker: str = "") -> str:
    """10-Q MD&A is Part I, Item 2 — NOT a `management_discussion` attribute (that
    exists only on TenK). Returns "" on any extraction failure and says so on stderr: an
    empty span is an edgartools heading-detection failure, not "this filer has no
    MD&A", and must not look like no-data.

    TWO NON-FIXES that look obvious (`tests/research/test_filings.py`):
    - `tenq["Item 2"]` is NOT a fallback: it can return *Part II* Item 2 (share
      repurchases), wrong content labelled MD&A.
    - `tenq.items` is NOT a guard: it lists misleading entries for filers whose MD&A
      extracts fine, so it reports phantom failures.
    Recovering the missing span is deferred: it would inject wrong text into the
    grounding haystack. See docs/audits/2026-08-14-tenq-mda-recovery-kill.md."""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        # markdown=True applies to the legacy-fallback path only.
        value = getter("Part I", "Item 2", markdown=True) if getter is not None else None
        text = str(value) if value else ""
    except Exception as e:
        log_abstain("10-Q MD&A extraction failed", ticker or "?", e)
        return ""
    if not text:
        # Not an exception, so log_abstain does not fit; keep its message shape.
        print(f"research: 10-Q MD&A empty (Part I Item 2 not detected) for "
              f"{ticker or '?'}", file=sys.stderr)
        return ""
    _note_over_capture(text, tenq, ticker)
    return text


def _tenq_risk_factors(tenq: Any, ticker: str = "") -> str:
    """10-Q risk factors are Part II Item 1A — NOT a `risk_factors` attribute (that
    exists only on TenK; `TenQ.risk_factors` is always ""). Same getter and never-raises
    contract as `_tenq_mda`: "" on any failure or missing section. Used only by
    `filing_text_change`'s 10-Q arm (`_filing_sections`).

    RETURNS THE RAW SECTION, A KNOWN WEAKNESS OF THE METRIC. Unlike `_tenq_added_risks`,
    it neither DIFFS against the 10-K Item 1A nor caps. Many filers restate every risk
    factor quarterly, so that near-identical section dominates the pooled bag in
    `textsim.combined_similarity` and drags the similarity toward 1.0. `filing_text_change`
    has no production caller; any producer wired to it must fix this first (route
    through `riskdiff.added_risk_blocks`, or cap)."""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        value = getter("Part II", "Item 1A", markdown=True) if getter is not None else None
        return str(value) if value else ""
    except Exception as e:
        log_abstain("10-Q risk factors extraction failed", ticker or "?", e)
        return ""


_TENQ_RISK_DEFAULTS = {"enabled": True, "max_blocks": 4, "max_chars": 4000}


def _tenq_risk_cfg(config: Optional[dict]) -> dict:
    """Own config block, NOT `research.risk_diff`, which tunes the YoY 10-K diff.
    An absent block ships ON."""
    blk = ((config or {}).get("research") or {}).get("tenq_risk_update") or {}
    return {**_TENQ_RISK_DEFAULTS, **blk}


def _tenq_added_risks(tenq: Any, tenk_risk_factors: str, config: Optional[dict]) -> str:
    """Risk-factor blocks in the 10-Q's Part II Item 1A that are NOT already in the
    10-K's Item 1A — the quarter's *changes*. Returns "" on any failure.

    A DIFF, not the raw section, and that is load-bearing: some filers restate every risk
    factor quarterly, so the raw section can run to tens of thousands of chars that the
    10-K Item 1A in the same prompt already carries. Diffing keeps only the new blocks.

    No boilerplate filter: a section can open with "no material changes..." and still
    list new risk factors, so a regex on that sentence would drop real content.

    `tenk_risk_factors` is the UNCAPPED 10-K section (cap_bundle runs later); a fuller
    baseline can only reduce false 'new' blocks."""
    cfg = _tenq_risk_cfg(config)
    if not cfg.get("enabled", True):
        return ""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        if getter is None:
            return ""
        # markdown=True mirrors _tenq_mda.
        value = getter("Part II", "Item 1A", markdown=True)
        current = str(value) if value else ""
    except Exception:
        return ""
    if not current or not tenk_risk_factors:
        # No baseline => abstain. The raw section is the dump this function prevents.
        return ""
    return riskdiff.added_risk_blocks(current, tenk_risk_factors, {"research": {"risk_diff": {
        k: v for k, v in cfg.items() if k != "enabled"}}})


def _fiscal_year(filing: Any) -> Optional[int]:
    """Best-effort fiscal year from a filing's period_of_report (YYYY-MM-DD)."""
    por = str(getattr(filing, "period_of_report", "") or "")
    return int(por[:4]) if por[:4].isdigit() else None


def _similarity_enabled(config: Optional[dict]) -> bool:
    """Similarity ships ON: an ABSENT block is not a no-op (see `docs/audits/README.md`,
    Lazy Prices)."""
    block = ((config or {}).get("research") or {}).get("text_similarity") or {}
    return bool(block.get("enabled", True))


def _prior_year_sections(ticker: str, company_factory=None, filings=None) -> tuple[str, str]:
    """(risk_factors, mda) from the prior fiscal year's 10-K: the diff baseline and the
    similarity baseline, from ONE parsed filing. Excludes 10-K/A amendments and selects
    by fiscal year, not 'second most recent'. ("", "") if there is no genuinely-prior
    annual report. Never raises.

    `company_factory` lets tests inject a fake. The `edgar` import is lazy because the
    [edgar] extra is optional. `filings` lets a caller pass an already-fetched 10-K
    index to save a request; None fetches it here."""
    try:
        if filings is None:
            if company_factory is None:
                from edgar import Company
                company_factory = Company
            filings = company_factory(ticker).get_filings(form="10-K")
        rows = [f for f in filings if str(getattr(f, "form", "")) == "10-K"]
        if len(rows) < 2:
            return "", ""
        rows.sort(key=lambda f: str(getattr(f, "filing_date", "")), reverse=True)
        current_fy = _fiscal_year(rows[0])
        for f in rows[1:]:
            fy = _fiscal_year(f)
            if current_fy is None or fy is None or fy < current_fy:
                tenk = f.obj()
                return _section(tenk, "risk_factors"), _section(tenk, "management_discussion")
        return "", ""
    except Exception as e:
        log_abstain("prior-year 10-K fetch failed", ticker, e)
        return "", ""


def _filing_sections(obj: Any, form: str, ticker: str = "") -> tuple[str, str]:
    """(risk_factors, mda) text for a parsed filing object. 10-K risk factors and
    MD&A are the `risk_factors`/`management_discussion` attributes; a 10-Q has
    neither — its risk factors are Part II Item 1A (see _tenq_risk_factors) and its
    MD&A is Part I Item 2 (see _tenq_mda). Returns "" for any missing section.
    `ticker` is passed through so the 10-Q extractors' stderr diagnostics name the
    issuer."""
    is_tenq = str(form).upper().startswith("10-Q")
    risk = _tenq_risk_factors(obj, ticker) if is_tenq else _section(obj, "risk_factors")
    mda = _tenq_mda(obj, ticker) if is_tenq else _section(obj, "management_discussion")
    return risk, mda


def _acceptance_date(filing: Any) -> str:
    """Sortable point-in-time date for a filing: its acceptance/filing date as a
    'YYYY-MM-DD' string. Uses filing_date (the date the filing became public);
    a string compare orders ISO dates correctly. "" if unknown (sorts first)."""
    return str(getattr(filing, "filing_date", "") or "")


def require_identity(identity: Optional[str] = None) -> None:
    """Resolve the SEC EDGAR contact-email identity (explicit override, else the
    SEC_IDENTITY env var) and register it with edgartools via `set_identity`.
    Raises RuntimeError if neither is set — the SEC requires a contact identity
    on every request. Shared by every EDGAR-fetching entry point in this package
    (fetch_10k / filing_text_change / proxy.fetch_proxy); process-global, so it's
    safe to call once per fetch."""
    from edgar import set_identity  # lazy: optional [edgar] extra

    ident = identity or os.environ.get("SEC_IDENTITY")
    if not ident:
        raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
    set_identity(ident)


def log_abstain(action: str, ticker: str, e: Exception) -> None:
    """stderr line for the 'never raises, degrade to the abstain value' contract
    shared by several EDGAR fetchers here and in proxy.py: a systematic failure
    must not silently look identical to 'no data for this ticker'."""
    print(f"research: {action} for {ticker}: "
          f"{type(e).__name__}: {redact_secrets(str(e))[:200]}", file=sys.stderr)


def filing_text_change(
    ticker: str,
    form: str = "10-K",
    as_of: Optional[str] = None,
    identity: Optional[str] = None,
) -> Optional[dict]:
    """POINT-IN-TIME "Lazy Prices" similarity for `ticker`.

    Compares the current same-`form` filing against the immediately-prior same-type
    filing, using only filings dated `<= as_of` (ISO 'YYYY-MM-DD'; None == now). This is
    the look-ahead guard for backtest and replay.

    Returns {similarity, current_accession, prior_accession, current_date, prior_date},
    or None when there is no usable pair or no extractable text. Never raises.
    Requires SEC_IDENTITY (the [edgar] extra).
    """
    from edgar import Company  # lazy: optional [edgar] extra

    require_identity(identity)
    try:
        filings = Company(ticker).get_filings(form=form)
        # Exact-form only, then filing_date <= as_of: the point-in-time cut.
        rows = [f for f in filings if str(getattr(f, "form", "")) == form]
        if as_of is not None:
            rows = [f for f in rows if _acceptance_date(f) and _acceptance_date(f) <= as_of]
        if len(rows) < 2:
            return None
        rows.sort(key=_acceptance_date, reverse=True)
        current, prior = rows[0], rows[1]
        cur_risk, cur_mda = _filing_sections(current.obj(), form, ticker)
        pri_risk, pri_mda = _filing_sections(prior.obj(), form, ticker)
        sim = textsim.combined_similarity(cur_risk, pri_risk, cur_mda, pri_mda)
        if sim is None:
            return None
        return {
            "similarity": sim,
            "current_accession": str(getattr(current, "accession_no", "") or ""),
            "prior_accession": str(getattr(prior, "accession_no", "") or ""),
            "current_date": _acceptance_date(current),
            "prior_date": _acceptance_date(prior),
        }
    except Exception as e:
        # Abstain to None, but log why so a systematic failure is visible.
        log_abstain("filing_text_change failed", ticker, e)
        return None


def _latest_exact_10k(filings: Any) -> Any:
    """The newest exact-form 10-K in a filings index, or None if it holds only
    amendments. edgartools returns 10-K/A rows inside `get_filings(form="10-K")`, and an
    amendment is usually a Part III patch with no Item 1/1A/7. `.latest(1)` would pick a
    document the brief cannot be built from: either an abstain with a misleading "no
    10-K", or a partial brief that passes `has_content()`.

    Selects by filing_date, as `_prior_year_sections` and `filing_text_change` do. Cost:
    a genuine full-restatement amendment is ignored for the superseded original."""
    rows = [f for f in filings if str(getattr(f, "form", "")) == "10-K"]
    if not rows:
        return None
    rows.sort(key=lambda f: str(getattr(f, "filing_date", "")), reverse=True)
    return rows[0]


def _fetch_10k_parsed(
        ticker: str,
        identity: Optional[str] = None) -> tuple[Optional[FilingText], Any, Any, Any]:
    """(FilingText|None, parsed filing object|None, filing|None, filings index) — the
    shared core of fetch_10k.

    Split out so `fetch_bundle` reads the statement notes off the SAME parsed object,
    avoiding a second `.obj()` parse.

    The third element is the FILING, not the parsed object: `controls.detect` needs the
    whole document text and `TenK` has no text accessor. Item 9A alone is not a
    substitute; `part_ii_item_9a` can be empty.

    The 4th element is the raw 10-K filings index, returned so `fetch_bundle` can pass it
    to `_prior_year_sections` and avoid a second fetch."""
    from edgar import Company  # lazy: optional [edgar] extra

    require_identity(identity)
    filings = Company(ticker).get_filings(form="10-K")
    # `is None`, not truthiness: a falsy row must not fall through to an amendment.
    latest = _latest_exact_10k(filings)
    if latest is None:
        latest = filings.latest(1)      # amendment-only filer: nothing else to use
    if latest is None:
        return None, None, None, filings
    tenk = latest.obj()
    filing = _build_filing_text(
        ticker, getattr(latest, "accession_no", ""), getattr(latest, "filing_date", ""), tenk)
    return (filing if filing.has_content() else None), tenk, latest, filings


def fetch_10k(ticker: str, identity: Optional[str] = None) -> Optional[FilingText]:
    """Fetch the latest 10-K narrative for `ticker` via edgartools.
    Returns None if there is no usable 10-K (e.g. foreign filers file 20-F) or
    all narrative sections are empty. Raises RuntimeError if SEC_IDENTITY is unset.
    """
    return _fetch_10k_parsed(ticker, identity)[0]


def no_10k_reason(ticker: str) -> str:
    """Human-readable explanation for why `fetch_bundle` returned None, for the
    `skipped` field. Best-effort: a cheap filings-index lookup (no document parse)
    distinguishes a foreign issuer that files Form 20-F from a name with no annual
    report at all. Never raises and makes no document download — falls back to the
    generic reason. Assumes `set_identity` already ran (fetch_10k sets it)."""
    try:
        from edgar import Company  # lazy: optional [edgar] extra
        if Company(ticker).get_filings(form="20-F").latest(1) is not None:
            return ("no 10-K — files Form 20-F (foreign issuer); "
                    "research briefs currently cover 10-K filers only")
    except Exception:
        pass
    return "no 10-K"


def _detect_controls(filing: Any, tenk_obj: Any, accession: str, ticker: str,
                     config: Optional[dict] = None):
    """The 10-K's adverse internal-control conclusion, or None. Never raises.

    COSTS ~2 EXTRA SEC REQUESTS per brief: `filing.text()` re-downloads rather than
    reusing what `.obj()` parsed. They go through the shared sec.gov throttle. A
    section-only route was rejected because it misses known positives (see
    docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md). Skipped when the block
    is disabled."""
    from . import controls as controls_mod

    cfg = controls_mod.config_block(config)
    if not cfg.get("enabled", True):
        return None
    try:
        period = str(getattr(tenk_obj, "period_of_report", "") or "")
        if not period:
            return None
        return controls_mod.detect(
            filing.text(), date.fromisoformat(period), cfg, accession=accession)
    except Exception as e:
        log_abstain("controls conclusion detection failed", ticker or "?", e)
        return None


def fetch_bundle(ticker: str, identity: Optional[str] = None,
                 config: Optional[dict] = None) -> Optional[FilingBundle]:
    """Fetch the documents for one research brief: the current 10-K (required), the
    latest 10-Q MD&A, and the YoY added-risk diff. Returns None ONLY when the
    current 10-K is unusable (matches fetch_10k's contract); the 10-Q and diff
    degrade to "" on any failure (failure isolation)."""
    from edgar import Company  # lazy: optional [edgar] extra

    # Sets identity; raises if SEC_IDENTITY is unset. Keeps the parsed object for the notes.
    tenk, tenk_obj, tenk_filing, tenk_filings = _fetch_10k_parsed(ticker, identity)
    if tenk is None:
        return None

    # Imported here, not at module scope: `notes` imports log_abstain from this module.
    from . import notes as debtnotes
    ncfg = debtnotes.config_block(config)
    debt_notes = debtnotes.collect(tenk_obj, "10-K", tenk.accession, ticker, ncfg)

    controls = _detect_controls(tenk_filing, tenk_obj, tenk.accession, ticker, config)

    tenq_mda, tenq_acc, tenq_added = "", "", ""
    try:
        latest_q = Company(ticker).get_filings(form="10-Q").latest(1)
        if latest_q is not None and str(getattr(latest_q, "form", "")) == "10-Q":
            # ONE parse feeds Part I Item 2, Part II Item 1A and the debt notes.
            qobj = latest_q.obj()
            tenq_mda = _tenq_mda(qobj, ticker)
            tenq_added = _tenq_added_risks(qobj, tenk.risk_factors, config)
            tenq_acc = str(getattr(latest_q, "accession_no", "") or "")
            # KEEP LAST, right after `tenq_acc`. The `except` below resets the three
            # 10-Q strings but NOT `debt_notes`, so nothing may raise between the
            # accession assignment and this append.
            debt_notes += debtnotes.collect(qobj, "10-Q", tenq_acc, ticker, ncfg)
    except Exception:
        tenq_mda, tenq_acc, tenq_added = "", "", ""

    prior_1a, prior_mda = _prior_year_sections(ticker, filings=tenk_filings)
    added = riskdiff.added_risk_blocks(tenk.risk_factors, prior_1a, config or {})
    similarity = None
    if _similarity_enabled(config):
        similarity = textsim.combined_similarity(
            tenk.risk_factors, prior_1a, tenk.mda, prior_mda)

    # Imported here, not at module scope: `eightk` imports log_abstain from this module.
    from . import eightk
    eightks = eightk.fetch_eightks(ticker, config)

    cache_key = f"{tenk.accession}+{tenq_acc}" if tenq_acc else tenk.accession
    # 8-K accessions belong in the FILING half of the key: selection is independent of
    # `filing_events`, so `context_digest` cannot see an 8-K outside EdgarSource's index,
    # and a fresh release would bust the brief only via the day bucket. Sorted for
    # determinism; no 8-K appends nothing.
    for acc in sorted(e.accession for e in eightks if e.accession):
        cache_key += f"+{acc}"
    return FilingBundle(
        tenk=tenk, primary_accession=tenk.accession, cache_key=cache_key,
        filing_date=tenk.filing_date, tenq_mda=tenq_mda, added_risks_text=added,
        text_similarity=similarity, eightks=eightks, tenq_added_risks=tenq_added,
        tenq_accession=tenq_acc, debt_notes=debt_notes, controls=controls)
