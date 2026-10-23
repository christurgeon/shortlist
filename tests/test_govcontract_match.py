from shortlist.data.govcontract_match import normalize_name, match_confidence


def test_normalize_strips_suffixes_and_case():
    assert normalize_name("LOCKHEED MARTIN CORPORATION") == "lockheed martin"
    assert normalize_name("Apple Inc.") == "apple"
    assert normalize_name("The Boeing Co") == "boeing"


def test_exact_match_high_confidence():
    assert match_confidence("LOCKHEED MARTIN CORP", "Lockheed Martin Corporation") >= 0.95


def test_unrelated_low_confidence():
    assert match_confidence("Lockheed Martin Corporation", "ZETA ASSOCIATES INC") < 0.5


def test_alias_seed_resolves_subsidiary():
    # Pratt & Whitney is a known RTX recipient; alias seed lifts it to a match.
    assert match_confidence("RTX Corporation", "PRATT & WHITNEY",
                            alias_for=("RTX",)) >= 0.85


# Labeled set: (sec_name, true recipient name as USAspending returns it, is_same)
_LABELED = [
    ("Lockheed Martin Corporation", "LOCKHEED MARTIN CORPORATION", True),
    ("Lockheed Martin Corporation", "LOCKHEED MARTIN CORP", True),
    ("Lockheed Martin Corporation", "LOCKHEED MARTIN SERVICES, LLC", True),
    ("Lockheed Martin Corporation", "RAYTHEON/LOCKHEED MARTIN JAVELIN JV", False),
    ("Lockheed Martin Corporation", "ZETA ASSOCIATES INC", False),
    ("General Dynamics Corporation", "GENERAL DYNAMICS CORPORATION", True),
    ("General Dynamics Corporation", "GENERAL DYNAMICS LAND SYSTEMS INC", True),
    ("General Dynamics Corporation", "GENERAL ELECTRIC COMPANY", False),
    ("Microsoft Corporation", "MICROSOFT CORPORATION", True),
    ("Microsoft Corporation", "MICRO SYSTEMS INC", False),
    ("Coca-Cola Company", "COCA-COLA COMPANY", True),
    ("Coca-Cola Company", "COCA COLA BOTTLING CO CONSOLIDATED", False),
]

THRESHOLD = 0.80  # the shipped config default; this test pins its measured behavior


def test_labeled_threshold_recall_precision():
    tp = fp = fn = tn = 0
    for sec, recip, same in _LABELED:
        pred = match_confidence(sec, recip) >= THRESHOLD
        tp += pred and same
        fp += pred and not same
        fn += (not pred) and same
        tn += (not pred) and not same
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    # Documented bar: no false positives (precision 1.0); recall >= 0.6.
    # If this fails, RE-TUNE THRESHOLD or normalization, then update the spec's
    # recorded recall — do NOT relax silently.
    assert precision == 1.0, f"precision {precision} (false positives present)"
    assert recall >= 0.6, f"recall {recall} below documented bar"


