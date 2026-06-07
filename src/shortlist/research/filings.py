from __future__ import annotations

import dataclasses
import os
from typing import Any, Optional

from .models import FilingText


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
