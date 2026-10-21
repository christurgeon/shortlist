"""Live: EdgarSource's newest-year statements agree with SEC companyfacts on one filer per
known edgartools row-selection failure. Run this before bumping edgartools: a release that
moves a `standard_concept` bucket or adds a period column fails here, not in production.

    IPAR  noncash lease line bucketed as OCF; an empty 2018 FY column
    MO    equity-method dividends bucketed as OCF
    UNH   Revenue bucket on contract revenue, not the `Revenues` total
    BSX   no `NetIncomeLoss` row
    AMD   continuing-operations OCF beside a total with discontinued operations

The reference rules match `docs/audits/scripts/probe_edgar_statements_vs_companyfacts.py`."""
import os
from datetime import date

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra"),
]

_CIKS = {"IPAR": 822663, "MO": 764180, "UNH": 731766, "BSX": 885725, "AMD": 2488}
_TOL = 0.02
_REF = {
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                            "NetCashProvidedByUsedInOperatingActivities"],
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}


def _companyfacts(cik: int) -> dict:
    import httpx

    from shortlist.edgar.sec_throttle import sec_throttle
    sec_throttle()("live_statements_test")
    resp = httpx.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                     headers={"User-Agent": os.environ["SEC_IDENTITY"]}, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def _annual_10k_value(facts: dict, names: list[str], end: str):
    """Latest-filed 10-K value of the first tag in `names` reporting an annual period at `end`."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for n in names:
        rows = [r for r in gaap.get(n, {}).get("units", {}).get("USD", [])
                if r.get("end") == end and "start" in r
                and r.get("form", "").startswith("10-K")
                and 350 <= (date.fromisoformat(end) - date.fromisoformat(r["start"])).days <= 380]
        if rows:
            return max(rows, key=lambda r: r.get("filed", ""))["val"]
    return None


@pytest.mark.parametrize("ticker", sorted(_CIKS))
def test_newest_year_statements_match_companyfacts(ticker):
    from shortlist.data.sources.edgar import EdgarSource

    src = EdgarSource()
    errors: list[str] = []
    st = src._build_financials_snapshot(
        ticker, src._fetch_financials_object(ticker), errors).statements
    assert st is not None and st.fiscal_period_end, errors
    end = st.fiscal_period_end[0]
    facts = _companyfacts(_CIKS[ticker])

    for field in ("operating_cash_flow", "revenue", "net_income"):
        want = _annual_10k_value(facts, _REF[field], end)
        assert want, f"{ticker} {field}: no companyfacts reference at {end}"
        got = getattr(st, field)
        assert got, f"{ticker} {field} is empty at {end}"
        assert abs(got[0] - want) / abs(want) < _TOL, f"{ticker} {field} {got[0]} vs {want}"

    ocf = _annual_10k_value(facts, _REF["operating_cash_flow"], end)
    capex = _annual_10k_value(facts, _REF["capex"], end)
    assert st.free_cash_flow and capex is not None
    assert (st.free_cash_flow[0] < 0) == (ocf - capex < 0), f"{ticker} FCF sign"
