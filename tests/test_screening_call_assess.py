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


def _card(**kw):
    base = dict(ticker="X", gates=[], flags=[], confidence=1.0, coverage=None,
                abstentions=[], sic_bucket="unknown",
                metrics=SimpleNamespace(price=10.0, filing_events=None,
                                        revenue_cagr=None, fcf_cagr=None, eps_cagr=None,
                                        revenue_growth_persistence=None, gross_margin=None,
                                        net_margin=None, roic=None, debt_to_equity=None,
                                        interest_coverage=None, short_pct_outstanding=None,
                                        days_to_cover=None, financial_series=None),
                quality=80, moat=70, growth=60, momentum=50, value=40, insider=30,
                composite=65, risk=None)
    base.update(kw)
    return SimpleNamespace(**base)


_PAYLOAD = {
    "business_model_summary": "Sells widgets.",
    "moat": {"summary": "Scale.", "sources": [], "trajectory": "stable"},
    "risks": [], "red_flags": [],
    "management_capital_allocation": "Buybacks.",
    "reconciliation": [], "thesis": {"bull_case": "b", "bear_case": "x",
                                     "what_would_change_my_mind": ["margins compress"],
                                     "takeaway": "ok"},
    "call": {"stance": "BUY", "conviction": "HIGH", "rationale": "Moat is durable."},
}


def _runner_returning(payload):
    def run(prompt, system, model, timeout_s):
        return CliResult(text=json.dumps(payload), model=model, cost_usd=0.0,
                         stop_reason="end_turn", error=None)
    return run


def test_call_parsed_and_guarded_when_enabled():
    cfg = {"research": {"screening_call": {"enabled": True,
        "gate_clamp": {"_default": "HOLD"}, "conviction_cap": {"low_below": 0.45,
        "medium_below": 0.70}, "high_conviction": {"contra_flags": []}}}}
    a = assess(_card(), _bundle(), cfg, runner=_runner_returning(_PAYLOAD))
    assert a is not None and a.screening_call is not None
    assert a.screening_call.stance == "BUY"
    # HIGH with no corroborating reconciliation -> demoted
    assert a.screening_call.conviction == "MEDIUM"
    assert a.screening_call.as_of_price == 10.0


def test_disabled_is_noop():
    cfg = {"research": {"screening_call": {"enabled": False}}}
    a = assess(_card(), _bundle(), cfg, runner=_runner_returning(_PAYLOAD))
    assert a is not None
    assert a.screening_call is None
