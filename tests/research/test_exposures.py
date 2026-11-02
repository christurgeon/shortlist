"""The exposure map: the exposure note family (research/notes.py) and the `exposures`
brief field (models, assess, report, bot report).

Titles below are quoted as filed in the probe recorded in
docs/audits/2026-11-02-deep-exposure-map-design.md.
"""
import json

import pytest

from shortlist.bot.report.html import HtmlBuilder
from shortlist.bot.report.sections import Detail, _Research
from shortlist.bot.report.viewmodel import _assessment_vm
from shortlist.research import filings
from shortlist.research import notes as nm
from shortlist.research.assess import (SYSTEM_PROMPT, _build_user_prompt,
                                       _verify_grounding, assess)
from shortlist.research.cachekey import _PROMPT_MODULES, brief_key
from shortlist.research.claude_cli import CliResult
from shortlist.research.models import (EXPOSURE_KINDS, SCHEMA_HINT, Exposure, FilingBundle,
                                       FilingText, Finding, QualitativeAssessment,
                                       StatementNote, assessment_from_payload)
from shortlist.research.report import to_markdown

XCFG = nm.exposure_config_block(None)


class _Note:
    def __init__(self, title, markdown="body"):
        self.title = title
        self._md = markdown

    def to_markdown(self):
        return self._md


class _Notes:
    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]


class _Filing:
    def __init__(self, notes):
        self.notes = notes


def _titles(selected):
    return [n.title for n in selected]


# ------------------------------------------------------------------- selection

@pytest.mark.parametrize("title", [
    "SEGMENT INFORMATION",                                                  # NKE / F
    "Segment Information and Geographic Data",                              # AAPL
    "SEGMENT DATA",                                                         # DE
    "Industry Segment Operations",                                          # AMAT
    "OPERATING SEGMENTS AND GEOGRAPHIC INFORMATION",                        # CROX
    "OPERATING SEGMENTS",                                                   # KO
    "Description of Business, Including Segment and Geographic Area Information",  # TXN
    "Information on Business Segments",                                     # LMT
    "Disclosures About Segments And Related Information",                   # XOM
    "Segment and Geographic Information",                                   # O
    "Commitments, Contingencies and Supply Concentrations",                 # AAPL
    "Financial Instruments and Risk Concentration",                         # TXN
    "Credit Risk Concentrations",                                           # JPM
    "Revenue",                                                              # LLY / GD
    "REVENUES",                                                             # NKE / CROX
    "NET OPERATING REVENUES",                                               # KO
    "REVENUE RECOGNITION",                                                  # DE
])
def test_select_exposures_matches_every_probed_title(title):
    assert _titles(nm.select_exposures(_Notes([_Note(title)]))) == [title]


@pytest.mark.parametrize("title", [
    "OTHER LIABILITIES AND DEFERRED REVENUE",          # F
    "Deferred Revenue",
    "Unearned Revenue",
    "Contract Balances and Performance Obligations",   # AMAT
    "Income Taxes",
    "Borrowing Facilities and Debt",                   # AMAT: the debt family's note
    "Debt",
    "Summary of Significant Accounting Policies",
    "Earnings Per Share",
])
def test_select_exposures_rejects_non_exposure_titles(title):
    assert nm.select_exposures(_Notes([_Note(title)])) == []


def test_the_debt_family_never_takes_an_exposure_note():
    idx = _Notes([_Note("SEGMENT INFORMATION"), _Note("Revenue"),
                  _Note("Financial Instruments and Risk Concentration")])
    assert nm.select(idx) == []


def test_segment_notes_rank_before_concentration_before_revenue():
    """AAPL files Revenue first and the segment note last; the segment note, which
    carries the geography, must not be crowded out of the budget."""
    idx = _Notes([_Note("Revenue"),
                  _Note("Commitments, Contingencies and Supply Concentrations"),
                  _Note("Segment Information and Geographic Data")])
    assert _titles(nm.select_exposures(idx)) == [
        "Segment Information and Geographic Data",
        "Commitments, Contingencies and Supply Concentrations",
        "Revenue"]


