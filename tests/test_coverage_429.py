from shortlist.data.models import TickerSnapshot, Profile
from shortlist.data.coverage_adapt import snapshot_to_coverage_inputs


def test_429_error_maps_to_rate_limited():
    snap = TickerSnapshot(
        ticker="AAPL",
        profile=Profile(market_cap=1e9),
        provenance={"profile": ["finnhub"], "price": ["yahoo"]},
        errors=["fmp.quote: 429 Too Many Requests"],
    )
    outcomes, _ = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert outcomes["fmp"] == "rate_limited_429"


def test_402_takes_precedence_over_429_and_429_over_generic_error():
    snap = TickerSnapshot(
        ticker="X",
        errors=[
            "fmp.quote: 402 Special Endpoint",
            "fmp.profile: 429 Too Many Requests",
            "finnhub.metrics: 429 rate limited",
            "edgar-financials: 503 backoff",
        ],
    )
    outcomes, _ = snapshot_to_coverage_inputs(snap, ["yahoo", "edgar", "fmp", "finnhub"])
    assert outcomes["fmp"] == "gated_402"            # 402 ordered before 429
    assert outcomes["finnhub"] == "rate_limited_429"  # 429 ordered before generic error
    assert outcomes["edgar"] == "error"
    assert outcomes["yahoo"] == "ok"
