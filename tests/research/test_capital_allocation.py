"""The "Capital allocation" section of the /deep brief: computed from card data, never shown to
the model, every line abstaining on its own."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from shortlist import research
from shortlist.research import assess as assess_mod
from shortlist.research import cachekey, report
from shortlist.research import capital_allocation as ca
from shortlist.research.models import FilingBundle, FilingText, Moat, QualitativeAssessment, Thesis

B = 1e9


def _fs(year, ocf, fcf, shares):
    return {"fiscal_year": year, "operating_cash_flow": ocf, "free_cash_flow": fcf,
            "diluted_shares": shares}


def _ic(year, nopat, ic):
    return {"fiscal_year": year, "nopat": nopat, "invested_capital": ic}


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


def test_a_full_section():
    body, status = ca.section(_card())
    assert status == ca.SHOWN
    assert body.split("\n") == [
        "Accounting data from SEC filings over FY2023–FY2025 (3 fiscal years). It shows where "
        "cash went and what the reported capital earned; it is not a judgement of returns or "
        "business quality.",
        "- Cash, FY2023–FY2025 summed: operating cash flow $330.0B, capital spending $60.0B, "
        "free cash flow $270.0B.",
        "- FY2025 only: buybacks $40.0B, dividends $20.0B; together 60% of that year's free "
        "cash flow.",
        "- Diluted share count: -5.0% from FY2023 to FY2025 (fewer shares).",
        "- ROIC: FY2023 12.5%, FY2024 13.9%, FY2025 15.0%.",
        "- Incremental ROIC, FY2023 to FY2025: 25% (NOPAT up $10.0B, invested capital up "
        "$40.0B). Reinvestment rate: 73% (rise in invested capital over NOPAT of $55.0B in "
        "FY2024–FY2025).",
        "- Basis: buybacks and dividends are the cash paid in the year, read from cash-flow "
        "tags, and only the latest year's are on file. ROIC is operating income × (1 − 25%) over "
        "total debt plus total equity at year end. Incremental ROIC and the reinvestment rate "
        "are shown only when invested capital grew by at least 5% of its starting level.",
    ]


def test_a_masked_sector_gets_one_sentence():
    masked = [{"field": "roic", "reason": "inapplicable", "scope": "leg"}]
    assert ca.section(_card(masked)) == ("Not shown: not meaningful for this sector.",
                                         ca.NOT_SHOWN)
    masked = [{"field": "fcf_yield", "reason": "inapplicable", "scope": "leg"}]
    assert ca.section(_card(masked))[1] == ca.NOT_SHOWN
    # A leg that is merely missing is not a sector mask.
    missing = [{"field": "roic", "reason": "missing", "scope": "leg"}]
    assert ca.section(_card(missing))[1] == ca.SHOWN


def test_no_statement_data_is_said_not_left_silent():
    assert ca.section(SimpleNamespace(ticker="X", metrics=None)) == (
        "Not shown: no SEC statement data on the card.", ca.NOT_SHOWN)
    bare = _card(financial_series=None, invested_capital_series=None, dividends_paid=None,
                 repurchases=None)
    assert ca.section(bare) == ("Not shown: no SEC statement data on the card.", ca.NOT_SHOWN)


def test_nothing_resolving_is_one_sentence():
    card = _card(financial_series=[_fs(2025, None, None, None)], invested_capital_series=None,
                 dividends_paid=None, repurchases=None)
    assert ca.section(card) == (
        "Not shown: the statements on file do not resolve any of its lines.", ca.NOT_SHOWN)


def test_incremental_roic_abstains_when_invested_capital_barely_moved():
    card = _card(invested_capital_series=[_ic(2025, 30 * B, 167 * B), _ic(2024, 25 * B, 163 * B),
                                          _ic(2023, 20 * B, 160 * B)])
    (line,) = [ln for ln in _lines(card) if ln.startswith("- Incremental")]
    assert line == ("- Incremental ROIC and reinvestment rate: not shown (invested capital "
                    "changed by less than 5% of its starting level from FY2023 to FY2025).")


def test_incremental_roic_abstains_when_invested_capital_fell():
    card = _card(invested_capital_series=[_ic(2025, 30 * B, 100 * B), _ic(2024, 25 * B, 130 * B),
                                          _ic(2023, 20 * B, 160 * B)])
    (line,) = [ln for ln in _lines(card) if ln.startswith("- Incremental")]
    assert line == ("- Incremental ROIC and reinvestment rate: not shown (invested capital fell "
                    "from FY2023 to FY2025).")


def test_incremental_roic_needs_two_consecutive_years():
    for series in ([_ic(2025, 30 * B, 200 * B)],
                   [_ic(2025, 30 * B, 200 * B), _ic(2023, 20 * B, 160 * B)]):
        (line,) = [ln for ln in _lines(_card(invested_capital_series=series))
                   if ln.startswith("- Incremental")]
        assert line == ("- Incremental ROIC and reinvestment rate: not shown (fewer than two "
                        "consecutive fiscal years of ROIC inputs).")


def test_an_explosive_ratio_is_never_printed():
    card = _card(invested_capital_series=[_ic(2025, 60 * B, 170 * B), _ic(2024, 25 * B, 165 * B),
                                          _ic(2023, 2 * B, 160 * B)])     # +58 on +10
    (line,) = [ln for ln in _lines(card) if ln.startswith("- Incremental")]
    assert line.startswith("- Incremental ROIC, FY2023 to FY2025: more than 100% (")
    card = _card(invested_capital_series=[_ic(2025, -5 * B, 170 * B), _ic(2024, 3 * B, 165 * B),
                                          _ic(2023, 20 * B, 160 * B)])    # -25 on +10
    (line,) = [ln for ln in _lines(card) if ln.startswith("- Incremental")]
    assert line.endswith("Reinvestment rate: not shown (NOPAT over FY2024–FY2025 was not "
                         "positive).")
    assert "less than -100%" in line


def test_the_payout_share_abstains_on_non_positive_free_cash_flow():
    fs = [_fs(2025, 10 * B, -5 * B, 95.0), _fs(2024, 110 * B, 90 * B, 98.0)]
    (line,) = [ln for ln in _lines(_card(financial_series=fs)) if ln.startswith("- FY2025")]
    assert line == ("- FY2025 only: buybacks $40.0B, dividends $20.0B; not shown as a share of "
                    "free cash flow, which was not positive.")


def test_an_untagged_leg_is_named_not_zeroed():
    (line,) = [ln for ln in _lines(_card(dividends_paid=None)) if ln.startswith("- FY2025")]
    assert line == ("- FY2025 only: buybacks $40.0B, no dividend tag; together 40% of that "
                    "year's free cash flow.")
    lines = _lines(_card(dividends_paid=None, repurchases=None))
    assert ("- Buybacks and dividends: not shown (neither is tagged in the latest cash-flow "
            "statement).") in lines


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
        (ln,) = [x for x in _lines(_card(financial_series=fs)) if x.startswith("- Diluted")]
        return ln
    assert line(103.0) == "- Diluted share count: +3.0% from FY2024 to FY2025 (more shares)."
    assert line(99.999) == "- Diluted share count: 0.0% from FY2024 to FY2025 (unchanged)."


def test_money_scales_to_millions():
    fs = [_fs(2025, 120e6, 100e6, 95.0), _fs(2024, 110e6, 90e6, 100.0)]
    lines = _lines(_card(financial_series=fs, dividends_paid=None, repurchases=-4.5e6))
    assert ("- Cash, FY2024–FY2025 summed: operating cash flow $230M, capital spending $40M, "
            "free cash flow $190M.") in lines


def test_garbage_never_raises():
    card = _card(financial_series=[{"fiscal_year": "x", "operating_cash_flow": "y"}],
                 invested_capital_series=[{"nopat": object()}], dividends_paid="z")
    body, status = ca.section(card)
    assert status in (ca.SHOWN, ca.NOT_SHOWN) and body


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
    a = _assessment()
    assert ca.HEADING not in report.to_markdown(a)


def test_the_model_never_sees_the_section():
    source = inspect.getsource(assess_mod)
    for name in ("capital_allocation_line", "invested_capital_series", "capital_allocation import",
                 ca.HEADING):
        assert name not in source, name
    bundle = _bundle()
    body, _ = ca.section(_card())
    assert all(body not in text for _, text in bundle.segments())


def test_the_prompt_fingerprint_does_not_cover_the_section():
    assert "capital_allocation" not in cachekey._PROMPT_MODULES
