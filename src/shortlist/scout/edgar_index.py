"""SEC Form 4 daily-index scanner -> same-session insider cluster buys.

A NEW ingestion path (the per-ticker providers/_form4.py does not do this). The
daily index lists ~1,700 Form 4 rows (CIK + accession only); resolving cluster
buys means fetching+parsing each filing, classifying P/S, mapping CIK->ticker,
and grouping by issuer. Live fetching is bounded by a per-day cap and its own
concurrency budget; this module keeps the *pure* aggregation testable in isolation.
"""
from __future__ import annotations

import warnings
from collections import defaultdict
from datetime import date, timedelta

from ..env import redact_secrets
from ..providers._form4 import classify_code
from .calendar import is_trading_day
from .models import Emission
from .quality import is_affiliate_filing, is_initial_13d, is_spac_or_shell, marquee_activist

# Tokens edgartools emits when an issuer ticker can't be resolved (private funds,
# foreign filers). They are NOT real symbols — left unfiltered they bucket together
# into a phantom "NONE" issuer that looks like a multi-insider cluster buy.
_NON_TICKERS = {"", "NONE", "NA", "N/A", "NULL"}


def _is_real_ticker(raw: str | None) -> str:
    """Normalize and validate an issuer ticker; return "" if it's a placeholder.

    Catches the documented `str(None).upper() -> "NONE"` case plus whitespace, the
    em-dash/punctuation-only forms, and CIK-as-ticker (pure digits). A real symbol
    always contains at least one letter (e.g. BRK.B, AXIA3), so requiring an alpha
    char rejects numeric/punctuation junk without dropping any valid ticker.
    """
    t = (raw or "").strip().upper()
    if not t or t in _NON_TICKERS or not any(c.isalpha() for c in t):
        return ""
    return t


def cluster_buys_from_records(records: list[dict], min_buyers: int = 2) -> list[Emission]:
    """Pure aggregation: records -> cluster-buy Emissions.

    Each record: {ticker, insider, code, value}. A cluster = >= min_buyers distinct
    insiders making open-market purchases ('P') in the same issuer. Records whose
    ticker is a placeholder (unresolved issuer) are skipped so they can't form a
    phantom cluster.
    """
    buys: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        tkr = _is_real_ticker(r.get("ticker"))
        if not tkr:
            continue
        if classify_code(r.get("code", "")) == "buy":
            buys[tkr].append(r)

    out: list[Emission] = []
    for ticker, rows in buys.items():
        buyers = {r["insider"] for r in rows}
        if len(buyers) < min_buyers:
            continue
        total = sum(r.get("value", 0) for r in rows)
        # strength scales with #buyers and dollar size, capped at 1.0
        strength = min(1.0, 0.4 + 0.2 * len(buyers) + min(0.4, total / 5_000_000))
        out.append(Emission(
            ticker, "edgar:form4_cluster_buy", strength,
            f"{len(buyers)} insiders bought ${total/1000:.0f}k", is_discovery=True))
    return out


