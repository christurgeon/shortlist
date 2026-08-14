from __future__ import annotations

import dataclasses
import os
import sys
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


def _tenq_mda(tenq: Any) -> str:
    """10-Q MD&A is Part I, Item 2 — NOT a `management_discussion` attribute (that
    exists only on TenK). Returns "" on any extraction failure."""
    try:
        getter = getattr(tenq, "get_item_with_part", None)
        if getter is None:
            return ""
        # markdown=True is passed for the legacy-fallback path only; on the current
        # parser path get_item_with_part returns Section.text() and ignores it.
        value = getter("Part I", "Item 2", markdown=True)
        return str(value) if value else ""
    except Exception:
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


def _prior_year_sections(ticker: str, company_factory=None) -> tuple[str, str]:
    """(risk_factors, mda) from the prior fiscal year's 10-K — the diff baseline
    AND the Lazy-Prices baseline, taken from ONE already-parsed filing object so
    the similarity costs no extra network request. Excludes 10-K/A amendments and
    selects by fiscal year (not 'second most recent'). ("", "") if there is no
    genuinely-prior annual report. Never raises.

    `company_factory` exists ONLY so tests can inject a fake without patching
    `sys.modules`; production always takes the lazy `edgar` import below (the
    [edgar] extra is optional, so it must not be imported at module scope).

    BEHAVIOUR CHANGE vs `_prior_year_risk_factors`: the `edgar` import now sits
    INSIDE the try, so a missing [edgar] extra degrades to ("", "") + a stderr
    line instead of raising ImportError. That is unreachable in practice —
    `fetch_10k` runs first in `fetch_bundle` and imports `edgar` at its top — and
    it matches this function's documented never-raises contract."""
    try:
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


def _filing_sections(obj: Any, form: str) -> tuple[str, str]:
    """(risk_factors, mda) text for a parsed filing object. 10-K MD&A is the
    `management_discussion` attribute; 10-Q MD&A is Part I Item 2 (see _tenq_mda).
    Item 1A (risk_factors) exists on both. Returns "" for any missing section."""
    risk = _section(obj, "risk_factors")
    mda = _tenq_mda(obj) if str(form).upper().startswith("10-Q") else _section(
        obj, "management_discussion")
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
        cur_risk, cur_mda = _filing_sections(current.obj(), form)
        pri_risk, pri_mda = _filing_sections(prior.obj(), form)
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


def fetch_10k(ticker: str, identity: Optional[str] = None) -> Optional[FilingText]:
    """Fetch the latest 10-K narrative for `ticker` via edgartools.
    Returns None if there is no usable 10-K (e.g. foreign filers file 20-F) or
    all narrative sections are empty. Raises RuntimeError if SEC_IDENTITY is unset.
    """
    from edgar import Company  # lazy: optional [edgar] extra

    require_identity(identity)
    latest = Company(ticker).get_filings(form="10-K").latest(1)
    if latest is None:
        return None
    tenk = latest.obj()
    filing = _build_filing_text(
        ticker, getattr(latest, "accession_no", ""), getattr(latest, "filing_date", ""), tenk)
    return filing if filing.has_content() else None


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


def fetch_bundle(ticker: str, identity: Optional[str] = None,
                 config: Optional[dict] = None) -> Optional[FilingBundle]:
    """Fetch the documents for one research brief: the current 10-K (required), the
    latest 10-Q MD&A, and the YoY added-risk diff. Returns None ONLY when the
    current 10-K is unusable (matches fetch_10k's contract); the 10-Q and diff
    degrade to "" on any failure (failure isolation)."""
    from edgar import Company  # lazy: optional [edgar] extra

    tenk = fetch_10k(ticker, identity)   # sets identity / raises if SEC_IDENTITY unset
    if tenk is None:
        return None

    tenq_mda, tenq_acc, tenq_added = "", "", ""
    try:
        latest_q = Company(ticker).get_filings(form="10-Q").latest(1)
        if latest_q is not None and str(getattr(latest_q, "form", "")) == "10-Q":
            # ONE parse feeds both Part I Item 2 and Part II Item 1A.
            qobj = latest_q.obj()
            tenq_mda = _tenq_mda(qobj)
            tenq_added = _tenq_added_risks(qobj, tenk.risk_factors, config)
            tenq_acc = str(getattr(latest_q, "accession_no", "") or "")
    except Exception:
        tenq_mda, tenq_acc, tenq_added = "", "", ""

    prior_1a, prior_mda = _prior_year_sections(ticker)
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
        text_similarity=similarity, eightks=eightks, tenq_added_risks=tenq_added)
