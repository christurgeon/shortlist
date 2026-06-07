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

def test_max_blocks_cap_respected():
    extra = "".join(f"\n\nNovel risk {i}.\nUnique unprecedented exposure number {i}.\n"
                    for i in range(10))
    cfg = {"research": {"risk_diff": {"similarity_threshold": 0.5, "max_blocks": 2,
                                      "max_chars": 12000}}}
    out = added_risk_blocks(PRIOR + extra, PRIOR, cfg)
    assert out.count("Novel risk") == 2

def test_max_chars_cap_respected():
    big = "\n\nHuge risk.\n" + ("x" * 50000)
    cfg = {"research": {"risk_diff": {"similarity_threshold": 0.5, "max_blocks": 4,
                                      "max_chars": 100}}}
    out = added_risk_blocks(PRIOR + big, PRIOR, cfg)
    assert len(out) <= 100

def test_empty_inputs_are_safe():
    assert added_risk_blocks("", PRIOR, CFG) == ""
    assert added_risk_blocks(PRIOR, "", CFG) == ""
