"""The "Capital allocation" section of the /deep brief: computed from card data, never shown to
the model, every line abstaining on its own."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from shortlist import research
from shortlist.research import cachekey, report
from shortlist.research import capital_allocation as ca
from shortlist.research.models import FilingBundle, FilingText, Moat, QualitativeAssessment, Thesis

B = 1e9
BASIS = ("- Basis: buybacks and dividends are the cash paid in the year, from the cash-flow "
         "statement; only the latest year's are on file. ROIC is operating income × (1 − 25%) "
         "over total debt plus total equity at year end. Invested capital includes cash held and "
         "acquired goodwill, so a change in it can reflect cash raised or retained, not only "
         "investment. The added-capital lines are shown only when invested capital grew by at "
         "least 5% of its starting level.")
MASK = [{"field": "roic", "reason": "inapplicable", "scope": "leg"}]


def _fs(year, ocf, fcf, shares, end="12-31"):
    return {"fiscal_year": year, "period_end": f"{year}-{end}", "operating_cash_flow": ocf,
            "free_cash_flow": fcf, "diluted_shares": shares}


def _ic(year, nopat, ic, end="12-31"):
    return {"fiscal_year": year, "period_end": f"{year}-{end}", "nopat": nopat,
            "invested_capital": ic}


def _metrics(**over):
    base = dict(
        financial_series=[_fs(2025, 120 * B, 100 * B, 95.0), _fs(2024, 110 * B, 90 * B, 98.0),
                          _fs(2023, 100 * B, 80 * B, 100.0)],
        invested_capital_series=[_ic(2025, 30 * B, 200 * B), _ic(2024, 25 * B, 180 * B),
                                 _ic(2023, 20 * B, 160 * B)],
        dividends_paid=-20 * B, repurchases=-40 * B)
    base.update(over)
    return SimpleNamespace(**base)


def _card(abstentions=(), **over):
    return SimpleNamespace(ticker="ACME", metrics=_metrics(**over), abstentions=list(abstentions))


def _lines(card) -> list[str]:
    body, _ = ca.section(card)
    return body.split("\n")


def _line(card, prefix) -> str:
    (ln,) = [x for x in _lines(card) if x.startswith(prefix)]
    return ln


def test_a_full_section():
    body, status = ca.section(_card())
    assert status == ca.SHOWN
    assert body.split("\n") == [
        "Accounting data from the company's filed statements over FY2023–FY2025 (3 fiscal "
        "years). It is not a judgement of returns or business quality.",
        "- Cash, FY2023–FY2025 summed: operating cash flow $330.0B, capital spending $60.0B, "
        "free cash flow $270.0B.",
        "- FY2025 only: buybacks $40.0B, dividends $20.0B; together 60% of that year's free "
        "cash flow.",
        "- Diluted share count: -5.0% from FY2023 to FY2025 (fewer shares).",
        "- ROIC: FY2023 12.5%, FY2024 13.9%, FY2025 15.0%.",
        "- Return on added capital, FY2023 to FY2025: 25% (NOPAT up $10.0B, invested capital up "
        "$40.0B). Added capital / NOPAT: 73% (NOPAT of $55.0B in FY2024–FY2025).",
        BASIS,
    ]


def test_a_non_december_year_is_labelled_by_its_period_end():
    fs = [_fs(2026, 20 * B, 15 * B, 99.0, "02-01"), _fs(2025, 20 * B, 15 * B, 100.0, "02-02")]
    ics = [_ic(2026, 10 * B, 70 * B, "02-01"), _ic(2025, 9 * B, 60 * B, "02-02")]
    lines = _lines(_card(financial_series=fs, invested_capital_series=ics))
    assert lines[0].startswith("Accounting data from the company's filed statements over "
                               "years ended Feb 2025–Feb 2026 (2 fiscal years).")
    assert ("- Cash, years ended Feb 2025–Feb 2026 summed: operating cash flow $40.0B, capital "
            "spending $10.0B, free cash flow $30.0B.") in lines
    assert "- Year ended Feb 2026 only: buybacks $40.0B" in "\n".join(lines)
    assert ("- Diluted share count: -1.0% from the year ended Feb 2025 to the year ended Feb "
            "2026 (fewer shares).") in lines
    assert "- ROIC: year ended Feb 2025 15.0%, year ended Feb 2026 14.3%." in lines
    assert _line(_card(financial_series=fs, invested_capital_series=ics),
                 "- Return on added capital").startswith(
        "- Return on added capital, year ended Feb 2025 to year ended Feb 2026: 10% (")


def test_a_masked_sector_keeps_only_payouts_and_share_count():
    body, status = ca.section(_card(MASK))
    assert status == ca.SHOWN
    assert body.split("\n") == [
        "Accounting data from the company's filed statements over FY2023–FY2025 (3 fiscal "
        "years). It is not a judgement of returns or business quality.",
        "- FY2025 only: buybacks $40.0B, dividends $20.0B.",
        "- Diluted share count: -5.0% from FY2023 to FY2025 (fewer shares).",
        "- Cash-flow sums, payout share of free cash flow, ROIC and added capital: not shown "
        "(not meaningful for this sector).",
        "- Basis: buybacks and dividends are the cash paid in the year, from the cash-flow "
        "statement; only the latest year's are on file.",
    ]
    fcf_masked = [{"field": "fcf_yield", "reason": "inapplicable", "scope": "leg"}]
    assert ca.section(_card(fcf_masked)) == (body, status)


def test_a_masked_sector_with_nothing_left_gets_one_sentence():
    card = _card(MASK, dividends_paid=None, repurchases=None,
                 financial_series=[_fs(2025, 1 * B, 1 * B, None)])
    assert ca.section(card) == ("Not shown: not meaningful for this sector.", ca.NOT_SHOWN)


def test_a_missing_leg_is_not_a_sector_mask():
    missing = [{"field": "roic", "reason": "missing", "scope": "leg"}]
    assert ca.section(_card(missing)) == ca.section(_card())


def test_no_statement_data_is_said_not_left_silent():
    assert ca.section(SimpleNamespace(ticker="X", metrics=None)) == (
        "Not shown: no statement data on the card.", ca.NOT_SHOWN)
    bare = _card(financial_series=None, invested_capital_series=None, dividends_paid=None,
                 repurchases=None)
    assert ca.section(bare) == ("Not shown: no statement data on the card.", ca.NOT_SHOWN)


def test_nothing_resolving_is_one_sentence():
    card = _card(financial_series=[_fs(2025, None, None, None)], invested_capital_series=None,
                 dividends_paid=None, repurchases=None)
    assert ca.section(card) == (
        "Not shown: the statements on file do not resolve any of its lines.", ca.NOT_SHOWN)


def test_added_capital_abstains_when_invested_capital_barely_moved():
    card = _card(invested_capital_series=[_ic(2025, 30 * B, 167 * B), _ic(2024, 25 * B, 163 * B),
                                          _ic(2023, 20 * B, 160 * B)])
    assert _line(card, "- Return on added capital") == (
        "- Return on added capital: not shown (invested capital changed by less than 5% of its "
        "starting level from FY2023 to FY2025).")


def test_added_capital_abstains_when_invested_capital_fell():
    card = _card(invested_capital_series=[_ic(2025, 30 * B, 100 * B), _ic(2024, 25 * B, 130 * B),
                                          _ic(2023, 20 * B, 160 * B)])
    assert _line(card, "- Return on added capital") == (
        "- Return on added capital: not shown (invested capital fell from FY2023 to FY2025).")


def test_added_capital_needs_two_consecutive_years():
    for series in ([_ic(2025, 30 * B, 200 * B)],
                   [_ic(2025, 30 * B, 200 * B), _ic(2023, 20 * B, 160 * B)]):
        assert _line(_card(invested_capital_series=series), "- Return on added capital") == (
            "- Return on added capital: not shown (fewer than two consecutive fiscal years of "
            "ROIC inputs).")


def test_an_explosive_ratio_is_never_printed():
    card = _card(invested_capital_series=[_ic(2025, 60 * B, 170 * B), _ic(2024, 25 * B, 165 * B),
                                          _ic(2023, 2 * B, 160 * B)])     # +58 on +10
    assert _line(card, "- Return on added capital").startswith(
        "- Return on added capital, FY2023 to FY2025: more than 100% (")
    card = _card(invested_capital_series=[_ic(2025, -5 * B, 170 * B), _ic(2024, 3 * B, 165 * B),
                                          _ic(2023, 20 * B, 160 * B)])    # -25 on +10
    line = _line(card, "- Return on added capital")
    assert "less than -100%" in line
    assert line.endswith("Added capital / NOPAT: not shown (NOPAT over FY2024–FY2025 was not "
                         "positive).")


def test_the_payout_share_abstains_on_non_positive_free_cash_flow():
    fs = [_fs(2025, 10 * B, -5 * B, 95.0), _fs(2024, 110 * B, 90 * B, 98.0)]
    assert _line(_card(financial_series=fs), "- FY2025") == (
        "- FY2025 only: buybacks $40.0B, dividends $20.0B; share of free cash flow not shown "
        "(free cash flow was not positive).")


def test_a_missing_free_cash_flow_is_not_called_non_positive():
    fs = [_fs(2025, 10 * B, None, 95.0), _fs(2024, 110 * B, 90 * B, 98.0)]
    assert _line(_card(financial_series=fs), "- FY2025") == (
        "- FY2025 only: buybacks $40.0B, dividends $20.0B; share of free cash flow not shown "
        "(free cash flow not on file).")


def test_an_untagged_leg_is_named_and_the_share_is_a_floor():
    assert _line(_card(dividends_paid=None), "- FY2025") == (
        "- FY2025 only: buybacks $40.0B, dividends not on file; at least 40% of that year's "
        "free cash flow.")
    assert ("- Buybacks and dividends: not shown (not on file for the latest year)."
            in _lines(_card(dividends_paid=None, repurchases=None)))


def test_sums_use_the_newest_run_of_consecutive_complete_years():
    fs = [_fs(2025, 120 * B, 100 * B, 95.0), _fs(2024, None, 90 * B, 98.0),
          _fs(2023, 100 * B, 80 * B, 100.0)]
    lines = _lines(_card(financial_series=fs))
    assert ("- Cash, FY2025 only: operating cash flow $120.0B, capital spending $20.0B, free "
            "cash flow $100.0B.") in lines
    assert "- Diluted share count: -5.0% from FY2023 to FY2025 (fewer shares)." in lines


def test_share_count_needs_two_years():
    fs = [_fs(2025, 120 * B, 100 * B, 95.0), _fs(2024, 110 * B, 90 * B, None)]
    assert ("- Diluted share count: not shown (fewer than two consecutive fiscal years on file)."
            in _lines(_card(financial_series=fs)))


def test_share_count_direction_and_rounding():
    def line(new):
        fs = [_fs(2025, 1 * B, 1 * B, new), _fs(2024, 1 * B, 1 * B, 100.0)]
        return _line(_card(financial_series=fs), "- Diluted")
    assert line(103.0) == "- Diluted share count: +3.0% from FY2024 to FY2025 (more shares)."
    assert line(99.999) == "- Diluted share count: 0.0% from FY2024 to FY2025 (unchanged)."


def test_money_scales_to_millions():
    fs = [_fs(2025, 120e6, 100e6, 95.0), _fs(2024, 110e6, 90e6, 100.0)]
    lines = _lines(_card(financial_series=fs, dividends_paid=None, repurchases=-4.5e6))
    assert ("- Cash, FY2024–FY2025 summed: operating cash flow $230M, capital spending $40M, "
            "free cash flow $190M.") in lines
    assert "buybacks $0, dividends $20.0B" in "\n".join(_lines(_card(repurchases=0.0)))


def test_malformed_values_abstain_line_by_line():
    card = _card(financial_series=[{"fiscal_year": "x", "operating_cash_flow": "y"}],
                 invested_capital_series=[{"nopat": object()}], dividends_paid="z")
    body, status = ca.section(card)
    assert status == ca.SHOWN and "- FY" not in body and "buybacks $40.0B" in body


def test_input_that_raises_is_unreadable_and_logged(capsys):
    assert ca.section(_card(financial_series=5)) == (ca.NOT_SHOWN_UNREADABLE, ca.NOT_SHOWN)
    assert capsys.readouterr().err.startswith("[capital-allocation] ACME: ")


# ---------------------------------------------------------------- wiring


def _assessment():
    return QualitativeAssessment(ticker="ACME", as_of="t", filing_accession="acc-1",
                                 filing_date="2026-02-19", model="m", moat=Moat(),
                                 thesis=Thesis(takeaway="read."), cache_key="acc-1",
                                 management_capital_allocation="Buys back stock.")


def _bundle():
    tenk = FilingText("ACME", "acc-1", "2026-02-19", business="b")
    return FilingBundle(tenk=tenk, primary_accession="acc-1", cache_key="acc-1",
                        filing_date="2026-02-19")


def _enrich(tmp_path, monkeypatch):
    calls = []
    real = ca.section

    def fake_assess(card, bundle, config, **kw):
        calls.append("assess")
        return _assessment()

    def spy(card):
        calls.append("capital_allocation")
        return real(card)

    monkeypatch.setattr(ca, "section", spy)
    card = _card()
    card.__dict__.update(composite=90.0, confidence=1.0, scored=True, gates=[], passed=True)
    config = {"research": {"output_root": str(tmp_path)}}
    (result,) = research.enrich([card], config, top_n=1, fetch=lambda t, **k: _bundle(),
                                assess_fn=fake_assess)
    return result, calls


def test_the_section_is_computed_after_the_model_call_and_stored_with_the_brief(tmp_path,
                                                                                 monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch)
    assert calls == ["assess", "capital_allocation"]
    brief = Path(result.brief_path).read_text()
    assert f"## {ca.HEADING}" in brief and "- ROIC: FY2023 12.5%, FY2024 13.9%, FY2025 15.0%." in brief
    assert brief.index("## Management & capital allocation") < brief.index(ca.HEADING)
    assert brief.index(ca.HEADING) < brief.index("## Material risks")
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert record["capital_allocation_status"] == ca.SHOWN
    assert record["capital_allocation_line"] == ca.section(_card())[0]


def test_a_brief_written_without_the_section_renders_as_before():
    assert ca.HEADING not in report.to_markdown(_assessment())


def test_the_prompt_does_not_change_with_the_sections_inputs():
    from shortlist.config import load_config
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.data.models import Statements, TickerSnapshot
    from shortlist.research.assess import _build_user_prompt
    from shortlist.scoring import score

    config = load_config(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    st = Statements(fiscal_years=[2025, 2024], fiscal_period_end=["2025-12-31", "2024-12-31"],
                    operating_income=[30 * B, 25 * B], total_debt=[50 * B, 50 * B],
                    total_equity=[150 * B, 130 * B], free_cash_flow=[9 * B, 8 * B],
                    dividends_paid=-2 * B, repurchases=-3 * B)
    card = score(snapshot_to_metrics(TickerSnapshot(ticker="ACME", statements=st)), config)
    assert card.metrics.invested_capital_series and card.metrics.repurchases
    with_inputs = _build_user_prompt(_bundle(), config, card=card)
    card.metrics.invested_capital_series = None
    card.metrics.dividends_paid = card.metrics.repurchases = None
    assert _build_user_prompt(_bundle(), config, card=card) == with_inputs
    body, _ = ca.section(_card())
    assert all(body not in text for _, text in _bundle().segments())


def test_the_prompt_fingerprint_does_not_cover_the_section():
    assert "capital_allocation" not in cachekey._PROMPT_MODULES
