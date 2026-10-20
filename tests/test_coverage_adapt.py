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


def test_cik_digits_in_an_edgar_url_are_not_a_status():
    snap = TickerSnapshot(ticker="MT", errors=[
        "edgar-diluted-shares-concept: Client error '404 Not Found' for url "
        "'https://data.sec.gov/api/xbrl/companyconcept/CIK0001243429/us-gaap/X.json'",
    ])
    assert snapshot_to_coverage_inputs(snap, ["edgar"])[0]["edgar"] == "error"
    snap = TickerSnapshot(ticker="CDW", errors=[
        "edgar-events: Server error '503 Service Unavailable' for url "
        "'https://www.sec.gov/Archives/edgar/data/1402057/000140205726000012/index.json'",
    ])
    assert snapshot_to_coverage_inputs(snap, ["edgar"])[0]["edgar"] == "error"


def test_real_httpx_402_and_429_errors_map_to_their_status():
    import httpx

    def _httpx_error(code: int) -> str:
        url = "https://financialmodelingprep.com/stable/profile?symbol=GEV&apikey=REDACTED"
        try:
            httpx.Response(code, request=httpx.Request("GET", url)).raise_for_status()
        except httpx.HTTPStatusError as e:
            return str(e)
        raise AssertionError("raise_for_status did not raise")

    snap = TickerSnapshot(ticker="GEV", errors=[
        f"fmp.profile: {_httpx_error(402)}", f"finnhub.metrics: {_httpx_error(429)}"])
    outcomes, _ = snapshot_to_coverage_inputs(snap, ["fmp", "finnhub"])
    assert outcomes == {"fmp": "gated_402", "finnhub": "rate_limited_429"}
