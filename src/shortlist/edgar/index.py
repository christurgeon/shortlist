"""EDGAR daily-index scanners: Form 4 submission fetch, initial 13D and 13D/A discovery.

The daily index lists ~800-1,500 rows/day (CIK + accession only), so each filing needs one
further request for its submission text. Live fetching is bounded by a per-day cap; the pure
aggregation stays testable in isolation.

Mostly uncalled by `src/`, deliberately kept. Only `_dedup_by_accession` (via
`backtest/edgar_history.py`) and `_walk_back_to_published` have production importers.
The fetchers and `activist_stakes_from_records` are reachable only from tests: their
caller was retired with the scout (`docs/audits/2026-08-11-scout-retirement.md`).

That audit stopped the repo acting on 13D/Form-4 signals; it did not disprove the
collectors. They parse SEC's daily index, an external format that drifts, and their tests
show whether the parsing still holds. Do not delete this module without a decision to
abandon 13D/Form-4 ingestion.
"""
from __future__ import annotations

import warnings
from collections import defaultdict
from datetime import date, timedelta
from typing import Callable

from ..env import redact_secrets
from .calendar import is_trading_day
from .models import Emission
from .quality import is_affiliate_filing, is_initial_13d, is_spac_or_shell, marquee_activist
from .sec_throttle import sec_throttle

# Tokens edgartools emits for an unresolved issuer ticker. Unfiltered, they bucket into a
# phantom "NONE" issuer that looks like a multi-insider cluster buy.
_NON_TICKERS = {"", "NONE", "NA", "N/A", "NULL"}


def _is_real_ticker(raw: str | None) -> str:
    """Normalize and validate an issuer ticker; return "" if it's a placeholder.

    Rejects `str(None).upper() -> "NONE"`, punctuation-only forms and CIK-as-ticker.
    A real symbol has at least one letter (BRK.B, AXIA3).
    """
    t = (raw or "").strip().upper()
    if not t or t in _NON_TICKERS or not any(c.isalpha() for c in t):
        return ""
    return t


def _walk_back_to_published(session: date, lookback: int,
                            fetch_day: Callable[[date], list[dict]]) -> tuple[list[dict], date]:
    """Walk-back for every "most-recent published SEC daily index" scanner. The index
    for `session` is not published until ~02:00 UTC, so an empty result means "not
    published yet", not "no activity". Calls `fetch_day` for `session`, then walks back
    up to `lookback` trading days until it returns rows. Returns (records, day_used);
    ([], session) if the window is empty. Never raises (`fetch_day`'s contract)."""
    d = session
    for _ in range(lookback + 1):
        recs = fetch_day(d)
        if recs:
            return recs, d
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return [], session


def fetch_form4_submissions(session: date, max_filings: int, identity: str,
                            _throttle=None) -> tuple[list[str], date | None]:
    """Form 4 complete-submission texts for `session` (walk-back to the last published
    index, see _walk_back_to_published).

    One request per filing: `Filing.full_text_submission` downloads the complete-submission
    `.txt` (the ownershipDocument XML embedded) from its deterministic URL, with no
    index.json lookup because the primary document filename is arbitrary.

    Not `Filing.text()`: for XML-native forms it renders the parsed object back to HTML
    and flattens that to prose, which can cost a second request and drops the raw
    `<ownershipDocument>` tags parse_form4_xml scans for.

    Never raises. Two distinct empty outcomes:
    - Walk-back exhaustion (no index published in the last `lookback` days) ->
      `([], session)`, `used == session`. Not an error.
    - A real failure (edgartools missing, SEC outage, identity error) -> `([], None)`.
      `used is None` keeps an outage from reading like a quiet day.
    """
    try:
        from edgar import get_filings, set_identity
        set_identity(identity)
        filings, used = _walk_back_to_published(
            session, lookback=5,
            fetch_day=lambda d: list(get_filings(form="4", filing_date=d.isoformat())))
        candidates = _dedup_by_accession(filings)[:max_filings]
        out: list[str] = []
        throttle = _throttle or sec_throttle()
        for f in candidates:
            try:
                throttle("edgar_form4")   # heaviest SEC consumer (docs/audits/2026-08-05-discovery-funnel-audit.md §4)
                out.append(f.full_text_submission())
            except Exception:  # noqa: BLE001 -- skip one bad filing
                continue
        # len(candidates) is the pre-error count, which shows whether the cap bound;
        # len(out) cannot once any filing errors.
        return out, used, len(candidates)
    except Exception as exc:  # noqa: BLE001 -- edgartools missing or SEC error -> degrade
        warnings.warn(f"edgar: form4 submission fetch failed: {redact_secrets(str(exc))}",
                      stacklevel=2)
        return [], None, 0


