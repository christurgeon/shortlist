import json
from types import SimpleNamespace
from shortlist.research.assess import assess, SYSTEM_PROMPT
from shortlist.research.claude_cli import CliResult
from shortlist.research.models import FilingText, FilingBundle


def _bundle():
    ft = FilingText(ticker="X", accession="acc-1", filing_date="2026-01-01",
                    business="We sell widgets and gizmos to enterprises worldwide.",
                    mda="Revenue grew on strong demand for our flagship product line.",
                    risk_factors="Competition is intense across all our segments today.")
    return FilingBundle(tenk=ft, primary_accession="acc-1", cache_key="acc-1",
                        filing_date="2026-01-01")


def _card():
    return SimpleNamespace(
        ticker="X", gates=["negative_fcf"], flags=[], confidence=0.3, coverage=None,
        abstentions=[], sic_bucket="unknown",
        metrics=SimpleNamespace(price=10.0, filing_events=None, revenue_cagr=None,
            fcf_cagr=None, eps_cagr=None, revenue_growth_persistence=None,
            gross_margin=None, net_margin=None, roic=None, debt_to_equity=None,
            interest_coverage=None, short_pct_outstanding=None, days_to_cover=None,
            financial_series=None),
        quality=80, moat=70, growth=60, momentum=50, value=None, insider=30,
        composite=65, risk=None)


_PAYLOAD = {
    "business_model_summary": "Sells widgets.",
    "moat": {"summary": "Scale.", "sources": [], "trajectory": "stable"},
    "risks": [], "red_flags": [], "management_capital_allocation": "Buybacks.",
    "reconciliation": [], "thesis": {"bull_case": "b", "bear_case": "x",
        "what_would_change_my_mind": [], "takeaway": "ok"},
    "call": {"stance": "STRONG_BUY", "conviction": "HIGH", "rationale": "r"},
}


def _runner():
    def run(prompt, system, model, timeout_s):
        run.captured = {"system": system, "prompt": prompt}
        return CliResult(text=json.dumps(_PAYLOAD), model=model, cost_usd=0.0,
                         stop_reason="end_turn", error=None)
    return run


def test_disabled_no_call_and_prompt_unchanged():
    cfg = {"research": {"screening_call": {"enabled": False}}}
    r = _runner()
    a = assess(_card(), _bundle(), cfg, runner=r)
    assert a.screening_call is None
    # system prompt is the bare SYSTEM_PROMPT (no call addendum)
    assert r.captured["system"] == SYSTEM_PROMPT
    # no DATA GAPS line in the user prompt even though `value` is None + gated
    assert "DATA GAPS" not in r.captured["prompt"]
