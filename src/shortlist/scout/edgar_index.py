"""SEC Form 4 daily-index scanner -> same-session insider cluster buys.

A NEW ingestion path (the per-ticker providers/_form4.py does not do this). The
daily index lists ~1,700 Form 4 rows (CIK + accession only); resolving cluster
buys means fetching+parsing each filing, classifying P/S, mapping CIK->ticker,
and grouping by issuer. Live fetching is bounded by a per-day cap and its own
concurrency budget; this module keeps the *pure* aggregation testable in isolation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..providers._form4 import classify_code
from .models import Emission


def cluster_buys_from_records(records: list[dict], min_buyers: int = 2) -> list[Emission]:
    """Pure aggregation: records -> cluster-buy Emissions.

    Each record: {ticker, insider, code, value}. A cluster = >= min_buyers distinct
    insiders making open-market purchases ('P') in the same issuer.
    """
    buys: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if classify_code(r.get("code", "")) == "buy":
            buys[r["ticker"].upper()].append(r)

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
                ticker = (getattr(getattr(form4, "issuer", None), "ticker", "") or "").upper()
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
        return [r for r in records if r["ticker"]]
    except Exception:  # noqa: BLE001 — edgartools missing or SEC error -> degrade
        return []
