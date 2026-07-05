from datetime import date

from shortlist.scout.delisting import (
    normalize_items,
    shumway_partial,
    venue_from_filer,
)


def test_normalize_items_from_string():
    # SEC submissions JSON style: comma-joined codes
    assert normalize_items("1.03,9.01") == ("1.03", "9.01")


def test_normalize_items_from_labelled_list():
    # edgartools .obj().items style: human labels
    raw = ["Item 2.01: Completion of Acquisition", "Item 5.01: Changes in Control", "Item 9.01"]
    assert normalize_items(raw) == ("2.01", "5.01", "9.01")


def test_normalize_items_degenerate_inputs():
    assert normalize_items(None) == ()
    assert normalize_items("") == ()
    assert normalize_items("no codes here") == ()
    assert normalize_items(42) == ()          # never raises on junk


def test_normalize_items_dedups_preserving_order():
    assert normalize_items("2.01,2.01,5.01") == ("2.01", "5.01")


def test_venue_from_filer():
    assert venue_from_filer("The Nasdaq Stock Market LLC") == "nasdaq"
    assert venue_from_filer("NASDAQ Stock Market LLC") == "nasdaq"
    assert venue_from_filer("New York Stock Exchange LLC") == "nyse"
    assert venue_from_filer("NYSE American LLC") == "nyse"
    assert venue_from_filer("Some Issuer Inc.") is None
    assert venue_from_filer(None) is None


def test_shumway_partial_by_venue():
    assert shumway_partial("nyse") == -0.30
    assert shumway_partial("nasdaq") == -0.55
    assert shumway_partial(None) == -0.55     # unknown venue -> harsher partial (documented)
