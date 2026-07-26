"""SEC DERA bulk Form 3/4/5 ingest -> InsiderTxn records + a per-insider trade-month index.

Quarterly ZIPs (~12.8 MB each) at sec.gov/files/structureddata/data/insider-transactions-
data-sets/. Publication lags a quarter, so this is the HISTORY side only; live detection
reads Form 4 XML (scout/insider.py). Both produce the same InsiderTxn from RAW fields.

Design: docs/FORM4_INSIDER.md
"""
from __future__ import annotations

import csv
from datetime import date

from .insider import InsiderTxn

_BASE = ("https://www.sec.gov/files/structureddata/data/"
         "insider-transactions-data-sets")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

_TRUE = {"1", "true", "yes", "y"}


def dera_zip_url(quarter: str) -> str:
    """'2025q1' -> the quarterly Form 345 ZIP URL."""
    return f"{_BASE}/{quarter}_form345.zip"


def parse_dera_date(raw: str | None) -> date | None:
    """DERA dates are DD-MON-YYYY ('27-MAR-2025'), NOT ISO. None-safe."""
    s = (raw or "").strip().upper()
    parts = s.split("-")
    if len(parts) != 3 or parts[1] not in _MONTHS:
        return None
    try:
        return date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
    except ValueError:
        return None


def _roles(raw: str | None) -> frozenset[str]:
    """'Director,Officer,TenPercentOwner' -> {'director','officer','tenpercent'}."""
    out = set()
    for part in (raw or "").split(","):
        p = part.strip().lower()
        if p == "director":
            out.add("director")
        elif p == "officer":
            out.add("officer")
        elif p in ("tenpercentowner", "tenpercent"):
            out.add("tenpercent")
    return frozenset(out)


def _num(raw: str | None) -> float | None:
    try:
        return float(raw) if (raw or "").strip() else None
    except ValueError:
        return None


def parse_dera_tsvs(sub_fh, owner_fh, trans_fh) -> list[InsiderTxn]:
    """The three DERA TSVs -> InsiderTxn records, matching parse_form4_xml exactly."""
    subs = {r["ACCESSION_NUMBER"]: r for r in csv.DictReader(sub_fh, delimiter="\t")
            if r.get("DOCUMENT_TYPE") == "4"}
    owners: dict[str, list[dict]] = {}
    for r in csv.DictReader(owner_fh, delimiter="\t"):
        owners.setdefault(r["ACCESSION_NUMBER"], []).append(r)

    out: list[InsiderTxn] = []
    for r in csv.DictReader(trans_fh, delimiter="\t"):
        s = subs.get(r["ACCESSION_NUMBER"])
        if not s:
            continue
        d = parse_dera_date(r.get("TRANS_DATE"))
        if d is None:
            continue
        os_ = owners.get(r["ACCESSION_NUMBER"], [])
        o = os_[0] if os_ else {}
        title = (o.get("RPTOWNER_TITLE") or "").strip() or None
        out.append(InsiderTxn(
            owner_cik=(o.get("RPTOWNERCIK") or "").strip(),
            ticker=(s.get("ISSUERTRADINGSYMBOL") or "").strip().upper(),
            date=d,
            code=(r.get("TRANS_CODE") or "").strip(),
            shares=_num(r.get("TRANS_SHARES")),
            price=_num(r.get("TRANS_PRICEPERSHARE")),
            plan_10b5_1=str(s.get("AFF10B5ONE") or "").strip().lower() in _TRUE,
            roles=_roles(o.get("RPTOWNER_RELATIONSHIP")),
            title=title,
            # >1 reporting owner: neither source joins a transaction to a PARTICULAR
            # owner, so any single attribution is a guess. Abstain (spec §5.1).
            joint_filing=len(os_) > 1,
        ))
    return out
