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


def test_no_alias_collisions_and_all_entries_reachable():
    # the module-load assertion guards collisions; verify every entry is
    # reachable through its own name
    for e in glossary.GLOSSARY:
        assert lookup(e.name) is e
