import json

from shortlist.research.assess import _salvage_json, assess
from shortlist.research.claude_cli import CliResult
from shortlist.research.models import FilingText


def _wrap(ft):
    from shortlist.research.models import FilingBundle
    return FilingBundle(tenk=ft, primary_accession=ft.accession,
                        cache_key=ft.accession, filing_date=ft.filing_date)


CONFIG = {"research": {"model": "claude-sonnet-4-6", "timeout_s": 30,
                       "max_risks": 8, "max_red_flags": 8}}

FILING = FilingText(
    ticker="AAPL", accession="0000320193-25-000123", filing_date="2025-10-31",
    business="The Company designs and sells smartphones.",
    mda="Net sales increased due to higher iPhone revenue.",
    risk_factors="Substantially all of the Company's manufacturing is performed by outsourcing partners.",
)

BUNDLE = _wrap(FILING)

GOOD = {
    "business_model_summary": "Designs and sells consumer devices.",
    "moat": {"summary": "Brand and ecosystem lock-in.", "sources": ["brand"], "trajectory": "stable"},
    "risks": [{"claim": "Manufacturing is outsourced",
               "evidence": "Substantially all of the Company's manufacturing is performed by outsourcing partners."}],
    "red_flags": [{"claim": "Invented flag", "evidence": "this exact phrase is not in the filing"}],
    "management_capital_allocation": "Returns cash via buybacks.",
    "reconciliation": [],
    "thesis": {"bull_case": "Durable.", "bear_case": "Pricey.",
               "what_would_change_my_mind": ["margins compress"],
               "takeaway": "High-quality franchise."},
}


def _runner_returning(text, stop_reason="end_turn", cost=0.02, error=None):
    def runner(prompt, system, model, timeout_s):
        return CliResult(text=text, cost_usd=cost, stop_reason=stop_reason, model=model, error=error)
    return runner


def test_salvage_strips_code_fences_and_prose():
    raw = 'Here is the JSON:\n```json\n{"a": 1}\n```\nThanks!'
    assert json.loads(_salvage_json(raw)) == {"a": 1}


def test_salvage_returns_none_when_no_object():
    assert _salvage_json("no braces here") is None


def test_assess_happy_path_and_grounding():
    runner = _runner_returning(json.dumps(GOOD))
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None
    assert a.synthesis == "High-quality franchise."
    assert a.cost_usd == 0.02 and a.model == "claude-sonnet-4-6"
    # grounded risk verifies True; fabricated red flag verifies False
    assert a.risks[0].verified is True
    assert a.red_flags[0].verified is False
    assert a.unverified_count == 1


def test_assess_salvages_fenced_json():
    runner = _runner_returning("```json\n" + json.dumps(GOOD) + "\n```")
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None and a.business_model_summary.startswith("Designs")


def test_assess_retries_then_gives_up_returns_none():
    prompts = []
    def runner(prompt, system, model, timeout_s):
        prompts.append(prompt)
        return CliResult(text="totally not json", stop_reason="end_turn", model=model)
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is None
    assert len(prompts) == 2                          # one retry, then give up
    assert "could not be parsed" in prompts[1]        # retry prompt has feedback


def test_assess_trivial_evidence_is_not_verified():
    payload = {
        "business_model_summary": "x", "moat": {"summary": "x", "sources": [], "trajectory": "stable"},
        "risks": [{"claim": "Vague risk", "evidence": "the"}],   # substring of filing, but trivially short
        "red_flags": [],
        "management_capital_allocation": "x", "thesis": {"takeaway": "x"},
    }
    runner = _runner_returning(json.dumps(payload))
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None
    assert a.risks[0].verified is False
    assert a.unverified_count == 1


def test_assess_ellipsis_evidence_is_not_verified():
    # An ellipsis-shortened quote reads as "verbatim-ish" but is not a contiguous
    # substring of the filing, so it must fail grounding — this is exactly the
    # edit the system prompt now forbids. Locks the prompt-rule ↔ verifier contract.
    payload = {
        "business_model_summary": "x", "moat": {"summary": "x", "sources": [], "trajectory": "stable"},
        "risks": [{"claim": "Manufacturing is outsourced",
                   "evidence": "Substantially all of the Company's manufacturing … by outsourcing partners."}],
        "red_flags": [],
        "management_capital_allocation": "x", "thesis": {"takeaway": "x"},
    }
    runner = _runner_returning(json.dumps(payload))
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None
    assert a.risks[0].verified is False
    assert a.unverified_count == 1


