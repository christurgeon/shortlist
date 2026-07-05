"""Historical 13D walker for the Phase-2 backfill (spec §8).

One ranged edgartools get_filings call per form string over the whole window (verified live:
filing_date accepts "YYYY-MM-DD:YYYY-MM-DD"), dedup (edgartools returns rows twice), initial-13D
filter, then ONE rate-limited header fetch per surviving filing for the SUBJECT company CIK/name
(the filer is the activist; the subject is the target). A header failure keeps the record with
cik=None — downstream must count it as selected + non-measurable, never silently drop it.
Never raises: index failure -> None (warned); empty -> [].
"""
from __future__ import annotations

import time
import warnings
from datetime import date
from typing import Optional

from ..env import redact_secrets
from ..scout.edgar_index import _dedup_by_accession
from ..scout.quality import is_initial_13d

_FORMS = ("SCHEDULE 13D", "SC 13D")


def _norm_cik10(raw) -> Optional[str]:
    try:
        return f"{int(str(raw).strip()):010d}"
    except (TypeError, ValueError):
        return None


def fetch_activist_window(start: date, end: date, identity: str, *,
                          throttle_s: float = 0.2, max_records: Optional[int] = None,
                          _get_filings=None) -> Optional[list[dict]]:
    """All initial 13D records filed in [start, end]. None = index fetch failed; [] = none."""
    if _get_filings is None:
        try:
            from edgar import get_filings, set_identity  # edgartools (optional dep, lazy)
            set_identity(identity)
            _get_filings = get_filings
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"edgar_history: edgartools unavailable: {redact_secrets(str(exc))}",
                          stacklevel=2)
            return None
    rng = f"{start.isoformat()}:{end.isoformat()}"
    rows = []
    try:
        for form in _FORMS:
            rows.extend(list(_get_filings(form=form, filing_date=rng)))
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"edgar_history: index fetch failed for {rng}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None
    out: list[dict] = []
    for f in _dedup_by_accession(rows):
        if not is_initial_13d(getattr(f, "form", "") or ""):
            continue
        if max_records is not None and len(out) >= max_records:
            warnings.warn(f"edgar_history: max_records={max_records} hit for {rng} — "
                          "window truncated, narrow the range", stacklevel=2)
            break
        fd = getattr(f, "filing_date", None)
        if isinstance(fd, str):
            try:
                fd = date.fromisoformat(fd[:10])
            except ValueError:
                fd = None
        if not isinstance(fd, date):
            continue                              # unusable row (no date to key on)
        acc = getattr(f, "accession_no", None) or getattr(f, "accession_number", None)
        cik = subject = activist = None
        try:
            if throttle_s > 0:
                time.sleep(throttle_s)            # SEC fair-access: bound the header-fetch rate
            hdr = f.header
            subs = getattr(hdr, "subject_companies", None)
            if subs:
                ci = subs[0].company_information
                cik = _norm_cik10(getattr(ci, "cik", None))
                subject = getattr(ci, "name", None)
            filers = getattr(hdr, "filers", None)
            if filers:
                activist = getattr(filers[0].company_information, "name", None)
        except Exception:  # noqa: BLE001 — keep the record; downstream counts it non-measurable
            pass
        out.append({"cik": cik, "subject_name": subject, "activist": activist,
                    "form": str(getattr(f, "form", "")), "accession": acc, "filing_date": fd})
    return out


def group_by_day(records: list[dict]) -> dict[date, list[dict]]:
    out: dict[date, list[dict]] = {}
    for r in records:
        out.setdefault(r["filing_date"], []).append(r)
    return dict(sorted(out.items()))
