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
from ..scout.quality import is_13d_amendment, is_affiliate_filing, is_initial_13d, is_spac_or_shell

_FORMS = ("SCHEDULE 13D", "SC 13D")
_AMENDMENT_FORMS = ("SCHEDULE 13D", "SC 13D", "SCHEDULE 13D/A", "SC 13D/A")


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
    n_poisoned = 0
    last_exc: Optional[str] = None
    for f in _dedup_by_accession(rows):
        # Everything below reads attributes off an untrusted edgartools filing object. A
        # poisoned row (e.g. a property that raises something other than AttributeError)
        # must not abort the whole window -- that's a deterministic wedge: a re-run over
        # the same range hits the same poisoned row and dies again. This outer guard is
        # last-resort: it wraps ONLY the basic attribute reads/loop control, never the
        # header try/except below, whose own semantics (failure -> cik=None record, not a
        # skip) are unchanged.
        try:
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
                    time.sleep(throttle_s)        # SEC fair-access: bound the header-fetch rate
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
        except Exception as exc:  # noqa: BLE001 — one poisoned row must not wedge the batch
            n_poisoned += 1
            last_exc = redact_secrets(str(exc))
            continue
    if n_poisoned:
        warnings.warn(f"edgar_history: skipped {n_poisoned} unreadable filing row(s) for "
                      f"{rng} (last error: {last_exc})", stacklevel=2)
    return out


def fetch_amendment_window(start: date, end: date, identity: str, *,
                           throttle_s: float = 0.2, max_records: Optional[int] = None,
                           _get_filings=None, _stake_fn=None) -> Optional[list[dict]]:
    """Both initial SCHEDULE 13D and SCHEDULE 13D/A amendment records filed in [start, end]
    (the escalation-pack backfill walker, spec §8) -- initials seed the pair baseline the
    assembler diffs amendments against. Each kept row also carries a doc-fetched `stake_pct`
    (percent-of-class off the cover page) and the FILER's CIK (`filer_cik`, distinct from the
    SUBJECT `cik` -- `stake.pair_key` needs both). None = index fetch failed; [] = none.

    Mirrors fetch_activist_window's poisoned-row guard and header-failure semantics (a
    header failure keeps the record with cik=None/filer_cik=None rather than dropping the
    row -- downstream, the assembler's pair_key() abstains on the missing CIK, which is a
    scoped design choice for THIS signal: see _assemble_13d_a_factory).

    Controller-resolution guard (Task 8 review finding): the per-row stake DOC-fetch is
    SKIPPED (stake_pct stays None) for rows the assembler excludes anyway --
    is_spac_or_shell(subject_name) or is_affiliate_filing(activist, subject_name), checked
    on the SAME header-derived fields the assembler re-checks -- so a multi-year walk never
    burns an SEC document fetch on a filing that can never be selected. Filter-before-fetch,
    per the design spec; the assembler's own drops are unchanged and still apply.

    `_stake_fn(filing) -> float | None` is the test seam (default
    `scout.stake.stake_pct_from_filing`). Emits one `warnings.warn` parse-rate line per
    window (never per row) once at least one doc was attempted.
    """
    if _get_filings is None:
        try:
            from edgar import get_filings, set_identity  # edgartools (optional dep, lazy)
            set_identity(identity)
            _get_filings = get_filings
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"edgar_history: edgartools unavailable: {redact_secrets(str(exc))}",
                          stacklevel=2)
            return None
    if _stake_fn is None:
        from ..scout.stake import stake_pct_from_filing
        _stake_fn = stake_pct_from_filing
    rng = f"{start.isoformat()}:{end.isoformat()}"
    rows = []
    try:
        for form in _AMENDMENT_FORMS:
            rows.extend(list(_get_filings(form=form, filing_date=rng)))
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"edgar_history: index fetch failed for {rng}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None
    out: list[dict] = []
    n_poisoned = 0
    last_exc: Optional[str] = None
    n_parsed = n_docs = 0
    for f in _dedup_by_accession(rows):
        # Same outer poisoned-row guard as fetch_activist_window: wraps only the basic
        # attribute reads/loop control, never the header try/except below (whose own
        # failure -> cik=None semantics are unchanged).
        try:
            form = getattr(f, "form", "") or ""
            if not (is_initial_13d(form) or is_13d_amendment(form)):
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
            cik = subject = activist = filer_cik = None
            try:
                if throttle_s > 0:
                    time.sleep(throttle_s)        # SEC fair-access: bound the header-fetch rate
                hdr = f.header
                subs = getattr(hdr, "subject_companies", None)
                if subs:
                    ci = subs[0].company_information
                    cik = _norm_cik10(getattr(ci, "cik", None))
                    subject = getattr(ci, "name", None)
                filers = getattr(hdr, "filers", None)
                if filers:
                    fci = filers[0].company_information
                    activist = getattr(fci, "name", None)
                    filer_cik = _norm_cik10(getattr(fci, "cik", None))
            except Exception:  # noqa: BLE001 — keep the record; downstream counts it non-measurable
                pass
            stake_pct = None
            if not (is_spac_or_shell(subject or "") or
                    is_affiliate_filing(activist or "", subject or "")):
                n_docs += 1
                try:
                    if throttle_s > 0:
                        time.sleep(throttle_s)    # a second, separate SEC fetch (the doc body)
                    stake_pct = _stake_fn(f)
                except Exception:  # noqa: BLE001 — one bad doc must not wedge the walk
                    stake_pct = None
                if stake_pct is not None:
                    n_parsed += 1
            out.append({"cik": cik, "filer_cik": filer_cik, "subject_name": subject,
                        "activist": activist, "form": str(form), "accession": acc,
                        "filing_date": fd, "stake_pct": stake_pct})
        except Exception as exc:  # noqa: BLE001 — one poisoned row must not wedge the batch
            n_poisoned += 1
            last_exc = redact_secrets(str(exc))
            continue
    if n_poisoned:
        warnings.warn(f"edgar_history: skipped {n_poisoned} unreadable filing row(s) for "
                      f"{rng} (last error: {last_exc})", stacklevel=2)
    if n_docs:
        warnings.warn(f"edgar_history: 13D/A stake parse rate {n_parsed}/{n_docs} "
                      f"for {rng}", stacklevel=2)
    return out


def group_by_day(records: list[dict]) -> dict[date, list[dict]]:
    out: dict[date, list[dict]] = {}
    for r in records:
        out.setdefault(r["filing_date"], []).append(r)
    return dict(sorted(out.items()))