def test_assess_skips_on_runner_error():
    runner = _runner_returning("", error="claude CLI not found on PATH")
    assert assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner) is None


def test_assess_skips_on_truncation():
    runner = _runner_returning(json.dumps(GOOD), stop_reason="max_tokens")
    assert assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner) is None


def test_verify_grounding_conflicts():
    from shortlist.research.assess import _verify_grounding
    from shortlist.research.models import (QualitativeAssessment, Thesis, Conflict)
    # has "manufacturing is performed by outsourcing partners"
    a = QualitativeAssessment(
        ticker="AAPL", as_of="t", filing_accession="x", filing_date="d", model="m",
        thesis=Thesis(takeaway="t"),
        reconciliation=[
            Conflict(signal="value", tension="t",
                     filing_says="manufacturing is performed by outsourcing partners",
                     verdict="contradicts"),                       # verified
            Conflict(signal="growth", tension="t",
                     filing_says="not in the filing at all", verdict="confirms"),  # unverified
            Conflict(signal="risk", tension="t", filing_says="",
                     verdict="silent"),                            # silent → silent_count
        ])
    _verify_grounding(a, BUNDLE)
    assert a.reconciliation[0].verified is True
    assert a.reconciliation[1].verified is False
    assert a.unverified_count == 1     # the non-silent unverified conflict
    assert a.silent_count == 1
    assert a.reconciliation[2].filing_says == ""   # silent quote cleared


def test_quant_context_lists_present_scores_and_omits_none():
    from shortlist.research.assess import _quant_context
    from shortlist.models import ScoreCard, StockMetrics
    card = ScoreCard(
        ticker="AAPL", composite=72.0, quality=80.0, moat=None, growth=40.0,
        momentum=55.0, value=88.0, opportunity=88.0, insider=60.0,
        metrics=StockMetrics(ticker="AAPL", revenue_cagr=0.03), sic_bucket="unknown",
        confidence=0.8, gates=["below_min_mktcap"], flags=["crowded_short"])
    block = _quant_context(card)
    assert "QUANT CONTEXT" in block
    assert "value" in block and "88" in block       # present score shown
    assert "moat" not in block                       # None score omitted entirely
    assert "revenue_cagr" in block and "0.03" in block
    assert "below_min_mktcap" in block and "crowded_short" in block


def test_assess_passes_valid_signals():
    import json
    from shortlist.research import assess as A
    from shortlist.models import ScoreCard, StockMetrics
    payload = {
        "business_model_summary": "x",
        "moat": {"summary": "x", "sources": [], "trajectory": "stable"},
        "risks": [], "red_flags": [], "management_capital_allocation": "x",
        "reconciliation": [{"signal": "flag:activist_13d", "tension": "t",
                            "filing_says": "", "verdict": "silent"}],
        "thesis": {"takeaway": "x"},
    }
    card = ScoreCard(ticker="AAPL", composite=70.0, quality=None, moat=None,
                     growth=None, momentum=None, value=None, opportunity=None,
                     insider=None, metrics=StockMetrics(ticker="AAPL"))
    runner = _runner_returning(json.dumps(payload))
    a = A.assess(card, BUNDLE, CONFIG, runner=runner)
    assert a is not None
    assert a.reconciliation[0].signal == "flag:activist_13d"


def test_prompt_includes_new_sections_when_present():
    from shortlist.research.assess import _build_user_prompt
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("AAPL", "acc", "2025-10-31", business="b", mda="m", risk_factors="r")
    bundle = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc+q",
                          filing_date="2025-10-31", tenq_mda="Quarter update.",
                          added_risks_text="Cybersecurity risk. A breach could harm us.")
    p = _build_user_prompt(bundle, {"research": {}}, card=None)
    assert "LATEST 10-Q" in p and "Quarter update." in p
    assert "NEWLY ADDED RISK FACTORS" in p and "Cybersecurity risk." in p


def test_prompt_omits_new_sections_when_empty():
    from shortlist.research.assess import _build_user_prompt
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("AAPL", "acc", "d", business="b", mda="m", risk_factors="r")
    bundle = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                          filing_date="d")
    p = _build_user_prompt(bundle, {"research": {}}, card=None)
    assert "LATEST 10-Q" not in p
    assert "NEWLY ADDED RISK FACTORS" not in p


