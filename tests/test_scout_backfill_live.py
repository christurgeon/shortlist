"""Live verification of the 13D backfill (Task 7, validation-harness P2 Plan 3).

Two @pytest.mark.live smoke tests hitting REAL EDGAR/Yahoo/archive.org, run manually
(`-m live`) — never in the default `uv run pytest` suite. VPS-budgeted (spec §7-brief):
small windows, small `max_records` caps, so total live request volume stays well under the
hard per-run budgets (~60 SEC / ~10 Yahoo / ~5 archive.org). Follows the skipif-on-
SEC_IDENTITY convention in tests/test_scout_delisting_fetch.py.
"""
import json
import os
from datetime import date

import pytest

from shortlist.backtest.edgar_history import fetch_activist_window
from shortlist.scout.backfill import load_backfill_events, run_backfill_13d

pytestmark_live = pytest.mark.skipif(
    not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra")

_SCRATCH_JSONL = ("/tmp/claude-1000/-home-chris-shortlist/"
                  "6b9248e8-594d-4d3c-a1ac-d4a635d5a4bc/scratchpad/task7-e2e-13d.jsonl")


@pytest.mark.live
@pytestmark_live
def test_live_walker_smoke_three_day_window():
    """fetch_activist_window over a real 3-trading-day window: only internal-consistency
    properties are asserted (never a fixed name/count — the point is "the walker behaves",
    not "this specific week had exactly N filings")."""
    identity = os.environ["SEC_IDENTITY"]
    start, end = date(2023, 10, 10), date(2023, 10, 12)
    recs = fetch_activist_window(start, end, identity, max_records=40)

    assert recs is not None
    n = len(recs)
    print(f"\n[live] walker 2023-10-10..2023-10-12: n={n} records")
    assert 3 <= n <= 60, f"count {n} outside the spec's plausible band (§8: ~4-12/day * 3d)"

    for r in recs:
        assert start <= r["filing_date"] <= end

    n_with_cik = sum(1 for r in recs if r.get("cik") and len(r["cik"]) == 10 and r["cik"].isdigit())
    assert n_with_cik / n >= 0.80, f"only {n_with_cik}/{n} records carry a resolved 10-digit CIK"

    accessions = [r["accession"] for r in recs]
    assert len(accessions) == len(set(accessions)), "duplicate accession — dedup regressed"


@pytest.mark.live
@pytestmark_live
def test_live_end_to_end_one_week_backfill():
    """run_backfill_13d over one mature week (Aug 2022, K=12m matures well before `today`).
    max_records is capped small (8) to bound BOTH the SEC header-fetch volume AND the
    downstream Yahoo fetch_history volume (one per resolved, non-sentinel ticker) inside this
    single test — the walker test above already spends most of the SEC budget."""
    identity = os.environ["SEC_IDENTITY"]
    config = {"scout": {"backfill": {
        "max_records": 8, "sec_throttle_s": 0.2, "yahoo_throttle_s": 0.3,
        "symbology_cache_dir": ".cache/symbology",
    }}}
    out_path = _SCRATCH_JSONL
    if os.path.exists(out_path):
        os.remove(out_path)               # fresh run each time (idempotent-append semantics
                                            # would otherwise skip everything as "existing")

    summary = run_backfill_13d(config, start=date(2022, 8, 8), end=date(2022, 8, 12),
                               identity=identity, out_path=out_path)
    print("\n[live] one-week backfill summary:")
    print(json.dumps(summary, default=str, indent=2))

    assert summary["n_selected"] > 0
    assert summary["fraction"] > 0
    assert summary["n_measurable"] > 0

    rows = load_backfill_events(out_path)
    assert len(rows) == summary["n_selected"]
    for row in rows:                      # round-trip shape sanity (JSONL -> dict, not CohortEvent
        assert "ticker" in row and "meta" in row and "event_date" in row
        date.fromisoformat(row["event_date"])   # parses cleanly

    measurable_with_price = [r for r in rows
                             if r.get("meta", {}).get("measurable") and
                             isinstance(r.get("as_of_price"), (int, float)) and
                             r["as_of_price"] > 0]
    assert len(measurable_with_price) >= 1, "no row has a real measurable as_of_price"
    sample = measurable_with_price[0]
    print(f"[live] sample measurable row: ticker={sample['ticker']} "
          f"as_of_price={sample['as_of_price']} event_date={sample['event_date']}")
