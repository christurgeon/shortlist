import pytest

from shortlist.research.riskdiff import added_risk_blocks

CFG = {"research": {"risk_diff": {"similarity_threshold": 0.5, "max_blocks": 4,
                                  "max_chars": 12000}}}

PRIOR = (
    "Competition risk.\nWe face intense competition in our markets.\n\n"
    "Supply chain risk.\nWe depend on a limited number of suppliers.\n"
)

def test_identical_sections_yield_nothing():
    assert added_risk_blocks(PRIOR, PRIOR, CFG) == ""

def test_genuinely_new_block_is_flagged():
    current = PRIOR + ("\n\nCybersecurity risk.\nA breach of our systems could "
                       "materially harm our business and reputation.\n")
    out = added_risk_blocks(current, PRIOR, CFG)
    assert "Cybersecurity risk." in out
    assert "Competition risk." not in out          # carried over, not new

def test_cosmetic_number_change_is_not_flagged():
    # same risk, only a year/dollar figure changed -> NOT "new"
    prior = "Litigation risk.\nWe recorded $12.0 million of legal expense in 2023.\n"
    current = "Litigation risk.\nWe recorded $14.5 million of legal expense in 2024.\n"
    assert added_risk_blocks(current, prior, CFG) == ""

def test_reordered_blocks_are_not_flagged():
    reordered = ("Supply chain risk.\nWe depend on a limited number of suppliers.\n\n"
                 "Competition risk.\nWe face intense competition in our markets.\n")
    assert added_risk_blocks(reordered, PRIOR, CFG) == ""

@pytest.mark.parametrize("cap", [0, 2])
def test_max_blocks_cap_respected(cap):
    extra = "".join(f"\n\nNovel risk {i}.\nUnique unprecedented exposure number {i}.\n"
                    for i in range(10))
    cfg = {"research": {"risk_diff": {"similarity_threshold": 0.5, "max_blocks": cap,
                                      "max_chars": 12000}}}
    out = added_risk_blocks(PRIOR + extra, PRIOR, cfg)
    assert out.count("Novel risk") == cap

def test_max_chars_cap_respected():
    big = "\n\nHuge risk.\n" + ("x" * 50000)
    cfg = {"research": {"risk_diff": {"similarity_threshold": 0.5, "max_blocks": 4,
                                      "max_chars": 100}}}
    out = added_risk_blocks(PRIOR + big, PRIOR, cfg)
    assert len(out) <= 100

def test_empty_inputs_are_safe():
    assert added_risk_blocks("", PRIOR, CFG) == ""
    assert added_risk_blocks(PRIOR, "", CFG) == ""

def test_a_block_split_at_a_different_point_is_not_flagged():
    # A page break moves the paragraph boundary, so the tail block starts mid-sentence
    # and its prefix key matches nothing in the prior year.
    para = ("Regulation risk.\nChanges in laws governing our products could require us to "
            "reformulate them and increase our costs. Regulators have proposed new labelling "
            "rules in several of our largest markets, and consumer groups have petitioned for "
            "warnings on sweetened drinks. Public concern about certain types of caramel "
            "coloring used in cola beverages, or substances used in packaging materials such "
            "as bisphenol, could reduce demand for our brands and expose us to litigation.\n")
    prior = PRIOR + "\n\n" + para
    current = PRIOR + "\n\n" + para.replace("Public concern about ", "Public concern about\n\n")
    assert added_risk_blocks(current, prior, CFG) == ""


def test_a_short_page_break_tail_is_not_flagged():
    para = ("Regulation risk.\nPublic concern about substances used in packaging materials, "
            "including recent studies by several academic groups, could lead regulators to "
            "restrict them, which would raise our costs and impair consumer sentiment toward "
            "our brands and sparkling flavored products.\n")
    current = PRIOR + "\n\n" + para.replace("toward ", "toward\n\n")
    assert added_risk_blocks(current, PRIOR + "\n\n" + para, CFG) == ""


def test_a_partly_rewritten_risk_is_still_flagged():
    old = ("Trade risk.\nTariffs and trade restrictions imposed by governments could increase "
           "the cost of our components and reduce demand for our products in affected "
           "markets, which could adversely affect our results of operations.\n")
    new = ("Since the second quarter, new tariffs were announced on imports from several "
           "countries and remain subject to change. " + old.split("\n", 1)[1])
    out = added_risk_blocks(PRIOR + "\n\n" + new, PRIOR + "\n\n" + old, CFG)
    assert "new tariffs were announced" in out