# (ticker, SEC title, recipient spelled as USAspending returns it, same entity?)
_SEEDED = [
    ("UNH", "UNITEDHEALTH GROUP INC", "OPTUM PUBLIC SECTOR SOLUTIONS, INC.", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "OPTUMSERVE HEALTH SERVICES, INC.", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "OPTUMRX ADMINISTRATIVE SERVICES, LLC", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "OPTUMHEALTH CARE SOLUTIONS, LLC", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "OPTUM360 LLC", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "OPTUMINSIGHT LIFE SCIENCES INC", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "CHANGE HEALTHCARE TECHNOLOGIES LLC", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "UNITED HEALTHCARE SERVICES,INC.", True),
    ("UNH", "UNITEDHEALTH GROUP INC", "PLUS ONE HOLDINGS, INC.", False),
    ("JNJ", "JOHNSON & JOHNSON", "JOHNSON & JOHNSON HEALTH CARE SYSTEMS INC.", True),
    ("JNJ", "JOHNSON & JOHNSON", "DEPUY SYNTHES SALES, INC.", True),
    ("JNJ", "JOHNSON & JOHNSON", "JANSSEN BIOTECH INC", True),
    ("JNJ", "JOHNSON & JOHNSON", "AURIS HEALTH, INC", True),
    ("JNJ", "JOHNSON & JOHNSON", "ABIOMED INC", True),
    ("JNJ", "JOHNSON & JOHNSON", "MENTOR WORLDWIDE LLC", True),
    ("JNJ", "JOHNSON & JOHNSON", "NEUWAVE MEDICAL, INC.", True),
    ("JNJ", "JOHNSON & JOHNSON", "JOHNSON CONTROLS INC", False),
    ("JNJ", "JOHNSON & JOHNSON", "JOHNSON HEALTH CARE SYSTEMS LLC", False),
    ("CVX", "CHEVRON CORP", "CHEVRON USA INC.", True),
    ("CVX", "CHEVRON CORP", "CHEVRON U.S.A. INC.", True),
    ("CVX", "CHEVRON CORP", "CHEVRON PHILLIPS CHEMICAL COMPANY LP", False),
    ("XOM", "ExxonMobil Holdings Corp", "EXXONMOBIL MARINE LIMITED", True),
    ("XOM", "ExxonMobil Holdings Corp", "EXXON MOBIL CORPORATION", True),
    ("CMCSA", "COMCAST CORP", "COMCAST BUSINESS COMMUNICATIONS, LLC", True),
    ("CMCSA", "COMCAST CORP", "COMCAST OF CALIFORNIA/COLORADO/ILLINOIS/INDIANA/MICHIGAN GP, LLC",
     True),
    ("MRK", "Merck & Co., Inc.", "MERCK SHARP & DOHME LLC", True),
    ("MRK", "Merck & Co., Inc.", "BIOMARK LLC", False),
    ("MRK", "Merck & Co., Inc.", "MERCK KGAA", False),
    ("JPM", "JPMORGAN CHASE & CO", "JPMORGAN CHASE BANK, NATIONAL ASSOCIATION", True),
    ("JPM", "JPMORGAN CHASE & CO", "J. P. MORGAN INVESTMENT MANAGEMENT INC.", True),
    ("GS", "GOLDMAN SACHS GROUP INC", "J. ARON & COMPANY LLC", True),
    ("GS", "GOLDMAN SACHS GROUP INC", "GOLDMAN SACHS ASSET MANAGEMENT, L.P", True),
    ("QCOM", "QUALCOMM INC", "QUALCOMM TECHNOLOGIES INC", True),
    ("QCOM", "QUALCOMM INC", "RESERVOIR LABS, INC.", False),
    ("DIS", "Walt Disney Co", "TWENTIETH CENTURY FOX FILM CORPORATION", True),
    ("DIS", "Walt Disney Co", "METRONOME, LLC", False),
    ("WFC", "WELLS FARGO & COMPANY", "ALLSPRING GLOBAL INVESTMENTS, LLC", False),  # divested
]


def test_seed_phrases_attribute_subsidiaries_and_reject_near_misses():
    wrong = [(tk, recip) for tk, sec, recip, same in _SEEDED
             if (match_confidence(sec, recip, alias_for=(tk,)) >= THRESHOLD) != same]
    assert not wrong


def test_seed_phrases_are_in_normalized_form():
    from shortlist.data.govcontract_match import _ALIAS_SEED
    assert all(normalize_name(p) == p for ps in _ALIAS_SEED.values() for p in ps)


def test_a_seed_phrase_matches_whole_contiguous_words():
    assert match_confidence("RTX Corporation", "WHITNEY PRATT LLC", alias_for=("RTX",)) < THRESHOLD
    assert match_confidence("Boeing Co", "BOEINGS FARM", alias_for=("BA",)) < THRESHOLD