def test_untitled_entries_are_skipped():
    """AMAT's index carries a None at position 0."""
    assert _titles(nm.select_exposures(_Notes([None, _Note("SEGMENT DATA")]))) == [
        "SEGMENT DATA"]


# ---------------------------------------------------------------------- budgets

def test_exposure_defaults():
    assert XCFG == {"enabled": True, "max_notes_per_form": 3,
                    "max_chars_per_note": 12000, "max_chars_10k": 16000}


def test_exposure_config_reads_its_own_block_not_the_debt_block():
    cfg = nm.exposure_config_block({"research": {"notes": {"max_notes_per_form": 9},
                                                 "exposure_notes": {"max_chars_10k": 5}}})
    assert cfg["max_notes_per_form"] == 3
    assert cfg["max_chars_10k"] == 5


def test_collect_exposures_labels_a_10k_note():
    out = nm.collect_exposures(_Filing(_Notes([_Note("SEGMENT INFORMATION", "China 30%")])),
                               "acc-1", "AMAT", XCFG)
    assert len(out) == 1
    n = out[0]
    assert (n.form, n.accession, n.title, n.label, n.text, n.truncated) == (
        "10-K", "acc-1", "SEGMENT INFORMATION", "10-K note: SEGMENT INFORMATION",
        "China 30%", False)


def test_collect_exposures_caps_each_note_and_the_form_total():
    cfg = {**XCFG, "max_chars_per_note": 300, "max_chars_10k": 520}
    idx = _Notes([_Note("SEGMENT DATA", "a " * 400), _Note("Revenue", "b " * 400)])
    out = nm.collect_exposures(_Filing(idx), "acc", "T", cfg)
    assert [len(n.text) <= 300 for n in out] == [True, True]
    assert len(out[1].text) < 300
    assert sum(len(n.text) for n in out) <= 520
    assert all(n.truncated for n in out)
    assert all("truncat" not in n.text.lower() for n in out)


def test_collect_exposures_stops_at_max_notes():
    idx = _Notes([_Note(t, "x") for t in ("SEGMENT DATA", "Credit Risk Concentrations",
                                          "Revenue", "REVENUES")])
    out = nm.collect_exposures(_Filing(idx), "acc", "T", XCFG)
    assert len(out) == 3


def test_collect_exposures_disabled_is_empty():
    idx = _Notes([_Note("SEGMENT DATA", "x")])
    assert nm.collect_exposures(_Filing(idx), "acc", "T", {**XCFG, "enabled": False}) == []


def test_collect_exposures_failure_is_logged_under_its_own_name(capsys):
    class _Exploding:
        @property
        def notes(self):
            raise ValueError("xbrl broken")
    assert nm.collect_exposures(_Exploding(), "acc", "T", XCFG) == []
    assert "10-K exposure notes" in capsys.readouterr().err


def test_debt_family_log_wording_is_unchanged(capsys):
    class _Exploding:
        @property
        def notes(self):
            raise ValueError("xbrl broken")
    nm.collect(_Exploding(), "10-K", "acc", "T", nm.config_block(None))
    assert "research: 10-K debt notes for T" in capsys.readouterr().err


# --------------------------------------------------------------- bundle and prompt

SEG = ("| China | 8,529 | 10,117 | 7,247 |\nDuring fiscal 2025, two customers accounted "
       "for approximately 19% and 15%, respectively, of our net revenue.")


def _xnote(title="Industry Segment Operations", text=SEG, truncated=False):
    return StatementNote(form="10-K", accession="acc", title=title,
                         label=f"10-K note: {title}", text=text, truncated=truncated)


def _bundle(exposure_notes=(), debt_notes=()):
    tenk = FilingText("AMAT", "acc", "2025-12-12", business="We sell equipment.",
                      mda="Revenue rose.", risk_factors="Export controls could hurt us.")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2025-12-12", debt_notes=list(debt_notes),
                        exposure_notes=list(exposure_notes))


