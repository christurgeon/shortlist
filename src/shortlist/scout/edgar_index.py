"""SEC Form 4 daily-index scanner -> same-session insider cluster buys.

A NEW ingestion path (the per-ticker providers/_form4.py does not do this). The
daily index lists ~1,700 Form 4 rows (CIK + accession only); resolving cluster
buys means fetching+parsing each filing, classifying P/S, mapping CIK->ticker,
and grouping by issuer. Live fetching is bounded by a per-day cap and its own
concurrency budget; this module keeps the *pure* aggregation testable in isolation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from ..providers._form4 import classify_code
from .calendar import is_trading_day
from .models import Emission


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
        from edgar import set_identity, get_filings  # edgartools
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
                insider = getattr(form4, "insider_name", "?") or "?"
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
    except Exception:  # noqa: BLE001 — edgartools missing or SEC error -> degrade
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