def fetch_daily_records(session: date, max_filings: int, identity: str) -> list[dict]:
    """Live path: pull the Form 4 daily index for `session`, fetch up to
    `max_filings` documents, parse each into {ticker, insider, code, value}.

    Wraps synchronous edgartools; honors SEC fair-access via a bounded worker pool
    SEPARATE from the per-ticker EdgarSource semaphore. Returns [] (never raises) on
    any failure so the signal degrades. Implementation uses edgartools'
    get_filings(form='4', filing_date=session) + .obj() transaction parsing; cap the
    count at max_filings and record truncation in the caller's coverage detail.
    """
    try:
        from edgar import get_filings, set_identity  # edgartools
        set_identity(identity)
        filings = get_filings(form="4", filing_date=session.isoformat())
        records: list[dict] = []
        for f in list(filings)[:max_filings]:
            try:
                form4 = f.obj()
                mt = getattr(form4, "market_trades", None)
                if mt is None or getattr(mt, "empty", True):
                    continue
                ticker = _is_real_ticker(getattr(getattr(form4, "issuer", None), "ticker", ""))
                insider = getattr(form4, "insider_name", None)
                if not insider:
                    # Each Form 4 is exactly one reporting owner. When the name can't
                    # be parsed, key the buyer off the filing's unique accession so two
                    # distinct unnamed insiders don't collapse to one "?" and suppress
                    # a real cluster (cluster_buys_from_records counts distinct names).
                    acc = getattr(f, "accession_no", None) or getattr(f, "accession_number", None)
                    insider = f"acc:{acc}" if acc else "?"
                for row in mt.itertuples(index=False):
                    shares = getattr(row, "Shares", None) or 0
                    price = getattr(row, "Price", None) or 0
                    records.append({
                        "ticker": ticker,
                        "insider": insider,
                        "code": getattr(row, "Code", "") or "",
                        "value": float(shares * price),
                    })
            except Exception:  # noqa: BLE001 — skip an unparseable filing
                continue
        return [r for r in records if r["ticker"]]  # _is_real_ticker() already blanked placeholders
    except Exception as exc:  # noqa: BLE001 — edgartools missing or SEC error -> degrade
        # Still never-raises, but LOUD: a missing edgartools install or an SEC outage
        # must not read as "0 filings today".
        warnings.warn(f"scout: edgar index fetch failed: {redact_secrets(str(exc))}", stacklevel=2)
        return []


def fetch_recent_records(session: date, max_filings: int, identity: str,
                         lookback: int = 4, _fetch=None) -> tuple[list[dict], date]:
    """Most-recent *published* Form 4 daily index at or before `session`.

    The SEC daily index for the current session is not published until ~02:00 UTC,
    so at the scout's after-close run time (22:30 UTC) today's index is empty even
    though the session has closed. An empty result therefore means "not published
    yet," not "no insider activity" — so we walk back up to `lookback` trading days
    to the last published index. Returns (records, session_used) so the caller can
    surface the fallback in coverage. Never raises (degrades to ([], session)).
    """
    fetch = _fetch or fetch_daily_records
    d = session
    for _ in range(lookback + 1):
        recs = fetch(d, max_filings, identity)
        if recs:
            return recs, d
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return [], session


# --- Activist SCHEDULE 13D discovery (a SECOND ingestion path on this module) ---
# A fresh initial SCHEDULE 13D = an investor crossed 5% with intent to influence: a
# leading catalyst for a re-rating. We enter after-close (T+1), so the catchable edge is
# the post-filing DRIFT, not the filing-day pop — these are "watch / pass to /deep"
# candidates, not early-pop trades. The raw firehose is noise-dominated (SPAC shells,
# foreign holdcos, affiliate/sponsor filings), so quality.py filters it; the scorer + the
# market-cap gate remain the downstream skeptic.


def _dedup_by_accession(filings):
    """edgartools get_filings returns every row TWICE (verified 2026-06-28). Keep the
    first occurrence per accession so co-filer counts and header fetches aren't doubled."""
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
                                 marquee_boost=0.2):
    """Pure aggregation: records -> one Emission per resolved ticker (initial 13D only).

    record: {ticker, cik, subject_name, activist, form, accession}. Placeholder tickers
    (unresolved subjects) are skipped so they can't form a phantom candidate. Dedup is on
    the resolved TICKER (co-filers on the same target collapse to one emission). SPAC/shell
    subjects and affiliate filings (filer name echoes the subject) are dropped by config;
    a marquee (credible) activist boosts strength. The subject CIK is carried on the
    Emission so the selection ledger can re-resolve a renamed ticker later."""
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
        out.append(Emission(tkr, "edgar:activist_13d", strength, ev, is_discovery=True,
                            cik=rows[0].get("cik")))
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
                           resolve_ticker_fn) -> list[dict]:
    """Live: pull the SCHEDULE 13D (+ legacy SC 13D) daily index for `session`, dedup the
    doubled rows, parse each header into a record. `resolve_ticker_fn(cik)->ticker|None`
    maps the SUBJECT company's CIK to its ticker. Never raises (degrades to [])."""
    try:
        rows = _get_13d_index_rows(session, identity)
        records: list[dict] = []
        for f in _dedup_by_accession(rows)[:max_filings]:
            try:
                if not is_initial_13d(getattr(f, "form", "")):
                    continue  # exclude /A amendments (prefix match returns them)
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
                    "accession": acc})
            except Exception:  # noqa: BLE001 — skip an unparseable filing
                continue
        return records
    except Exception as exc:  # noqa: BLE001 — edgartools missing / SEC error -> degrade
        # Still never-raises, but LOUD: a missing edgartools install or an SEC outage
        # must not read as "0 filings today".
        warnings.warn(f"scout: edgar index fetch failed: {redact_secrets(str(exc))}", stacklevel=2)
        return []


