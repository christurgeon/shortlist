import json

from shortlist.research.assess import _salvage_json, assess
from shortlist.research.claude_cli import CliResult
from shortlist.research.models import FilingText


def _wrap(ft):
    from shortlist.research.models import FilingBundle
    return FilingBundle(tenk=ft, primary_accession=ft.accession,
                        cache_key=ft.accession, filing_date=ft.filing_date)


CONFIG = {"research": {"model": "claude-sonnet-5", "timeout_s": 30,
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


def test_salvage_returns_first_balanced_object_despite_trailing_brace_prose():
    # The old first-{ .. last-} slice would swallow the trailing "{y}" and fail to parse.
    raw = '{"a": 1, "b": "x"}\n\nNote: the placeholder {y} is illustrative.'
    assert json.loads(_salvage_json(raw)) == {"a": 1, "b": "x"}


def test_salvage_ignores_braces_inside_strings():
    raw = '{"note": "a } brace and a { brace inside a string"}'
    assert json.loads(_salvage_json(raw)) == {"note": "a } brace and a { brace inside a string"}


def test_salvage_returns_none_on_unbalanced_truncated_object():
    assert _salvage_json('{"a": 1, "b":') is None


def test_salvage_handles_escaped_quote_before_brace_in_string():
    # An escaped quote inside a string must not end string-tracking early; a `}` that
    # follows it is still inside the string, not the object's closing brace.
    obj = {"k": 'a \\" } still in string'}
    raw = json.dumps(obj)
    assert json.loads(_salvage_json(raw)) == obj


def test_assess_accumulates_cost_across_reparse_retry():
    # First call returns unparseable JSON (cost 0.02), retry succeeds (cost 0.03);
    # the persisted cost must reflect BOTH calls, not just the second.
    seq = [CliResult(text="not json", cost_usd=0.02, stop_reason="end_turn", model="m"),
           CliResult(text=json.dumps(GOOD), cost_usd=0.03, stop_reason="end_turn", model="m")]
    calls = {"i": 0}
    def runner(prompt, system, model, timeout_s):
        r = seq[calls["i"]]
        calls["i"] += 1
        return r
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None and calls["i"] == 2
    assert abs(a.cost_usd - 0.05) < 1e-9     # 0.02 (failed parse) + 0.03 (success)


def test_assess_happy_path_and_grounding():
    runner = _runner_returning(json.dumps(GOOD))
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None
    assert a.synthesis == "High-quality franchise."
    assert a.cost_usd == 0.02 and a.model == "claude-sonnet-5"
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
    assert len(prompts) == 3                          # max_attempts default, then give up
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


def test_assess_retries_on_transient_error_then_succeeds():
    # A transient CLI failure (e.g. a timeout) must NOT sink the assessment — the
    # very next call commonly succeeds (the observed production failure mode).
    seq = [CliResult(text="", error="claude timed out after 600s", transient=True),
           CliResult(text=json.dumps(GOOD), cost_usd=0.03, stop_reason="end_turn", model="m")]
    calls = {"i": 0}
    def runner(prompt, system, model, timeout_s):
        r = seq[calls["i"]]
        calls["i"] += 1
        return r
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None and calls["i"] == 2
    assert a.synthesis == "High-quality franchise."


def test_assess_logs_per_attempt_duration_and_outcome(capsys):
    # Each claude call emits one observability line (ticker, attempt, duration, outcome)
    # to stderr -> journald, so timeout/retry rates can be tuned against real numbers.
    seq = [CliResult(text="", error="claude timed out after 600s", transient=True),
           CliResult(text=json.dumps(GOOD), cost_usd=0.03, stop_reason="end_turn", model="m")]
    calls = {"i": 0}
    def runner(prompt, system, model, timeout_s):
        r = seq[calls["i"]]
        calls["i"] += 1
        return r
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is not None
    err = capsys.readouterr().err
    assert "AAPL" in err
    assert "attempt 1/" in err and "attempt 2/" in err
    assert "outcome=transient_error" in err and "outcome=ok" in err
    assert "dur=" in err


def test_assess_does_not_retry_permanent_error():
    # A permanent failure (binary missing) is not worth retrying — give up after one call.
    calls = {"i": 0}
    def runner(prompt, system, model, timeout_s):
        calls["i"] += 1
        return CliResult(text="", error="claude CLI not found on PATH", transient=False)
    a = assess(card=None, bundle=BUNDLE, config=CONFIG, runner=runner)
    assert a is None and calls["i"] == 1


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
    assert "Annual financials" in out
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
    assert "Annual financials" in block and "rev 391,035" in block


def test_system_prompt_mentions_trajectory():
    from shortlist.research.assess import SYSTEM_PROMPT
    assert "trajectory" in SYSTEM_PROMPT.lower()


def _dcf_card():
    from shortlist.models import ScoreCard, StockMetrics
    m = StockMetrics(ticker="AAPL", market_cap=2000e6, financial_series=[
        {"free_cash_flow": 100e6}, {"free_cash_flow": 100e6},
        {"free_cash_flow": 100e6}])
    return ScoreCard(ticker="AAPL", composite=72.0, quality=80.0, moat=None,
                     growth=None, momentum=None, value=88.0, opportunity=88.0,
                     insider=None, metrics=m, sic_bucket="unknown")


_RDCFG = {"enabled": True, "discount_rate": 0.10, "base_years": 3,
          "run_rate_flag_ratio": 1.5, "display_floor": -0.50}


def test_quant_context_includes_reverse_dcf_line():
    from shortlist.research.assess import _quant_context
    out = _quant_context(_dcf_card(), "", _RDCFG)
    assert "Price-implied FCF growth:" in out


def test_quant_context_omits_reverse_dcf_when_disabled():
    from shortlist.research.assess import _quant_context
    out = _quant_context(_dcf_card(), "", {"enabled": False})
    assert "Price-implied FCF growth:" not in out


def test_quant_context_two_arg_call_emits_no_dcf_line():
    # back-compat: existing 2-arg / 1-arg callers must still work, no DCF line.
    from shortlist.research.assess import _quant_context
    assert "Price-implied FCF growth:" not in _quant_context(_dcf_card(), "")
    assert "Price-implied FCF growth:" not in _quant_context(_dcf_card())


def test_reverse_dcf_line_excluded_from_haystack():
    # the computed number must never be verifiable as a filing quote.
    from shortlist.research.assess import _quant_context
    from shortlist.research.models import FilingBundle, FilingText
    line = _quant_context(_dcf_card(), "", _RDCFG)
    assert "perpetual FCF growth" in line
    tenk = FilingText("AAPL", "acc", "2025-10-31", business="b", mda="m",
                      risk_factors="r")
    bundle = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                          filing_date="2025-10-31")
    assert "perpetual FCF growth" not in bundle.haystack()


# --- 2026-08-04 deep-brief assessment fixes (docs/audits/2026-08-04-deep-brief-assessment.md) ---

def test_typographic_punctuation_in_filing_does_not_break_grounding():
    """D1: SEC filings use curly apostrophes/dashes and NBSP; models transcribe ASCII.
    A faithful verbatim quote must verify despite that skew, or the _(unverified)_
    marker is noise (measured: 73% of unverified findings recover under folding)."""
    from shortlist.research.models import FilingText
    filing = FilingText(
        ticker="AAPL", accession="acc-1", filing_date="2025-10-31",
        business="", mda="",
        # curly apostrophe (U+2019), em dashes (U+2014), non-breaking space (U+00A0)
        risk_factors="A significant majority of the Company’s manufacturing "
                     "is performed — in whole or in part — by outsourcing partners.",
    )
    payload = dict(GOOD)
    payload["risks"] = [{"claim": "Manufacturing is outsourced",
                         "evidence": "A significant majority of the Company's manufacturing "
                                     "is performed - in whole or in part - by outsourcing partners."}]
    payload["red_flags"] = []
    a = assess(card=None, bundle=_wrap(filing), config=CONFIG,
               runner=_runner_returning(json.dumps(payload)))
    assert a is not None
    assert a.risks[0].verified is True
    assert a.unverified_count == 0


def test_quant_context_includes_valuation_inputs():
    """D2: the model is asked to reconcile a `value` sub-score; without a multiple it
    can only infer meaning from the score. Measured: 1/35 briefs cited any multiple."""
    from shortlist.research.assess import _quant_context

    class _M:
        revenue_cagr = fcf_cagr = eps_cagr = revenue_growth_persistence = None
        gross_margin = net_margin = roic = debt_to_equity = interest_coverage = None
        short_pct_outstanding = days_to_cover = short_interest_rising = None
        financial_series = None
        pe_ttm, pe_median_5y, fcf_yield, peg = 28.4, 24.1, 0.031, 2.2
        market_cap, price = 3.2e12, 214.5

    class _C:
        metrics = _M(); composite = 70.0; confidence = 0.9
        quality = moat = growth = momentum = value = insider = risk = None
        gates: list = []; flags: list = []; sic_bucket = None

    out = _quant_context(_C())
    assert "pe_ttm=28.4" in out
    assert "fcf_yield=0.031" in out
    assert "peg=2.2" in out
    assert "price=214" in out or "price=215" in out    # 3sig-fig formatting


def test_apply_guards_persists_card_confidence():
    """D4: confidence is the input to two of three conviction guards but was never
    persisted, so no retrospective could attribute a conviction to a rule."""
    from shortlist.research.assess import apply_guards
    from shortlist.research.models import QualitativeAssessment, ScreeningCall

    class _C:
        metrics = None; confidence = 0.83
        gates: list = []; flags: list = []; abstentions: list = []; coverage = None

    a = QualitativeAssessment(ticker="X", as_of="", filing_accession="", filing_date="",
                              model="m")
    a.screening_call = ScreeningCall(stance="HOLD", conviction="MEDIUM", rationale="r")
    apply_guards(a, _C(), {"research": {"screening_call": {"enabled": True}}})
    assert a.screening_call.confidence == 0.83


def test_filing_events_line_includes_8k_item_codes():
    """D5: edgartools already returns 8-K item codes and the repo discards them, so a
    non-reliance restatement (4.02) renders as an undated form label."""
    from shortlist.research.assess import _build_user_prompt
    events = [{"form": "8-K", "filed": "2026-07-23", "items": "4.02,9.01"}]
    prompt = _build_user_prompt(BUNDLE, CONFIG, None, filing_events=events)
    assert "4.02" in prompt


def test_macro_line_rendered_when_enabled():
    """D8: MacroContext is fetched in _do_deep and threaded to score() and the report,
    but never to the brief — which reasons about a discount rate and leverage."""
    from shortlist.data.macro import MacroContext
    from shortlist.research.assess import _build_user_prompt
    macro = MacroContext(as_of="2026-08-04", dgs10=4.21, t10y2y=0.35, hy_oas=2.87,
                         vix=15.2, fedfunds=4.33, regime="neutral", risk_off=False)
    cfg = {"research": {**CONFIG["research"], "macro": {"enabled": True}}}
    prompt = _build_user_prompt(BUNDLE, cfg, None, macro=macro)
    assert "4.21" in prompt and "2.87" in prompt and "neutral" in prompt


def test_macro_absent_leaves_prompt_byte_identical():
    """Repo-wide convention: a new config block is a byte-identical no-op when absent."""
    from shortlist.research.assess import _build_user_prompt
    base = _build_user_prompt(BUNDLE, CONFIG, None)
    with_macro_key_but_no_macro = _build_user_prompt(BUNDLE, CONFIG, None, macro=None)
    assert base == with_macro_key_but_no_macro


def test_valuation_scalars_never_render_in_scientific_notation():
    """`%g` renders a BRK.A-class share price as 7.12e+05 and a thin FCF yield as
    3.1e-05; the model then has to decode them."""
    from shortlist.research.assess import _fmt_num
    assert _fmt_num(712000.0) == "712,000"
    assert _fmt_num(5000.0) == "5,000"
    assert _fmt_num(214.5) == "214.5"
    assert _fmt_num(0.031) == "0.031"
    assert _fmt_num(0.000031) == "0.000031"
    assert "e" not in _fmt_num(3.2e12)


def test_market_cap_is_never_reported_as_zero_for_a_small_cap():
    """A sub-$1B cap under `$%.0fB` prints '$0B' — a confidently WRONG number, worse
    than the scientific notation it replaced. RBKB/TACT are sub-$1B and get briefs
    (the /deep path researches gated names: require_passed=False)."""
    from shortlist.research.assess import _fmt_mcap
    assert _fmt_mcap(150e6) == "$150M"
    assert _fmt_mcap(490e6) == "$490M"
    assert _fmt_mcap(5e9) == "$5.0B"
    assert _fmt_mcap(3.2e12) == "$3.20T"


def test_similarity_line_renders_and_stays_out_of_the_haystack():
    from shortlist.research.assess import _similarity_line
    line = _similarity_line(0.62)
    assert "38%" in line                     # 1 - 0.62, rendered as percent rewritten
    assert "0.62" in line
    assert "context only" in line.lower()


def test_similarity_line_absent_when_none():
    from shortlist.research.assess import _similarity_line
    assert _similarity_line(None) == ""


def test_prompt_is_byte_identical_when_similarity_is_none():
    """spec §7: disabled / uncomputable similarity must not perturb the prompt."""
    from shortlist.research.assess import _build_user_prompt
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("A", "acc", "2026-01-01", business="b", mda="m", risk_factors="r")
    base = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-01-01")
    withnone = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                            filing_date="2026-01-01", text_similarity=None)
    cfg = {"research": {}}
    assert _build_user_prompt(base, cfg) == _build_user_prompt(withnone, cfg)