def test_exposure_note_is_its_own_segment_after_the_debt_notes():
    debt = StatementNote(form="10-K", accession="acc", title="Debt", label="10-K note: Debt",
                         text="ladder text here")
    labels = [label for label, _ in _bundle([_xnote()], [debt]).segments()]
    assert labels[-2:] == ["10-K note: Debt", "10-K note: Industry Segment Operations"]


def test_prompt_carries_the_exposure_note_with_truncation_in_the_header_only():
    p = _build_user_prompt(_bundle([_xnote(truncated=True)]), {"research": {}})
    assert ("=== 10-K STATEMENT NOTE — Industry Segment Operations (TRUNCATED — this note "
            "continues beyond what is shown) ===\n" + SEG) in p


def test_prompt_without_exposure_notes_matches_a_bundle_that_never_had_them():
    """Disabled or absent exposure notes leave the user prompt byte-identical to a
    bundle without the attribute at all."""
    bundle = _bundle()

    class _Legacy:
        def __init__(self, b):
            self.__dict__.update({k: v for k, v in vars(b).items() if k != "exposure_notes"})
    legacy = _Legacy(bundle)
    assert not hasattr(legacy, "exposure_notes")
    assert _build_user_prompt(bundle, {"research": {}}) == _build_user_prompt(
        legacy, {"research": {}})
    assert "STATEMENT NOTE" not in _build_user_prompt(bundle, {"research": {}})


def test_prompt_states_the_exposures_ceiling():
    assert "5 exposures" in _build_user_prompt(_bundle(), {"research": {}})
    assert "2 exposures" in _build_user_prompt(_bundle(), {"research": {"max_exposures": 2}})


def test_system_prompt_names_the_destination_field_and_the_quote_rules():
    assert '"exposures"' in SCHEMA_HINT
    assert "EXPOSURES map" in SYSTEM_PROMPT
    assert "'added_risks', 'exposures' and 'reconciliation'" in SYSTEM_PROMPT
    assert "'added_risks' and 'exposures' an empty 'evidence' is never valid" in SYSTEM_PROMPT
    assert "never in 'evidence'" in SYSTEM_PROMPT


def test_fetch_bundle_carries_exposure_notes_without_moving_the_cache_key(monkeypatch):
    tenk = FilingText("AMAT", "0001-25-000001", "2025-12-12",
                      business="b", mda="m", risk_factors="r")
    monkeypatch.setattr(filings, "_prior_year_sections", lambda *a, **k: ("", ""))
    import edgar

    from shortlist.research import eightk
    monkeypatch.setattr(eightk, "fetch_eightks", lambda *a, **k: [])
    monkeypatch.setattr(edgar, "Company",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))

    class _Obj:
        notes = _Notes([_Note("Borrowing Facilities and Debt", "ladder"),
                        _Note("Industry Segment Operations", SEG)])

    monkeypatch.setattr(filings, "_fetch_10k_parsed",
                        lambda *a, **k: (tenk, _Obj(), None, None))
    b = filings.fetch_bundle("AMAT")
    assert b.cache_key == "0001-25-000001"
    assert [n.title for n in b.debt_notes] == ["Borrowing Facilities and Debt"]
    assert [n.title for n in b.exposure_notes] == ["Industry Segment Operations"]
    off = filings.fetch_bundle("AMAT", config={"research": {"exposure_notes": {"enabled": False}}})
    assert off.exposure_notes == []


# ------------------------------------------------------------------------- schema

def _payload(**extra):
    base = {"business_model_summary": "b", "moat": {"summary": "m"}, "risks": [],
            "red_flags": [], "management_capital_allocation": "c",
            "thesis": {"bull_case": "", "bear_case": "", "takeaway": ""}}
    return {**base, **extra}


def _parse(payload, **kw):
    return assessment_from_payload(payload, ticker="AMAT", as_of="t", accession="acc",
                                   filing_date="d", model="m", cost_usd=None,
                                   stop_reason=None, **kw)


