from datetime import date

from shortlist.scout.delisting import (
    _base_form,
    last_traded_close,
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


def test_base_form_strips_amendment_suffix_and_normalizes():
    assert _base_form("25-NSE/A") == "25-NSE"
    assert _base_form(" 8-k ") == "8-K"
    assert _base_form(None) == ""


def test_last_traded_close_picks_last_non_null_on_or_before_cutoff():
    dates = [date(2023, 4, 28), date(2023, 5, 1), date(2023, 5, 2), date(2023, 5, 3)]
    closes = [0.30, 0.24, None, 0.10]
    # cutoff between rows: 5/2 close is None -> falls back to 5/1
    assert last_traded_close(dates, closes, date(2023, 5, 2)) == 0.24
    # cutoff on the last row: takes it
    assert last_traded_close(dates, closes, date(2023, 5, 3)) == 0.10
    # cutoff before the series -> None
    assert last_traded_close(dates, closes, date(2023, 4, 1)) is None


def test_last_traded_close_degenerate_inputs():
    assert last_traded_close([], [], date(2023, 1, 1)) is None
    # misaligned lengths -> None (never guess a position pairing)
    assert last_traded_close([date(2023, 1, 1)], [1.0, 2.0], date(2023, 1, 1)) is None


from shortlist.scout.delisting import (
    UNCLASSIFIED,
    DelistingVerdict,
    FilingRecord,
    classify_delisting,
)


def test_no_form25_or_15_means_not_delisted():
    recs = [FilingRecord("8-K", date(2023, 1, 5), items=("1.03",)),
            FilingRecord("10-K", date(2023, 2, 1))]
    assert classify_delisting(recs) is None
    assert classify_delisting([]) is None


def test_detection_anchors_on_earliest_form25_and_takes_exchange_venue():
    recs = [
        FilingRecord("25-NSE", date(2023, 5, 4), filer="The Nasdaq Stock Market LLC"),
        FilingRecord("25-NSE/A", date(2023, 5, 10), filer="The Nasdaq Stock Market LLC"),
        FilingRecord("15-12B", date(2023, 5, 20), filer="Some Issuer Inc."),
    ]
    v = classify_delisting(recs)
    assert isinstance(v, DelistingVerdict)
    assert v.delisting_date == date(2023, 5, 4)   # earliest 25, not the /A or the 15
    assert v.venue == "nasdaq"
    assert v.reason == UNCLASSIFIED               # reasons land in the next task
    assert v.terminal_return is None


def test_form15_only_detects_with_no_venue():
    recs = [FilingRecord("15-12G", date(2022, 8, 1), filer="Tiny Corp")]
    v = classify_delisting(recs)
    assert v is not None
    assert v.delisting_date == date(2022, 8, 1)
    assert v.venue is None
