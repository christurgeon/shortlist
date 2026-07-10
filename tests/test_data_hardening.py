"""Hardening round-1 regression tests: stderr diagnostics, None-safe gross
margins, load-once locks (thundering herd), FINRA page cap, corrupt-store
resilience, and the collector's per-source exception shim."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from shortlist.data.collector import collect_async
from shortlist.data.coverage_adapt import snapshot_to_coverage_inputs
from shortlist.data.models import Statements, TickerSnapshot
from shortlist.data.sources import (
    FinraSource,
    GovContractsSource,
    MockSource,
    Source,
    WsbSource,
    YahooSource,
)


# --- item 2: gross_margins survives a None gross-profit year ----------------

def test_gross_margins_skips_none_gross_profit_year():
    st = Statements(gross_profit=[None, 40.0], revenue=[100.0, 100.0])
    assert st.gross_margins() == [0.4]              # no TypeError, year skipped


def test_gross_margins_still_skips_falsy_revenue():
    st = Statements(gross_profit=[40.0, 10.0], revenue=[100.0, None])
    assert st.gross_margins() == [0.4]


# --- item 5: cold-cache bulk loads fire once under concurrency --------------

_CHART = {"chart": {"result": [{
    "timestamp": [1, 2, 3],
    "indicators": {"adjclose": [{"adjclose": [1.0, 2.0, 3.0]}]},
}]}}


def test_yahoo_spy_loads_once_under_concurrency(tmp_path):
    src = YahooSource(cache_dir=str(tmp_path))
    calls = {"n": 0}

    async def fake_chart(symbol):
        calls["n"] += 1
        await asyncio.sleep(0.01)       # yield so all tasks reach the cold check
        return _CHART

    src._get_chart = fake_chart

    async def main():
        await asyncio.gather(*(src._spy() for _ in range(8)))
        await src.aclose()

    asyncio.run(main())
    assert calls["n"] == 1
    assert src._spy_closes == [1.0, 2.0, 3.0]
    assert src._spy_dates and len(src._spy_dates) == 3


def test_finra_bulk_load_fires_once_under_concurrency(tmp_path):
    src = FinraSource(cache_dir=str(tmp_path))
    calls = {"parts": 0}

    async def fake_parts():
        calls["parts"] += 1
        await asyncio.sleep(0.01)
        return {"availablePartitions": [{"partitions": ["2026-06-30"]}]}

    async def fake_page(settlement, offset):
        return [{"symbolCode": "AAPL", "settlementDate": settlement,
                 "currentShortPositionQuantity": "100"}]

    src._fetch_partitions = fake_parts
    src._fetch_page = fake_page

    async def main():
        results = await asyncio.gather(*(src.fetch("AAPL") for _ in range(8)))
        await src.aclose()
        return results

    results = asyncio.run(main())
    assert calls["parts"] == 1
    assert all(r.partial.short_interest is not None for r in results)


def test_wsb_bulk_load_fires_once_under_concurrency(monkeypatch, tmp_path):
    calls = {"n": 0}
    lock = threading.Lock()

    def fake_fetch(cache_dir, timeout):
        with lock:
            calls["n"] += 1
        time.sleep(0.02)                # widen the to_thread race window
        return {}, None

    monkeypatch.setattr("shortlist.data.apewisdom.fetch_wsb_mentions", fake_fetch)
    src = WsbSource(cache_dir=str(tmp_path))
    asyncio.run(_gather_fetches(src, 8))
    assert calls["n"] == 1


async def _gather_fetches(src, n, ticker="AAPL"):
    results = await asyncio.gather(*(src.fetch(ticker) for _ in range(n)))
    await src.aclose()
    return results


def test_govcontracts_name_index_loads_once_under_concurrency(monkeypatch, tmp_path):
    calls = {"n": 0}

    async def fake_raw(client, cache_dir, month):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return {"0": {"ticker": "LMT", "title": "Lockheed Martin Corp"}}

    monkeypatch.setattr("shortlist.backtest.xbrl.fetch_company_tickers_raw", fake_raw)
    src = GovContractsSource(config={"gov_contracts": {"cache_dir": str(tmp_path)}})
    # Unknown ticker -> fetch() returns right after the name load: no HTTP needed.
    results = asyncio.run(_gather_fetches(src, 8, ticker="ZZZ"))
    assert calls["n"] == 1
    assert all(r.partial.gov_contracts is None for r in results)


# --- item 6: FINRA pagination is hard-capped ---------------------------------

def test_finra_pagination_hard_cap(tmp_path, capsys):
    src = FinraSource(cache_dir=str(tmp_path))
    src.PAGE = 2          # tiny pages so the cap is cheap to hit
    src.MAX_PAGES = 3

    async def fake_parts():
        return {"availablePartitions": [{"partitions": ["2026-06-30"]}]}

    async def fake_page(settlement, offset):
        # Always a FULL page: without the cap this loops forever.
        return [{"symbolCode": f"S{offset + i}"} for i in range(2)]

    src._fetch_partitions = fake_parts
    src._fetch_page = fake_page

    async def main():
        await src._load()
        await src.aclose()

    asyncio.run(main())
    assert src._load_error is None
    assert len(src._index) == 6           # 3 pages x 2 rows, then stop
    assert "pagination cap" in capsys.readouterr().err
    # A truncated row set must NEVER be persisted: the scout's short-interest
    # fetcher shares this file and requires the COMPLETE rows for the cycle.
    assert list(tmp_path.iterdir()) == []


# --- item 8: from_dict tolerates corrupt (non-dict) section payloads ---------

def test_from_dict_ignores_non_dict_sections():
    # Tolerated (no AttributeError) but LOUD — a silent drop would let a
    # store-format regression thin every replayed snapshot invisibly.
    with pytest.warns(UserWarning, match="non-dict payload"):
        snap = TickerSnapshot.from_dict({
            "ticker": "X",
            "fundamentals": ["corrupt", "list"],
            "price": "garbage",
            "insider": 42,
            "short_interest": [],
        })
    assert snap.ticker == "X"
    assert snap.fundamentals is None
    assert snap.price is None
    assert snap.insider is None
    assert snap.short_interest is None


def test_from_dict_absent_sections_stay_silent():
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("error")          # any warning would raise
        snap = TickerSnapshot.from_dict({"ticker": "X"})   # sections merely absent
    assert snap.fundamentals is None


# --- item 9: a raising source degrades to coverage, not a crashed run --------

class _BoomSource(Source):
    name = "boom"

    async def fetch(self, ticker):
        raise ValueError("exploded https://x.example/?apikey=SECRET123")


def test_collector_survives_a_raising_source():
    snaps = asyncio.run(collect_async(["LMT"], [MockSource(), _BoomSource()]))
    (snap,) = snaps
    assert snap.fundamentals.pe_ttm == 20.0             # mock data still merged
    boom_errs = [e for e in snap.errors if e.startswith("boom:")]
    assert boom_errs, snap.errors
    assert "SECRET123" not in boom_errs[0]              # redacted
    assert "<redacted>" in boom_errs[0]
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["mock", "boom"])
    assert outcomes["boom"] == "error"                  # degraded, not crashed
    assert outcomes["mock"] == "ok"


def test_collector_multi_ticker_run_survives_one_source_raising():
    snaps = asyncio.run(collect_async(["GEV", "LMT"], [MockSource(), _BoomSource()]))
    assert {s.ticker for s in snaps} == {"GEV", "LMT"}
    for s in snaps:
        assert any(e.startswith("boom:") for e in s.errors)
        assert s.coverage() > 0                         # mock data intact
