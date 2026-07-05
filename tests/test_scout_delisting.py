from datetime import date

from shortlist.scout.delisting import (
    BANKRUPTCY,
    MNA,
    UNCLASSIFIED,
    _base_form,
    last_traded_close,
    normalize_items,
    shumway_partial,
    venue_from_filer,
    DelistingVerdict,
    FilingRecord,
    classify_delisting,
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


# --- regression fixtures locked by spec §12 -------------------------------------------------

def test_bbby_bankruptcy_fixture():
    # BBBY (Nasdaq): 8-K Item 1.03 2023-04-24 (Ch. 11), Form 25-NSE 2023-05-04 by Nasdaq.
    recs = [
        FilingRecord("8-K", date(2023, 4, 24), items=("1.03", "9.01")),
        FilingRecord("25-NSE", date(2023, 5, 4), filer="The Nasdaq Stock Market LLC"),
    ]
    v = classify_delisting(recs)
    assert v.reason == BANKRUPTCY
    assert v.terminal_return == -0.55            # Nasdaq venue
    assert v.delisting_date == date(2023, 5, 4)
    assert any("1.03" in e for e in v.evidence)


def test_atvi_mna_fixture():
    # ATVI: completion 8-K with 2.01+3.01+5.01 and Form 25 same day (2023-10-13, Nasdaq).
    recs = [
        FilingRecord("8-K", date(2023, 10, 13), items=("2.01", "3.01", "5.01", "9.01")),
        FilingRecord("25-NSE", date(2023, 10, 13), filer="The Nasdaq Stock Market LLC"),
    ]
    v = classify_delisting(recs)
    assert v.reason == MNA
    assert v.terminal_return == 0.0              # last close ~= deal value, NOT a penalty
    assert v.venue == "nasdaq"


def test_twtr_mna_fixture():
    # TWTR (NYSE): take-private completion 8-K 2.01+3.01+5.01, Form 25 2022-10-28 by NYSE.
    recs = [
        FilingRecord("8-K", date(2022, 10, 28), items=("2.01", "3.01", "5.01")),
        FilingRecord("25", date(2022, 10, 28), filer="New York Stock Exchange LLC"),
    ]
    v = classify_delisting(recs)
    assert v.reason == MNA
    assert v.venue == "nyse"


# --- R-B3 precedence + tails ----------------------------------------------------------------

def test_joint_codes_bankruptcy_overrides_later_mna():          # R-B3, the joint-codes fixture
    # Ch. 11 (1.03), then a post-petition asset sale 8-K (2.01+5.01) BEFORE the Form 25:
    # bankruptcy must win even though the M&A-shaped 8-K is later.
    recs = [
        FilingRecord("8-K", date(2023, 1, 10), items=("1.03",)),
        FilingRecord("8-K", date(2023, 4, 10), items=("2.01", "5.01")),
        FilingRecord("25-NSE", date(2023, 5, 1), filer="New York Stock Exchange LLC"),
    ]
    v = classify_delisting(recs)
    assert v.reason == BANKRUPTCY
    assert v.terminal_return == -0.30            # NYSE venue partial


def test_going_dark_form15_with_301_only_is_unclassified():
    recs = [
        FilingRecord("8-K", date(2022, 6, 1), items=("3.01",)),
        FilingRecord("15-12G", date(2022, 7, 15), filer="Tiny Corp"),
    ]
    v = classify_delisting(recs)
    assert v.reason == UNCLASSIFIED
    assert v.terminal_return is None


def test_mna_codes_split_across_two_filings_do_not_combine():
    # 2.01 and 5.01 in SEPARATE 8-Ks -> unclassified (same-filing is the verified pattern).
    recs = [
        FilingRecord("8-K", date(2023, 3, 1), items=("2.01",)),
        FilingRecord("8-K", date(2023, 3, 8), items=("5.01",)),
        FilingRecord("25", date(2023, 4, 1), filer="New York Stock Exchange LLC"),
    ]
    assert classify_delisting(recs).reason == UNCLASSIFIED


def test_8k_outside_window_is_ignored():
    # 1.03 fired 2 years before the Form 25 -> outside the default 365d window -> unclassified.
    recs = [
        FilingRecord("8-K", date(2021, 5, 1), items=("1.03",)),
        FilingRecord("25", date(2023, 5, 1), filer="New York Stock Exchange LLC"),
    ]
    assert classify_delisting(recs).reason == UNCLASSIFIED
    # widen the window and it classifies
    assert classify_delisting(recs, window_days=900).reason == BANKRUPTCY


def test_unknown_venue_bankruptcy_uses_harsher_partial():
    recs = [
        FilingRecord("8-K", date(2022, 6, 1), items=("1.03",)),
        FilingRecord("15-12B", date(2022, 8, 1), filer="Tiny Corp"),   # no exchange filer
    ]
    v = classify_delisting(recs)
    assert v.reason == BANKRUPTCY
    assert v.terminal_return == -0.55
