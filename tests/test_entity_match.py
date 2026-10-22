import pytest

from shortlist.data.entity_match import match_confidence, normalize_name, strip_sec_qualifiers


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


@pytest.mark.parametrize("title, clean", [
    ("NORTHROP GRUMMAN CORP /DE/", "NORTHROP GRUMMAN CORP"),
    ("COSTCO WHOLESALE CORP /NEW", "COSTCO WHOLESALE CORP"),
    ("WELLS FARGO & COMPANY/MN", "WELLS FARGO & COMPANY"),
    ("QUALCOMM INC/DE", "QUALCOMM INC"),
    ("Anheuser-Busch InBev SA/NV", "Anheuser-Busch InBev SA"),
    ("AIB Group plc/ADR", "AIB Group plc"),
    ("NITTO DENKO CORP / ADR/", "NITTO DENKO CORP"),
    ("AMETEK INC/", "AMETEK INC"),
    ("Core Scientific, Inc./tx", "Core Scientific, Inc."),
    ("Columbus Acquisition Corp/Cayman Islands", "Columbus Acquisition Corp"),
    ("Troops, Inc. /Cayman Islands/", "Troops, Inc."),
    ("Imperial Petroleum Inc./Marshall Islands", "Imperial Petroleum Inc."),
    ("Alight, Inc. / Delaware", "Alight, Inc."),
    ("Enthusiast Gaming Holdings Inc. / Canada", "Enthusiast Gaming Holdings Inc."),
    ("Pandora A/S / ADR", "Pandora A/S"),
    ("Rockwool A/S/ADR", "Rockwool A/S"),
])
def test_strip_sec_qualifiers_removes_trailing_state_and_form_tags(title, clean):
    assert strip_sec_qualifiers(title) == clean


@pytest.mark.parametrize("title", [
    "GENMAB A/S",                 # a single-letter tail is part of the legal name
    "Apple Inc.",
    "CULLEN/FROST BANKERS, INC.",  # internal slash, not a trailing qualifier
    "DATA I/O CORP",
    "",
])
def test_strip_sec_qualifiers_leaves_real_names_alone(title):
    assert strip_sec_qualifiers(title) == title
