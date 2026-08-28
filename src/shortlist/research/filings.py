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
    """stderr note when the extracted MD&A span is >= half the whole 10-Q — the
    mirror image of the INTC gap: a NEIGHBOURING item's heading went undetected, so
    this span swallowed it. Measured 2026-08-14 over 35 large caps: JPM 0.846, MCD
    0.644, PFE 0.566 against a median 0.230 and p90 0.397, so the threshold sits in
    a clean gap.

    OBSERVABILITY ONLY — it must never change the returned text. All three
    over-capturing spans start at a genuine MD&A heading, and the prefix surviving
    `research.max_chars.tenq_mda` (40,000) is genuine MD&A prose, so the model sees
    correct content. Whole-document length is neither cheap nor guaranteed, so an
    absent or raising `doc` skips the check silently rather than failing."""
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
    exists only on TenK). Returns "" on any extraction failure, and says so on
    stderr: an empty span is a systematic edgartools heading-detection failure, not
    "this filer has no MD&A", and looking identical to no-data is how INTC's silent
    0-char gap (1 of 35 large caps, measured 2026-08-14) went unnoticed.

    TWO MEASURED NON-FIXES, both of which look obvious (`tests/research/test_filings.py`):
    - `tenq["Item 2"]` is NOT a fallback. On INTC it returns 2,459 chars of *Part II*
      Item 2 (share repurchases) — wrong content silently labelled MD&A.
    - `tenq.items` is NOT a guard. XOM/TSLA/MCD list misleading entries yet extract
      69,820 / 49,879 / 122,045 chars, so an `items` check reports phantom failures.
    Recovering the missing span (slicing the containing Part I Item 1 blob at an MD&A
    heading) is deliberately deferred — fitted to n=1, and it would inject wrong text
    into the grounding haystack."""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        # markdown=True is passed for the legacy-fallback path only; on the current
        # parser path get_item_with_part returns Section.text() and ignores it.
        value = getter("Part I", "Item 2", markdown=True) if getter is not None else None
        text = str(value) if value else ""
    except Exception as e:
        log_abstain("10-Q MD&A extraction failed", ticker or "?", e)
        return ""
    if not text:
        # Not an exception, so log_abstain (which formats one) does not fit; keep the
        # same `research: <action> for <ticker>` shape it prints.
        print(f"research: 10-Q MD&A empty (Part I Item 2 not detected) for "
              f"{ticker or '?'}", file=sys.stderr)
        return ""
    _note_over_capture(text, tenq, ticker)
    return text


def _tenq_risk_factors(tenq: Any, ticker: str = "") -> str:
    """10-Q risk factors are Part II Item 1A — NOT a `risk_factors` attribute (that
    exists only on TenK; `TenQ.risk_factors` is always "", verified live 10/10 names,
    TODO.md §2a). Mirrors `_tenq_mda`'s extraction pattern: same getter, same
    never-raises contract, "" on any failure or missing section. Used only by
    `filing_text_change`'s 10-Q arm (`_filing_sections`).

    RETURNS THE RAW SECTION, AND THAT IS A KNOWN WEAKNESS OF THE METRIC — do not read
    `_tenq_added_risks` above as precedent that this is safe. That function reads the
    same Part II Item 1A but DIFFS it against the 10-K Item 1A and caps it at 4,000
    chars, which is exactly what makes it safe; nothing here does either.
    `textsim.combined_similarity` pools both sections into ONE bag, so the cosine is
    length-weighted — and 4 filers in 10 restate EVERY risk factor quarterly (measured
    2026-08-14: GILD 84K, NVDA 43K, AAPL 19K chars). For those names this near-identical
    section dominates the pool and drags the similarity toward 1.0, diluting exactly the
    wholesale-MD&A-rewrite signal `combined_similarity`'s docstring says the weighting
    exists to protect. Blast radius today is nil — `filing_text_change` has no
    production caller — but ANY producer wired to it must fix this first (route through
    `riskdiff.added_risk_blocks`, or cap), not inherit it. `TODO.md` §2a."""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        value = getter("Part II", "Item 1A", markdown=True) if getter is not None else None
        return str(value) if value else ""
    except Exception as e:
        log_abstain("10-Q risk factors extraction failed", ticker or "?", e)
        return ""


_TENQ_RISK_DEFAULTS = {"enabled": True, "max_blocks": 4, "max_chars": 4000}


def _tenq_risk_cfg(config: Optional[dict]) -> dict:
    """Own config block, deliberately NOT `research.risk_diff`: that one tunes the
    YoY 10-K diff, which feeds a different prompt section. Absent block ships ON."""
    blk = ((config or {}).get("research") or {}).get("tenq_risk_update") or {}
    return {**_TENQ_RISK_DEFAULTS, **blk}


def _tenq_added_risks(tenq: Any, tenk_risk_factors: str, config: Optional[dict]) -> str:
    """Risk-factor blocks in the 10-Q's Part II Item 1A that are NOT already in the
    10-K's Item 1A — the quarter's *changes*. Returns "" on any failure.

    A DIFF, not the raw section, and that is load-bearing. Measured live on 10 large
    caps (2026-08-14): raw Part II Item 1A spans 204-84,281 chars because four of ten
    restate every risk factor quarterly (GILD 84K, NVDA 43K, AAPL 19K, DIS 17.6K) —
    text the 10-K Item 1A in the same prompt already carries. Feeding it raw would
    duplicate up to 84K chars against a `timeout_s` ceiling a heavy filer already
    approaches. Diffing collapses all ten to <3K while keeping the genuinely new
    blocks (AAPL's new AI-compute-capacity risk, DIS's IP-litigation risk).

    No boilerplate filter: NVDA's section OPENS with "there have been no material
    changes..." and then lists 2,949 chars of new risk factors, so a regex on that
    sentence would drop real content. The diff already collapses a true boilerplate
    filer to ~200 chars.

    `tenk_risk_factors` is the UNCAPPED 10-K section (cap_bundle runs later) — a
    fuller baseline can only reduce false 'new' blocks."""
    cfg = _tenq_risk_cfg(config)
    if not cfg.get("enabled", True):
        return ""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        if getter is None:
            return ""
        # markdown=True mirrors _tenq_mda: honoured on the legacy-fallback path,
        # ignored by the current parser (which returns Section.text()).
        value = getter("Part II", "Item 1A", markdown=True)
        current = str(value) if value else ""
    except Exception:
        return ""
    if not current or not tenk_risk_factors:
        # No baseline => abstain. Emitting the raw section here is exactly the
        # 84K-char dump this function exists to prevent.
        return ""
    return riskdiff.added_risk_blocks(current, tenk_risk_factors, {"research": {"risk_diff": {
        k: v for k, v in cfg.items() if k != "enabled"}}})


def _fiscal_year(filing: Any) -> Optional[int]:
    """Best-effort fiscal year from a filing's period_of_report (YYYY-MM-DD)."""
    por = str(getattr(filing, "period_of_report", "") or "")
    return int(por[:4]) if por[:4].isdigit() else None


