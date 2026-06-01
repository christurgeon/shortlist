from shortlist.data.models import TickerSnapshot, Profile
from shortlist.data.coverage_adapt import snapshot_to_coverage_inputs


def test_gated_fmp_maps_to_402():
    snap = TickerSnapshot(
        ticker="GEV",
        profile=Profile(market_cap=1e9),
        provenance={"profile": ["finnhub"], "price": ["yahoo"]},
        errors=["fmp: 402 Special Endpoint for GEV", "edgar-financials: 503 backoff"],
    )
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert outcomes["fmp"] == "gated_402"
    assert outcomes["edgar"] == "error"             # "edgar-financials:" maps to source "edgar"
    assert outcomes["finnhub"] == "ok"
    assert {"finnhub", "yahoo"} <= contributed


def test_clean_run_all_ok():
    snap = TickerSnapshot(
        ticker="AAPL",
        provenance={"profile": ["fmp"], "price": ["yahoo"], "insider": ["edgar"]},
        errors=[],
    )
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert set(outcomes.values()) == {"ok"}
    assert {"fmp", "yahoo", "edgar"} <= contributed
