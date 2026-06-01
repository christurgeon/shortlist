from shortlist.data.models import TickerSnapshot, Profile
from shortlist.data.coverage_adapt import snapshot_to_coverage_inputs


def test_gated_fmp_maps_to_402():
    snap = TickerSnapshot(
        ticker="GEV",
        profile=Profile(market_cap=1e9),
        provenance={"profile": ["finnhub"], "price": ["yahoo"]},
        errors=["fmp.profile: 402 Special Endpoint for GEV", "edgar-financials: 503 backoff"],
    )
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert outcomes["fmp"] == "gated_402"
    assert outcomes["edgar"] == "error"             # "edgar-financials:" maps to source "edgar"
    assert outcomes["finnhub"] == "ok"
    assert {"finnhub", "yahoo"} <= contributed


def test_dot_form_prefixes_map_to_base_source():
    from shortlist.data.models import TickerSnapshot
    from shortlist.data.coverage_adapt import snapshot_to_coverage_inputs
    snap = TickerSnapshot(ticker="X", errors=[
        "fmp.quote: 402 gated", "finnhub.metrics: 500 boom", "edgar-financials: 503", "edgar: timeout",
    ])
    outcomes, _ = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert outcomes["fmp"] == "gated_402"
    assert outcomes["finnhub"] == "error"
    assert outcomes["edgar"] == "error"      # both edgar/edgar-financials map to edgar
    assert outcomes["yahoo"] == "ok"


def test_clean_run_all_ok():
    snap = TickerSnapshot(
        ticker="AAPL",
        provenance={"profile": ["fmp"], "price": ["yahoo"], "insider": ["edgar"]},
        errors=[],
    )
    outcomes, contributed = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert set(outcomes.values()) == {"ok"}
    assert {"fmp", "yahoo", "edgar"} <= contributed
