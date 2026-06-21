"""Tests for the "Lazy Prices" YoY filing-text similarity scorer (§4).

Mirrors the riskdiff test patterns: a boilerplate/number-churn false-positive
guard, identical-text and disjoint-text endpoints, and the empty-baseline case."""
from shortlist.research import textsim

# A realistic risk-factor section (mixed prose + captions).
RISK = (
    "Competition risk. We face intense competition in our markets, which could "
    "erode our margins and reduce our market share.\n\n"
    "Supply chain risk. We depend on a limited number of suppliers for key "
    "components, and disruptions could materially harm operations.\n"
)


def test_identical_text_is_max_similarity():
    sim = textsim.cosine_similarity(RISK, RISK)
    assert sim is not None and abs(sim - 1.0) < 1e-9


def test_disjoint_vocabulary_is_zero():
    assert textsim.cosine_similarity("alpha beta gamma", "delta epsilon zeta") == 0.0


def test_empty_input_returns_none():
    # No usable baseline -> None, never a fabricated "0.0 = fully rewritten".
    assert textsim.cosine_similarity("", RISK) is None
    assert textsim.cosine_similarity(RISK, "") is None
    assert textsim.cosine_similarity("   ", "1234 5678 $%^") is None


def test_boilerplate_number_and_whitespace_churn_is_not_a_change():
    # Same risk language; only a dollar figure, a fiscal year, and whitespace
    # reflow changed. Normalization must make this read as (near-)identical so it
    # does NOT trip the filing_text_change flag (the false-positive guard).
    prior = "Litigation risk. We recorded $12.0 million of legal expense in 2023."
    current = (
        "Litigation    risk.\n\nWe recorded   $14.5 million of legal\nexpense in 2024."
    )
    sim = textsim.cosine_similarity(current, prior)
    assert sim is not None and sim > 0.99


def test_caption_renumbering_is_not_a_change():
    # "Item 1.01" -> "Item 2.03" style caption-number churn must cancel out.
    prior = "Item 1.01 Material Agreement. We entered into a credit facility."
    current = "Item 2.03 Material Agreement. We entered into a credit facility."
    sim = textsim.cosine_similarity(current, prior)
    assert sim is not None and abs(sim - 1.0) < 1e-9


def test_substantive_rewrite_lowers_similarity():
    # A genuinely new paragraph added (a wholesale change) drops similarity well
    # below the default 0.7 flag threshold.
    current = RISK + (
        "\n\nCybersecurity risk. A breach of our systems could expose customer "
        "data, trigger regulatory penalties, litigation, remediation costs, and "
        "lasting reputational harm across every product line we operate."
    ) * 3
    sim = textsim.cosine_similarity(current, RISK)
    assert sim is not None and sim < 0.7


def test_combined_pools_risk_and_mda():
    # combined_similarity pools both sections; an unchanged risk section plus a
    # fully-rewritten MD&A yields an intermediate (not 1.0) similarity.
    cur_mda = "Revenue grew on strong demand; we expect continued expansion."
    pri_mda = "Sales collapsed amid recession; we anticipate further contraction declines."
    sim = textsim.combined_similarity(RISK, RISK, cur_mda, pri_mda)
    assert sim is not None and 0.0 < sim < 1.0


def test_combined_none_when_no_baseline_either_side():
    assert textsim.combined_similarity("", "", "", "") is None