def test_parse_keeps_valid_kinds_and_folds_case_and_aliases():
    a = _parse(_payload(exposures=[
        {"kind": "Geography", "claim": "c1", "evidence": "e1"},
        {"kind": "customers", "claim": "c2", "evidence": "e2"},
        {"kind": "FX", "claim": "c3", "evidence": "e3"},
    ]))
    assert [(e.kind, e.claim, e.evidence) for e in a.exposures] == [
        ("geography", "c1", "e1"), ("customer", "c2", "e2"), ("currency", "c3", "e3")]


def test_parse_drops_unknown_kinds_and_malformed_items():
    a = _parse(_payload(exposures=[
        {"kind": "weather", "claim": "x", "evidence": "y"},
        {"claim": "no kind", "evidence": "y"},
        "a bare string",
        {"kind": "supplier", "claim": "ok", "evidence": "q"},
    ]))
    assert [e.kind for e in a.exposures] == ["supplier"]


def test_parse_tolerates_a_missing_or_non_list_field():
    assert _parse(_payload()).exposures == []
    assert _parse(_payload(exposures="none")).exposures == []
    assert _parse(_payload(exposures={"kind": "customer"})).exposures == []


def test_parse_enforces_the_ceiling():
    items = [{"kind": k, "claim": k, "evidence": k} for k in EXPOSURE_KINDS]
    assert len(_parse(_payload(exposures=items)).exposures) == 5
    assert len(_parse(_payload(exposures=items), max_exposures=2).exposures) == 2
    assert _parse(_payload(exposures=items), max_exposures=0).exposures == []


# ------------------------------------------------------------------- verification

def _assessment(exposures=(), risks=()):
    a = QualitativeAssessment(ticker="AMAT", as_of="t", filing_accession="acc",
                              filing_date="d", model="m")
    a.exposures = list(exposures)
    a.risks = list(risks)
    return a


def test_an_exposure_quoted_from_the_note_verifies_against_the_note():
    a = _assessment([Exposure(kind="customer", claim="two customers are 34%",
                              evidence="two customers accounted for approximately 19% and "
                                       "15%, respectively, of our net revenue")])
    _verify_grounding(a, _bundle([_xnote()]))
    assert a.exposures[0].verified is True
    assert a.exposures[0].source == "10-K note: Industry Segment Operations"
    assert a.unverified_count == 0


def test_a_table_row_copied_exactly_verifies():
    a = _assessment([Exposure(kind="geography", claim="China $8.5B of $28.4B = 30%",
                              evidence="| China | 8,529 | 10,117 | 7,247 |")])
    _verify_grounding(a, _bundle([_xnote()]))
    assert a.exposures[0].verified is True


@pytest.mark.parametrize("evidence", [
    "",                                                          # empty is not inference here
    "China accounted for 30% of net revenue",                    # a derived figure as a quote
    "Export controls could hurt us. | China | 8,529",            # stitched across segments
])
def test_unlocatable_evidence_is_unverified_and_counted(evidence):
    a = _assessment([Exposure(kind="geography", claim="c", evidence=evidence)])
    _verify_grounding(a, _bundle([_xnote()]))
    assert a.exposures[0].verified is False
    assert a.exposures[0].source == ""
    assert a.unverified_count == 1
    assert a.inference_count == 0


def test_exposures_and_risks_share_the_unverified_count():
    a = _assessment([Exposure(kind="customer", claim="c", evidence="fabricated quote text")],
                    [Finding(claim="r", evidence="another fabricated quote")])
    _verify_grounding(a, _bundle([_xnote()]))
    assert a.unverified_count == 2


# ---------------------------------------------------------------- assess() end-to-end