def test_grounding_verifies_tenq_quote_not_prior_year():
    from shortlist.research.assess import _verify_grounding
    from shortlist.research.models import (FilingBundle, FilingText, Finding,
                                           QualitativeAssessment, Moat, Thesis)
    tenk = FilingText("A", "acc", "d", business="b", mda="m", risk_factors="r")
    bundle = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                          filing_date="d", tenq_mda="Margins compressed this quarter.")
    a = QualitativeAssessment(ticker="A", as_of="t", filing_accession="acc",
                              filing_date="d", model="m", moat=Moat(), thesis=Thesis())
    a.added_risks = [Finding(claim="margin", evidence="Margins compressed this quarter.")]
    a.risks = [Finding(claim="ghost", evidence="A risk only in the prior-year filing.")]
    _verify_grounding(a, bundle)
    assert a.added_risks[0].verified is True       # in 10-Q MD&A (haystack)
    assert a.risks[0].verified is False            # prior-year text excluded from haystack


def test_assess_sets_cache_key():
    from shortlist.research import assess as A
    from shortlist.research.claude_cli import CliResult
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("A", "acc", "d", business="We make things.", mda="", risk_factors="")
    bundle = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc+q",
                          filing_date="d")
    payload = ('{"business_model_summary":"x","moat":{"summary":"m"},"risks":[],'
               '"red_flags":[],"management_capital_allocation":"y","added_risks":[],'
               '"thesis":{"bull_case":"","bear_case":"","what_would_change_my_mind":[],'
               '"takeaway":"t"}}')
    fake = CliResult(text=payload, model="m", cost_usd=0.01, stop_reason="end_turn",
                     error=None)
    a = A.assess(card=None, bundle=bundle, config={"research": {}},
                 runner=lambda **kw: fake)
    assert a is not None and a.cache_key == "acc+q"


def test_render_series_formats_usd_millions_and_eps():
    from shortlist.research.assess import _render_series
    series = [
        {"fiscal_year": 2025, "period_end": "2025-09-28", "revenue": 391_035e6,
         "gross_profit": 180_683e6, "net_income": 93_736e6,
         "operating_cash_flow": 118_254e6, "free_cash_flow": 108_807e6,
         "diluted_eps": 6.08, "total_debt": 106_629e6, "diluted_shares": 15_344e6},
    ]
    out = _render_series(series)
    assert "5-year financials" in out
    assert "FY2025 (2025-09-28)" in out
    assert "rev 391,035" in out and "GP 180,683" in out
    assert "dEPS 6.08" in out and "shrs 15,344" in out and "debt 106,629" in out


def test_render_series_omits_none_cells_and_skips_empty_rows():
    from shortlist.research.assess import _render_series
    series = [
        {"fiscal_year": 2025, "period_end": None, "revenue": 100e6,
         "gross_profit": None, "net_income": None, "operating_cash_flow": None,
         "free_cash_flow": None, "diluted_eps": None, "total_debt": None,
         "diluted_shares": None},
        {"fiscal_year": 2024, "period_end": None, "revenue": None,
         "gross_profit": None, "net_income": None, "operating_cash_flow": None,
         "free_cash_flow": None, "diluted_eps": None, "total_debt": None,
         "diluted_shares": None},
    ]
    out = _render_series(series)
    assert "FY2025" in out and "rev 100" in out
    assert "GP" not in out                       # None column omitted
    assert "FY2024" not in out                   # all-None row skipped


def test_render_series_empty_returns_blank():
    from shortlist.research.assess import _render_series
    assert _render_series(None) == "" and _render_series([]) == ""


def test_quant_context_includes_series_when_present():
    from shortlist.research.assess import _quant_context
    from shortlist.models import ScoreCard, StockMetrics
    m = StockMetrics(ticker="AAPL", revenue_cagr=0.03, financial_series=[
        {"fiscal_year": 2025, "period_end": "2025-09-28", "revenue": 391_035e6,
         "gross_profit": None, "net_income": 93_736e6, "operating_cash_flow": None,
         "free_cash_flow": None, "diluted_eps": 6.08, "total_debt": None,
         "diluted_shares": None}])
    card = ScoreCard(ticker="AAPL", composite=72.0, quality=80.0, moat=None,
                     growth=None, momentum=None, value=88.0, opportunity=88.0,
                     insider=None, metrics=m, sic_bucket="unknown")
    block = _quant_context(card)
    assert "5-year financials" in block and "rev 391,035" in block


def test_system_prompt_mentions_trajectory():
    from shortlist.research.assess import SYSTEM_PROMPT
    assert "trajectory" in SYSTEM_PROMPT.lower()
