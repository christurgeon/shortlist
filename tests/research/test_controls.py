"""Adverse internal-control conclusion detection (research/controls.py).

The corpus fixture is 21 excerpts from REAL filings, each verified at generation
time to reproduce the verdict its whole document produces. It exists because the
failure modes here are not hypothetical — every one of them was measured on live
filings 2026-08-23 (docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md):
a prior-period conclusion restated in a later filing, a negation, a conditional risk
factor, a self-referential period, and a plural phrasing.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from shortlist.research import controls
from shortlist.research.models import ControlsFinding

_FIXTURE = Path(__file__).parent / "fixtures" / "controls_corpus.json"
_CORPUS = json.loads(_FIXTURE.read_text())
_CFG = controls.config_block(None)


def _ids(rows):
    return [f"{r['ticker']}-{r['period_end']}-{'adverse' if r['expect_adverse'] else 'clean'}"
            for r in rows]


@pytest.mark.parametrize("row", _CORPUS, ids=_ids(_CORPUS))
def test_real_filing_excerpts(row):
    got = controls.detect(row["text"], date.fromisoformat(row["period_end"]), _CFG)
    assert bool(got) is row["expect_adverse"], row["why"]


def test_the_corpus_is_not_vacuous():
    """Guards the parametrize above: a fixture that lost its positives or its
    negatives would still pass every case while testing nothing."""
    adverse = [r for r in _CORPUS if r["expect_adverse"]]
    clean = [r for r in _CORPUS if not r["expect_adverse"]]
    assert len(adverse) >= 10 and len(clean) >= 8


# --------------------------------------------------------------- the tense rule

_CONCLUSION = ("Based on that evaluation, management concluded that our internal "
               "control over financial reporting was not effective as of {d}.")


def test_conclusion_dated_to_this_period_is_adverse():
    got = controls.detect(_CONCLUSION.format(d="December 31, 2025"),
                          date(2025, 12, 31), _CFG)
    assert got is not None and got.basis == "icfr" and got.as_of == "2025-12-31"


def test_conclusion_dated_to_a_prior_period_is_not():
    """THE dominant false positive: a remediated weakness discussed a year later.
    JJSF's FY2025 10-K carries this exact shape."""
    assert controls.detect(_CONCLUSION.format(d="December 31, 2023"),
                           date(2025, 12, 31), _CFG) is None


def test_self_referential_period_counts_without_a_date():
    got = controls.detect(
        "our Chief Executive Officer concluded our disclosure controls and procedures "
        "were not effective as of the end of the period covered by this Annual Report.",
        date(2025, 12, 31), _CFG)
    assert got is not None and got.basis == "dcp"


def test_a_negation_is_not_a_finding():
    """SPGI and HMN both state there were NO material weaknesses. The adverse-
    conclusion phrasing is what discriminates; 'material weakness' alone matched
    226 of 228 filers and would fire here."""
    assert controls.detect(
        "management has concluded that our internal controls over financial reporting "
        "were effective as of December 31, 2025. There are no material weaknesses in "
        "our internal control over financial reporting.", date(2025, 12, 31), _CFG) is None


def test_a_conditional_risk_factor_is_not_a_finding():
    assert controls.detect(
        "If we identify one or more material weaknesses in our internal control over "
        "financial reporting, we will be unable to assert that our internal control "
        "over financial reporting is effective as of December 31, 2025.",
        date(2025, 12, 31), _CFG) is None


def test_a_conclusion_with_no_date_anywhere_abstains():
    assert controls.detect(
        "we concluded that our disclosure controls and procedures were not effective.",
        date(2025, 12, 31), _CFG) is None


def test_the_plural_icfr_phrasing_is_caught():
    """NSSC's FY2024 10-K is caught by this variant and by nothing else."""
    got = controls.detect(
        "management has concluded that as of June 30, 2024, the Company's internal "
        "controls over financial reporting were not effective.", date(2024, 6, 30), _CFG)
    assert got is not None and got.basis == "icfr"


def test_a_52_53_week_fiscal_end_still_matches():
    got = controls.detect(_CONCLUSION.format(d="December 28, 2025"),
                          date(2025, 12, 31), _CFG)
    assert got is not None


# --------------------------------------------------- whitespace, config, safety

def test_whitespace_normalization_is_load_bearing():
    """Raw section text puts newlines between the phrase and its date. CASH and GPK
    both flipped false-to-true on the same document once flattened, so this is a
    regression test for a real defect, not a style preference."""
    broken = _CONCLUSION.format(d="December 31, 2025").replace(" ", "\n   \n")
    assert controls.detect(broken, date(2025, 12, 31), _CFG) is not None


def test_disabled_config_is_a_no_op():
    cfg = {**_CFG, "enabled": False}
    assert controls.detect(_CONCLUSION.format(d="December 31, 2025"),
                           date(2025, 12, 31), cfg) is None


def test_absent_config_block_ships_on():
    assert controls.config_block({})["enabled"] is True
    assert controls.config_block(None)["window_chars"] == 240


def test_missing_period_or_text_abstains_rather_than_raising():
    assert controls.detect(_CONCLUSION.format(d="December 31, 2025"), None, _CFG) is None
    assert controls.detect("", date(2025, 12, 31), _CFG) is None
    assert controls.detect(None, date(2025, 12, 31), _CFG) is None


def test_a_wide_window_bleeds_which_is_why_the_default_is_not_wide():
    """Measured: the verdict is flat for window_chars 100-800 and only breaks at
    1600, where an unrelated date elsewhere in the paragraph is close enough to
    the period end to satisfy the anchor."""
    text = ("As of December 31, 2025 we completed our assessment. " + "filler. " * 90
            + "In 2019 management concluded that our internal control over financial "
              "reporting was not effective as of December 31, 2019.")
    assert controls.detect(text, date(2025, 12, 31), {**_CFG, "window_chars": 240}) is None
    assert controls.detect(text, date(2025, 12, 31), {**_CFG, "window_chars": 1600}) is not None


# ------------------------------------------------------------ what reaches the model

def test_the_quote_is_pure_filing_text():
    """The quote becomes a grounding segment, so anything computed mixed into it
    could be quoted back and pass quote-verification as a filing fact — the defect
    research/notes.py records for DebtNote's truncation marker."""
    got = controls.detect(_CONCLUSION.format(d="December 31, 2025"),
                          date(2025, 12, 31), _CFG)
    assert got.quote in _CONCLUSION.format(d="December 31, 2025")
    for derived in ("2025-12-31", "icfr", "NOT effective"):
        assert derived not in got.quote


def test_the_context_line_carries_the_verdict_and_stays_out_of_the_haystack():
    finding = ControlsFinding(form="10-K", accession="x", basis="icfr",
                              as_of="2025-12-31", label="10-K controls conclusion",
                              quote="some filing sentence.")
    line = controls.context_line(finding)
    assert "context only" in line and "2025-12-31" in line
    assert controls.context_line(None) == ""


def test_the_quote_is_bounded():
    long_text = ("management concluded that our internal control over financial "
                 "reporting was not effective as of December 31, 2025" + " and more" * 300)
    got = controls.detect(long_text, date(2025, 12, 31), {**_CFG, "max_quote_chars": 200})
    assert got is not None and len(got.quote) <= 200
