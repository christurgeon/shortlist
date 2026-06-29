from shortlist.scout.quality import (is_initial_13d, is_spac_or_shell,
                                     is_affiliate_filing, marquee_activist)


def test_is_initial_13d():
    assert is_initial_13d("SCHEDULE 13D")
    assert is_initial_13d("sc 13d")
    assert not is_initial_13d("SCHEDULE 13D/A")
    assert not is_initial_13d("SC 13D/A")
    assert not is_initial_13d("SCHEDULE 13G")
    assert not is_initial_13d("")


def test_is_spac_or_shell():
    assert is_spac_or_shell("Peace Acquisition Corp.")
    assert is_spac_or_shell("Some Blank Check Company")
    assert not is_spac_or_shell("BBB FOODS INC")
    assert not is_spac_or_shell("GENCO SHIPPING & TRADING LTD")


def test_is_affiliate_filing_detects_name_overlap():
    # Hawkeye HoldCo LLC filing on Hawkeye Systems = affiliate (shared distinctive token).
    assert is_affiliate_filing("Hawkeye HoldCo LLC", "Hawkeye Systems, Inc.")
    # Generic corporate tokens alone must NOT trigger: both names literally share
    # "Capital"/"LP"/"LLC", but those are stripped, so no distinctive overlap remains.
    assert not is_affiliate_filing("Acme Capital LP", "Beta Capital LLC")
    assert not is_affiliate_filing("Starboard Value LP", "Acme Industries Inc")
    # A real outside activist does not overlap its target.
    assert not is_affiliate_filing("Elliott Investment Management L.P.", "Phillips 66")


def test_marquee_activist_alias_map():
    assert marquee_activist("Elliott Investment Management L.P.") == "Elliott"
    assert marquee_activist("Elliott Associates, L.P.") == "Elliott"
    assert marquee_activist("Carl C. Icahn") == "Icahn"
    assert marquee_activist("Icahn Partners LP") == "Icahn"
    assert marquee_activist("Joe Random Capital LLC") is None