# --- Activist SCHEDULE 13D discovery ---
# The raw 13D firehose is noise-dominated (SPAC shells, foreign holdcos, affiliate filings),
# so quality.py filters it.


def _dedup_by_accession(filings):
    """edgartools get_filings returns every row twice. Keep the first occurrence per
    accession so co-filer counts and header fetches are not doubled."""
    seen: set = set()
    out = []
    for f in filings:
        acc = getattr(f, "accession_no", None) or getattr(f, "accession_number", None)
        if acc in seen:
            continue
        seen.add(acc)
        out.append(f)
    return out


def activist_stakes_from_records(records, *, drop_spacs=True, drop_affiliates=True,
                                 marquee_boost=0.2, stake_by_accession=None):
    """Pure aggregation: records -> one Emission per resolved ticker (initial 13D only).

    record: {ticker, cik, subject_name, activist, form, accession}. Placeholder tickers
    (unresolved subjects) are skipped so they can't form a phantom candidate. Dedup is on
    the resolved TICKER (co-filers on the same target collapse to one emission). SPAC/shell
    subjects and affiliate filings (filer name echoes the subject) are dropped by config;
    a marquee (credible) activist boosts strength. The subject CIK is carried on the
    Emission so the selection ledger can re-resolve a renamed ticker later.

    When stake_by_accession is provided (a dict mapping accession -> stake %), the first
    row's accession that exists in the dict gains meta={"stake_pct": <value>}."""
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        tkr = _is_real_ticker(r.get("ticker"))
        if not tkr or not is_initial_13d(r.get("form", "")):
            continue
        subj, act = r.get("subject_name", "") or "", r.get("activist", "") or ""
        if drop_spacs and is_spac_or_shell(subj):
            continue
        if drop_affiliates and is_affiliate_filing(act, subj):
            continue
        by_ticker[tkr].append(r)

    out: list[Emission] = []
    for tkr, rows in sorted(by_ticker.items()):
        subject = rows[0].get("subject_name", "") or tkr
        activists = sorted({r.get("activist", "") or "" for r in rows})
        n = len(activists)
        marquee = next((marquee_activist(a) for a in activists if marquee_activist(a)), None)
        strength = min(1.0, 0.7 + (marquee_boost if marquee else 0.0)
                       + min(0.1, 0.05 * (n - 1)))
        # fall back to the subject when no filer name parsed (a deliberately-kept valid 13D)
        who = marquee or next((a for a in activists if a), None) or subject
        who_part = who if n == 1 else f"{n} filers incl. {who}"
        ev = f"Activist 13D: {who_part} → {subject}"

        meta = {}
        if stake_by_accession:
            for r in rows:
                pct = stake_by_accession.get(r.get("accession"))
                if pct is not None:
                    meta = {"stake_pct": pct}
                    break

        out.append(Emission(tkr, "edgar:activist_13d", strength, ev, is_discovery=True,
                            cik=rows[0].get("cik"), meta=meta))
    return out


def _get_13d_index_rows(session: date, identity: str) -> list:
    """Raw 13D-family daily-index rows (both form spellings; prefix match includes /A).
    Isolated so tests can fake it; raises to the caller's guard on SEC failure."""
    from edgar import get_filings, set_identity
    set_identity(identity)
    rows = []
    for form in ("SCHEDULE 13D", "SC 13D"):
        try:
            rows.extend(list(get_filings(form=form, filing_date=session.isoformat())))
        except Exception:  # noqa: BLE001 — one form spelling failing must not kill the other
            continue
    return rows


