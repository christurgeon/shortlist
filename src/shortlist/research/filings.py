from __future__ import annotations

import dataclasses
import os
from typing import Any, Optional

from . import riskdiff
from .models import FilingBundle, FilingText


def cap_sections(filing: FilingText, max_chars: dict | None) -> FilingText:
    """Prefix-trim each 10-K narrative section to its configured char cap, so the
    model prompt and the grounding haystack (filing.combined()) read identical text.
    Absent/None caps => filing returned unchanged (byte-identical). 10-K risk factors
    are ordered worst-first, so a prefix slice keeps the material content."""
    if not max_chars:
        return filing

    def _cap(s: str, n) -> str:
        return s if not n or len(s) <= n else s[:n]

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
    tenq_cap = max_chars.get("tenq_mda")
    tenq = bundle.tenq_mda
    if tenq_cap and len(tenq) > tenq_cap:
        tenq = tenq[:tenq_cap]
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
        value = getter("Part I", "Item 2", markdown=True)  # markdown=True for 10-K parity
        return str(value) if value else ""
    except Exception:
        return ""


def _fiscal_year(filing: Any) -> Optional[int]:
    """Best-effort fiscal year from a filing's period_of_report (YYYY-MM-DD)."""
    por = str(getattr(filing, "period_of_report", "") or "")
    return int(por[:4]) if por[:4].isdigit() else None


def _prior_year_risk_factors(ticker: str) -> str:
    """The prior fiscal year's 10-K Item 1A, for the YoY diff baseline. Excludes
    10-K/A amendments and selects by fiscal year (not 'second most recent'). "" if
    there is no genuinely-prior annual report. Never raises."""
    from edgar import Company
    try:
        filings = Company(ticker).get_filings(form="10-K")
        rows = [f for f in filings if str(getattr(f, "form", "")) == "10-K"]
        if len(rows) < 2:
            return ""
        rows.sort(key=lambda f: str(getattr(f, "filing_date", "")), reverse=True)
        current_fy = _fiscal_year(rows[0])
        for f in rows[1:]:
            fy = _fiscal_year(f)
            if current_fy is None or fy is None or fy < current_fy:
                tenk = f.obj()
                return _section(tenk, "risk_factors")
        return ""
    except Exception:
        return ""


def fetch_10k(ticker: str, identity: Optional[str] = None) -> Optional[FilingText]:
    """Fetch the latest 10-K narrative for `ticker` via edgartools.
    Returns None if there is no usable 10-K (e.g. foreign filers file 20-F) or
    all narrative sections are empty. Raises RuntimeError if SEC_IDENTITY is unset.
    """
    from edgar import Company, set_identity  # lazy: optional [edgar] extra

    ident = identity or os.environ.get("SEC_IDENTITY")
    if not ident:
        raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
    set_identity(ident)  # process-global; safe to set once per fetch here

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

    tenq_mda, tenq_acc = "", ""
    try:
        latest_q = Company(ticker).get_filings(form="10-Q").latest(1)
        if latest_q is not None and str(getattr(latest_q, "form", "")) == "10-Q":
            tenq_mda = _tenq_mda(latest_q.obj())
            tenq_acc = str(getattr(latest_q, "accession_no", "") or "")
    except Exception:
        tenq_mda, tenq_acc = "", ""

    prior_1a = _prior_year_risk_factors(ticker)
    added = riskdiff.added_risk_blocks(tenk.risk_factors, prior_1a, config or {})

    cache_key = f"{tenk.accession}+{tenq_acc}" if tenq_acc else tenk.accession
    return FilingBundle(
        tenk=tenk, primary_accession=tenk.accession, cache_key=cache_key,
        filing_date=tenk.filing_date, tenq_mda=tenq_mda, added_risks_text=added)
