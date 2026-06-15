from shortlist.data.entity_match import normalize_name, match_confidence


def test_normalize_strips_suffixes_and_case():
    assert normalize_name("LOCKHEED MARTIN CORPORATION") == "lockheed martin"
    assert normalize_name("Alphabet Inc.") == "alphabet"
    assert normalize_name("The Boeing Co") == "boeing"


def test_exact_and_suffix_variants_match():
    assert match_confidence("LOCKHEED MARTIN CORP", "Lockheed Martin Corporation") >= 0.95


def test_unrelated_low_confidence():
    assert match_confidence("Lockheed Martin Corporation", "Lockheed Martin Federal Credit Union") < 0.85
    assert match_confidence("Microsoft Corporation", "Micro Systems Inc") < 0.85


def test_empty_is_zero():
    assert match_confidence("", "Anything") == 0.0
    assert match_confidence("Anything", "") == 0.0
