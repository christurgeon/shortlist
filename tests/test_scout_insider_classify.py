from datetime import date

from shortlist.scout.dera import build_trade_month_index
from shortlist.scout.insider import InsiderTxn, classify_tier


def _t(owner, d, code="S"):
    return InsiderTxn(owner_cik=owner, ticker="ZZZ", date=d, code=code,
                      shares=1.0, price=1.0, plan_10b5_1=False,
                      roles=frozenset({"officer"}))


def test_index_uses_all_transaction_codes_not_just_purchases():
    """An insider who SELLS every March is routine -- that is the noise being stripped.
    Indexing only buys would misclassify them as opportunistic."""
    txns = [_t("A", date(y, 3, 10), code="S") for y in (2022, 2023, 2024)]
    idx = build_trade_month_index(txns)
    # keys are zero-padded to 10 chars (join-key canonicalization -- see
    # build_trade_month_index's docstring), so "A" lands at "000000000A".
    assert idx["A".zfill(10)] == {(2022, 3), (2023, 3), (2024, 3)}


def test_same_month_three_consecutive_years_is_routine():
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2022, 2023, 2024)])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "routine"


def test_a_gap_year_breaks_the_routine_pattern():
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2022, 2024, 2025)])
    assert classify_tier("A", idx, as_of=date(2026, 1, 1)) == "opportunistic"


def test_three_years_of_scattered_months_is_opportunistic():
    idx = build_trade_month_index(
        [_t("A", date(2022, 2, 1)), _t("A", date(2023, 7, 1)), _t("A", date(2024, 11, 1))])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "opportunistic"


def test_insufficient_history_is_unclassified():
    idx = build_trade_month_index([_t("A", date(2024, 3, 10))])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "unclassified"
    assert classify_tier("NOBODY", idx, as_of=date(2025, 1, 1)) == "unclassified"


def test_routine_pattern_must_be_within_the_lookback():
    """A same-month streak that ended long ago must not brand a trader routine forever."""
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2015, 2016, 2017)])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "unclassified"


def test_a_lapsed_routine_streak_is_no_longer_routine():
    """docs/FORM4_INSIDER.md §6 worked example: March 2022-24 evaluated as-of 2026 is
    opportunistic, not routine. The routine window is anchored to `as_of`, not to the
    trader's own most-recent activity -- a plausible off-by-one a future refactor could
    reintroduce silently. By as_of=2026 the streak stopped a full calendar year ago
    (the strict routine window is 2023-2025), so it must not brand the trader routine
    forever."""
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2022, 2023, 2024)])
    result = classify_tier("A", idx, as_of=date(2026, 1, 1))
    assert result == "opportunistic"
    assert result != "routine"


def test_cik_zero_padding_does_not_break_the_join():
    """owner_cik is the join key between the live XML path and the DERA history index. If
    the two sides ever disagree on zero-padding, every lookup misses and every insider
    silently classifies as unclassified (still emits, just at lower strength -- so the
    failure would go unnoticed rather than error loudly). Canonicalize on both sides of the
    join so this can't happen even if a future caller mixes padded/unpadded CIKs."""
    idx = build_trade_month_index([_t("2021774", date(y, 3, 10)) for y in (2022, 2023, 2024)])
    assert classify_tier("0002021774", idx, as_of=date(2025, 1, 1)) == "routine"

    idx2 = build_trade_month_index(
        [_t("0002021774", date(y, 3, 10)) for y in (2022, 2023, 2024)])
    assert classify_tier("2021774", idx2, as_of=date(2025, 1, 1)) == "routine"
