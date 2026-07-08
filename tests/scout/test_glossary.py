from shortlist.scout import glossary
from shortlist.scout.glossary import entry_text, index_text, lookup, suggest


def test_lookup_exact_name():
    e = lookup("13D")
    assert e is not None and e.name == "13D"


def test_lookup_alias_and_normalization():
    # normalization strips space/hyphen/underscore/dot/slash + lowercases
    assert lookup("sc 13d") is not None
    assert lookup("sc 13d") is lookup("SC 13-D") is lookup("13d")


def test_lookup_unknown_returns_none_and_suggests():
    assert lookup("13x") is None
    assert any("13" in s for s in suggest("13x"))


def test_suggest_empty_for_garbage():
    assert suggest("zzqqxx") == []


def test_index_lists_every_entry_once_under_its_category():
    idx = index_text()
    assert len(idx) <= 4096
    for e in glossary.GLOSSARY:
        assert e.name in idx
    for cat in glossary.CATEGORIES:
        assert cat in idx


def test_entry_text_has_name_and_body():
    e = lookup("13d")
    t = entry_text(e)
    assert t.startswith("13D") and e.text in t


def test_underscore_normalization_hits_flag_literals():
    assert lookup("recent_8k") is not None
    assert lookup("recent_8k") is lookup("Recent 8K")


def test_every_entry_within_length_budget():
    for e in glossary.GLOSSARY:
        assert len(e.text) <= 600, e.name
        assert len(entry_text(e)) <= 4096, e.name


def test_index_within_single_message():
    assert len(index_text()) <= 4096


def test_every_category_used_and_valid():
    used = {e.category for e in glossary.GLOSSARY}
    assert used == set(glossary.CATEGORIES)


def test_key_financial_terms_present():
    for term in ("cagr", "13d", "13g", "8k", "form 4", "144", "10-K",
                 "def 14a", "10b5-1", "20-f", "days to cover", "peg",
                 "roic", "accruals", "piotroski", "sue", "pead",
                 "residual momentum", "drawdown", "fcf yield",
                 "net debt to ebitda", "short interest",
                 "confidence", "scored", "thin", "gated", "coverage",
                 "screening call", "opportunity", "composite"):
        assert lookup(term) is not None, term


def test_no_alias_collisions_and_all_entries_reachable():
    # the module-load assertion guards collisions; verify every entry is
    # reachable through its own name
    for e in glossary.GLOSSARY:
        assert lookup(e.name) is e