def fetch_recent_activist_records(session: date, max_filings: int, identity: str,
                                  resolve_ticker_fn, lookback: int = 4,
                                  _fetch=None) -> tuple[list[dict], date]:
    """Most-recent *published* SCHEDULE 13D index at or before `session` (the daily index
    isn't published until ~02:00 UTC, so the after-close run walks back to the last
    published session). Returns (records, session_used). Never raises."""
    fetch = _fetch or fetch_activist_records
    d = session
    for _ in range(lookback + 1):
        recs = fetch(d, max_filings, identity, resolve_ticker_fn)
        if recs:
            return recs, d
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return [], session


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
        warnings.warn(f"scout: edgar 13D/A index fetch failed: {redact_secrets(str(exc))}",
                      stacklevel=2)
        return []


def fetch_recent_amendment_records(session: date, max_filings: int, identity: str,
                                   resolve_ticker_fn, lookback: int = 4,
                                   _fetch=None) -> tuple[list[dict], date]:
    """Walk-back twin of fetch_recent_activist_records for the /A stream."""
    fetch = _fetch or fetch_amendment_records
    d = session
    for _ in range(lookback + 1):
        recs = fetch(d, max_filings, identity, resolve_ticker_fn)
        if recs:
            return recs, d
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return [], session


def stake_increases_from_records(records, baselines, *,
                                 min_increase_pp: float | None = None,
                                 drop_spacs: bool = True, drop_affiliates: bool = True):
    """Pure: amendment records (+ merged baselines) -> (emissions, baseline_updates).

    Rules (registered in the prereg — same live and backfill): SPAC/affiliate rows are
    EXCLUDED by signal definition; a row with unparseable stake_pct or no usable pair key
    neither emits nor updates (abstention); a pair with no baseline SEEDS and never emits;
    an emission requires new - prior >= min_increase_pp (absolute pp). Baselines update to
    the newest parsed % regardless of emission, so a slow creep can't re-qualify daily.
    """
    from .quality import is_13d_amendment
    from .stake import MIN_INCREASE_PP, STRENGTH, pair_key
    from .stake import SIGNAL as STAKE_SIGNAL
    floor = MIN_INCREASE_PP if min_increase_pp is None else min_increase_pp
    emissions: list[Emission] = []
    updates: dict[str, dict] = {}
    merged = dict(baselines or {})
    for r in records:
        tkr = _is_real_ticker(r.get("ticker"))
        if not tkr or not is_13d_amendment(r.get("form", "")):
            continue
        subj, act = r.get("subject_name", "") or "", r.get("activist", "") or ""
        if drop_spacs and is_spac_or_shell(subj):
            continue
        if drop_affiliates and is_affiliate_filing(act, subj):
            continue
        pk = pair_key(r.get("filer_cik"), r.get("cik"))
        pct = r.get("stake_pct")
        if pk is None or pct is None:
            continue                               # abstention: no emit, no update
        prior = merged.get(pk, {}).get("pct")
        entry = {"pct": pct, "date": str(r.get("file_date") or ""),
                 "adsh": r.get("accession")}
        merged[pk] = entry
        updates[pk] = entry
        if prior is None or pct - prior < floor:
            continue                               # seed-only / immaterial / decrease
        ev = (f"13D/A stake increase: {act or subj} "
              f"{prior:.1f}%→{pct:.1f}% in {subj or tkr}")
        emissions.append(Emission(
            tkr, STAKE_SIGNAL, STRENGTH, ev, is_discovery=True, cik=r.get("cik"),
            meta={"adsh": r.get("accession"), "prior_pct": prior, "new_pct": pct,
                  "file_date": str(r.get("file_date") or "")}))
    return emissions, updates
