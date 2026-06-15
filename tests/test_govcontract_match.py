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