def test_assess_parses_verifies_and_applies_the_configured_ceiling():
    quote = ("two customers accounted for approximately 19% and 15%, respectively, "
             "of our net revenue")
    payload = _payload(exposures=[
        {"kind": "customer", "claim": "two customers = 34%", "evidence": quote},
        {"kind": "geography", "claim": "China", "evidence": "| China | 8,529 | 10,117 | 7,247 |"},
        {"kind": "government", "claim": "dropped by the ceiling", "evidence": quote},
    ])
    seen = {}

    def runner(**kw):
        seen.update(kw)
        return CliResult(text=json.dumps(payload), cost_usd=0.1, stop_reason="end_turn",
                         model="m", error=None)

    cfg = {"research": {"max_exposures": 2, "screening_call": {"enabled": False}}}
    a = assess(None, _bundle([_xnote()]), cfg, runner=runner)
    assert [(e.kind, e.verified) for e in a.exposures] == [("customer", True),
                                                           ("geography", True)]
    assert "2 exposures" in seen["prompt"]


# ------------------------------------------------------------------------ rendering

def _rendered_assessment():
    return _assessment([
        Exposure(kind="customer", claim="Two customers are 34% of revenue",
                 evidence="two customers accounted for approximately 19%", verified=True,
                 source="10-K note: Industry Segment Operations"),
        Exposure(kind="geography", claim="China is 30%", evidence="made up", verified=False),
    ])


def test_markdown_brief_has_an_exposures_section_with_marks():
    md = to_markdown(_rendered_assessment())
    sec = md.split("## Exposures (filing-grounded)\n", 1)[1].split("\n## ", 1)[0]
    assert ("- **Customer — Two customers are 34% of revenue** _— verified against "
            "10-K note: Industry Segment Operations_") in sec
    assert "- **Geography — China is 30%** _(unverified)_" in sec


def test_markdown_brief_with_no_exposures_says_so():
    md = to_markdown(_assessment())
    assert "## Exposures (filing-grounded)\n- No material concentration identified." in md


def _record(a):
    import dataclasses
    return json.loads(json.dumps(dataclasses.asdict(a), default=str))


def test_the_record_round_trips_into_the_bot_view_model():
    vm = _assessment_vm(_record(_rendered_assessment()))
    assert [(f.claim, f.status, f.source) for f in vm.exposures] == [
        ("Customer — Two customers are 34% of revenue", "verified",
         "10-K note: Industry Segment Operations"),
        ("Geography — China is 30%", "unverified", "")]


def test_an_old_record_without_the_field_loads_and_renders():
    rec = _record(_assessment())
    del rec["exposures"]
    vm = _assessment_vm(rec)
    assert vm.exposures == []


def _vm_with(a):
    from types import SimpleNamespace
    leader = SimpleNamespace(ticker="AMAT", composite=60.0,
                             assessment=_assessment_vm(_record(a)))
    return SimpleNamespace(leaders=[leader])


def test_bot_html_and_full_text_show_exposures_with_marks():
    vm = _vm_with(_rendered_assessment())
    html = _Research().render_html(vm, HtmlBuilder())
    assert "<b>Exposures</b>" in html
    assert "Customer — Two customers are 34% of revenue" in html
    text = "\n".join(_Research().render_text(vm, Detail.FULL))
    assert "🌐 Geography — China is 30%" in text
    assert "[unverified]" in text


def test_bot_html_omits_the_block_for_an_old_record():
    rec = _record(_assessment())
    del rec["exposures"]
    from types import SimpleNamespace
    vm = SimpleNamespace(leaders=[SimpleNamespace(ticker="AMAT", composite=60.0,
                                                  assessment=_assessment_vm(rec))])
    assert "Exposures" not in _Research().render_html(vm, HtmlBuilder())


# ------------------------------------------------------------------------ cache key

def test_the_exposure_extractor_and_the_prompt_are_fingerprinted():
    assert {"notes", "assess", "models"} <= set(_PROMPT_MODULES)


@pytest.mark.parametrize("research", [
    {"max_exposures": 3},
    {"exposure_notes": {"enabled": False}},
    {"exposure_notes": {"max_chars_10k": 8000}},
])
def test_exposure_config_moves_the_brief_key(research):
    from datetime import date
    b = _bundle()
    day = date(2026, 11, 2)
    assert brief_key(b, None, config={"research": {}}, today=day) != brief_key(
        b, None, config={"research": research}, today=day)