def fetch_activist_records(session: date, max_filings: int, identity: str,
                           resolve_ticker_fn, _throttle=None) -> list[dict]:
    """Live: pull the SCHEDULE 13D (+ legacy SC 13D) daily index for `session`, dedup the
    doubled rows, parse each header into a record. `resolve_ticker_fn(cik)->ticker|None`
    maps the SUBJECT company's CIK to its ticker. Never raises (degrades to [])."""
    try:
        rows = _get_13d_index_rows(session, identity)
        records: list[dict] = []
        throttle = _throttle or sec_throttle()
        for f in _dedup_by_accession(rows)[:max_filings]:
            try:
                if not is_initial_13d(getattr(f, "form", "")):
                    continue  # exclude /A amendments (prefix match returns them)
                throttle("edgar_activist_13d")   # `.header` is a network fetch
                hdr = f.header
                subs = getattr(hdr, "subject_companies", None)
                if not subs:
                    continue  # malformed/empty header -> skip this row, never abort the batch
                ci = subs[0].company_information
                cik = getattr(ci, "cik", None)
                if not cik:
                    continue
                tkr = resolve_ticker_fn(cik)
                if not tkr:
                    continue  # unresolved (foreign issuer absent from company_tickers.json)
                try:
                    filers = getattr(hdr, "filers", None)
                    activist = (filers[0].company_information.name if filers else "") or ""
                except Exception:  # noqa: BLE001 — a bad FILER name must not drop a valid subject
                    activist = ""
                acc = getattr(f, "accession_no", None) or getattr(f, "accession_number", None)
                records.append({
                    "ticker": tkr, "cik": f"{int(cik):010d}",
                    "subject_name": getattr(ci, "name", "") or "",
                    "activist": activist, "form": getattr(f, "form", ""),
                    "accession": acc, "_filing": f})
            except Exception:  # noqa: BLE001 — skip an unparseable filing
                continue
        return records
    except Exception as exc:  # noqa: BLE001 — edgartools missing / SEC error -> degrade
        # Loud on purpose: an outage must not read as "0 filings today".
        warnings.warn(f"edgar: index fetch failed: {redact_secrets(str(exc))}", stacklevel=2)
        return []


def fetch_recent_activist_records(session: date, max_filings: int, identity: str,
                                  resolve_ticker_fn, lookback: int = 4,
                                  _fetch=None) -> tuple[list[dict], date]:
    """Most-recent *published* SCHEDULE 13D index at or before `session`. See
    _walk_back_to_published for the fallback rationale. Returns (records, session_used).
    Never raises."""
    fetch = _fetch or fetch_activist_records
    return _walk_back_to_published(
        session, lookback, lambda d: fetch(d, max_filings, identity, resolve_ticker_fn))


def fetch_amendment_records(session: date, max_filings: int, identity: str,
                            resolve_ticker_fn) -> list[dict]:
    """Live: SCHEDULE 13D/A (+ legacy SC 13D/A) records for `session`. Same contract as
    fetch_activist_records but keeps ONLY amendments and also parses the FILER CIK (the
    stake-baseline pair key needs both sides). Never raises (degrades to [])."""
    from .quality import is_13d_amendment
    try:
        rows = _get_13d_index_rows(session, identity)
        records: list[dict] = []
        for f in _dedup_by_accession(rows)[:max_filings]:
            try:
                if not is_13d_amendment(getattr(f, "form", "")):
                    continue
                hdr = f.header
                subs = getattr(hdr, "subject_companies", None)
                if not subs:
                    continue
                ci = subs[0].company_information
                cik = getattr(ci, "cik", None)
                if not cik:
                    continue
                tkr = resolve_ticker_fn(cik)
                if not tkr:
                    continue
                filer_cik = activist = None
                try:
                    filers = getattr(hdr, "filers", None)
                    if filers:
                        fci = filers[0].company_information
                        activist = getattr(fci, "name", "") or ""
                        raw_fc = getattr(fci, "cik", None)
                        filer_cik = f"{int(raw_fc):010d}" if raw_fc else None
                except Exception:  # noqa: BLE001 — a bad FILER block must not drop the subject
                    pass
                acc = getattr(f, "accession_no", None) or getattr(f, "accession_number", None)
                records.append({
                    "ticker": tkr, "cik": f"{int(cik):010d}", "filer_cik": filer_cik,
                    "subject_name": getattr(ci, "name", "") or "",
                    "activist": activist or "", "form": getattr(f, "form", ""),
                    "accession": acc,
                    "file_date": str(getattr(f, "filing_date", "") or ""),
                    "_filing": f})            # carried so the signal can doc-fetch stake
            except Exception:  # noqa: BLE001 — skip an unparseable filing
                continue
        return records
    except Exception as exc:  # noqa: BLE001 — edgartools missing / SEC error -> degrade
        warnings.warn(f"edgar: 13D/A index fetch failed: {redact_secrets(str(exc))}",
                      stacklevel=2)
        return []


def fetch_recent_amendment_records(session: date, max_filings: int, identity: str,
                                   resolve_ticker_fn, lookback: int = 4,
                                   _fetch=None) -> tuple[list[dict], date]:
    """Walk-back twin of fetch_recent_activist_records for the /A stream."""
    fetch = _fetch or fetch_amendment_records
    return _walk_back_to_published(
        session, lookback, lambda d: fetch(d, max_filings, identity, resolve_ticker_fn))