def _similarity_enabled(config: Optional[dict]) -> bool:
    """Lazy-Prices similarity ships ON. Note this is the one research key whose
    ABSENT block is not a no-op — a producer nobody switches on is exactly how
    this signal sat dead with a fully-built consumer (see TODO.md §2a)."""
    block = ((config or {}).get("research") or {}).get("text_similarity") or {}
    return bool(block.get("enabled", True))


def _prior_year_sections(ticker: str, company_factory=None, filings=None) -> tuple[str, str]:
    """(risk_factors, mda) from the prior fiscal year's 10-K — the diff baseline
    AND the Lazy-Prices baseline, taken from ONE already-parsed filing object so
    the similarity costs no extra network request. Excludes 10-K/A amendments and
    selects by fiscal year (not 'second most recent'). ("", "") if there is no
    genuinely-prior annual report. Never raises.

    `company_factory` exists ONLY so tests can inject a fake without patching
    `sys.modules`; production always takes the lazy `edgar` import below (the
    [edgar] extra is optional, so it must not be imported at module scope).

    `filings` lets a caller that has ALREADY fetched the 10-K filings index (e.g.
    `fetch_bundle`, via `_fetch_10k_parsed`'s 4th return value) hand it in directly
    so this function costs no second network request. None (the default) preserves
    the original fetch-it-here behavior.

    BEHAVIOUR CHANGE vs `_prior_year_risk_factors`: the `edgar` import now sits
    INSIDE the try, so a missing [edgar] extra degrades to ("", "") + a stderr
    line instead of raising ImportError. That is unreachable in practice —
    `_fetch_10k_parsed` runs first in `fetch_bundle` and imports `edgar` at its
    top — and
    it matches this function's documented never-raises contract."""
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

    Compares the current same-`form` filing against the **immediately-prior**
    same-type filing, restricted to those whose acceptance (filing) date is
    `<= as_of` (an ISO 'YYYY-MM-DD' string; None == "now", live-screen path).
    This is the look-ahead guard: in any backtest/replay the comparison NEVER
    uses a filing dated after as_of, so a historical date is never compared
    against a future (e.g. 2026) filing.

    Returns a dict {similarity, current_accession, prior_accession,
    current_date, prior_date} or None when there is no usable
    current-and-prior pair (fewer than two filings at-or-before as_of, or no
    extractable text). Never raises. Requires SEC_IDENTITY (the [edgar] extra).
    """
    from edgar import Company  # lazy: optional [edgar] extra

    require_identity(identity)
    try:
        filings = Company(ticker).get_filings(form=form)
        # Exact-form only (drop /A amendments and adjacent forms), then restrict to
        # filings available AT as_of (filing_date <= as_of). This is the PiT cut.
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
        # Never-raises contract: the similarity abstains to None — but say why
        # on stderr so a systematic failure doesn't hide as "no filing pair".
        log_abstain("filing_text_change failed", ticker, e)
        return None


def _fetch_10k_parsed(
        ticker: str,
        identity: Optional[str] = None) -> tuple[Optional[FilingText], Any, Any, Any]:
    """(FilingText|None, parsed filing object|None, filing|None, filings index) — the
    shared core of fetch_10k.

    Split out so `fetch_bundle` can read the statement notes off the SAME parsed
    object the narrative sections came from. Re-deriving it would mean a second
    `.obj()` parse of a multi-hundred-KB document per brief, for data already in
    hand.

    The third element is the FILING, not the parsed object: `controls.detect` needs
    the whole document's text and the parsed `TenK` exposes no text accessor. Item
    9A alone is not a substitute — `part_ii_item_9a` returned 0 chars for 3 of 15
    filers measured and missed HP's adverse conclusion outright.

    The 4th element is the raw 10-K filings index (`Company(ticker).get_filings(
    form="10-K")`), returned so `fetch_bundle` can hand it to `_prior_year_sections`
    instead of that function re-fetching the same index from scratch — the fetch
    itself, not just the `.obj()` parse, used to happen twice per brief."""
    from edgar import Company  # lazy: optional [edgar] extra

    require_identity(identity)
    filings = Company(ticker).get_filings(form="10-K")
    latest = filings.latest(1)
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

    COSTS ~2 EXTRA SEC REQUESTS per brief (the filing index page + the document),
    measured 2026-08-23 — `filing.text()` re-downloads rather than reusing what
    `.obj()` parsed, and the parsed `TenK` exposes no text accessor. Bounded by the
    same process-wide throttle as every other sec.gov call. The cheaper section-only
    route was measured and rejected: `FilingText.combined()` alone fires on 2 of 7
    known positives, and adding `part_ii_item_9a` still returns 0 chars for 3 of 15
    filers. Skipped entirely when the block is disabled, so `enabled: false` costs
    nothing at all."""
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

    # sets identity / raises if SEC_IDENTITY unset. Keeps the parsed object so the
    # statement notes below cost no second parse.
    tenk, tenk_obj, tenk_filing, tenk_filings = _fetch_10k_parsed(ticker, identity)
    if tenk is None:
        return None

    # Debt & liquidity notes. Imported here, not at module scope: `notes` imports
    # log_abstain back out of this module, matching the `eightk` cycle break.
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
            # KEEP LAST, and keep `tenq_acc` immediately above it. The `except`
            # below resets the three 10-Q strings but NOT `debt_notes`, so this only
            # stays correct because nothing can raise between the accession being
            # assigned and the notes being appended. Insert a statement after this
            # line and 10-Q notes can survive with a stale accession.
            debt_notes += debtnotes.collect(qobj, "10-Q", tenq_acc, ticker, ncfg)
    except Exception:
        tenq_mda, tenq_acc, tenq_added = "", "", ""

    prior_1a, prior_mda = _prior_year_sections(ticker, filings=tenk_filings)
    added = riskdiff.added_risk_blocks(tenk.risk_factors, prior_1a, config or {})
    # Lazy-Prices YoY similarity from documents ALREADY in hand — no extra fetch.
    similarity = None
    if _similarity_enabled(config):
        similarity = textsim.combined_similarity(
            tenk.risk_factors, prior_1a, tenk.mda, prior_mda)

    # Recent 8-K substance. Imported here, not at module scope: `eightk` imports
    # log_abstain back out of this module, and its own contract is never-raises, so
    # a dead SEC endpoint costs a bare label rather than the whole brief.
    from . import eightk
    eightks = eightk.fetch_eightks(ticker, config)

    cache_key = f"{tenk.accession}+{tenq_acc}" if tenq_acc else tenk.accession
    # 8-K accessions belong in the FILING half of the key: selection is deliberately
    # independent of `filing_events` (that is the F1 fix), so `context_digest`'s event
    # tuple cannot see an 8-K outside EdgarSource's 40-row index — exactly the JPM case
    # the design was built around. Without this, a fresh earnings release only busts the
    # brief via the `max_age_days` day bucket, i.e. up to 24h late. Sorted for
    # determinism; empty list appends nothing, so a name with no qualifying 8-K keeps a
    # byte-identical key.
    for acc in sorted(e.accession for e in eightks if e.accession):
        cache_key += f"+{acc}"
    return FilingBundle(
        tenk=tenk, primary_accession=tenk.accession, cache_key=cache_key,
        filing_date=tenk.filing_date, tenq_mda=tenq_mda, added_risks_text=added,
        text_similarity=similarity, eightks=eightks, tenq_added_risks=tenq_added,
        tenq_accession=tenq_acc, debt_notes=debt_notes, controls=controls)
