"""Tests for the FINRA short-interest discovery originator (scout).

The pure aggregator `short_interest_jumps_from_rows` is the heart: raw FINRA rows ->
Emissions for names whose short interest jumped, filtered to a non-extreme crowding band.
See docs/superpowers/specs/2026-06-29-finra-short-interest-originator-design.md.
"""
from datetime import date

import pytest

from shortlist.data import finra as finra_leaf
from shortlist.scout.short_interest import (
    fetch_short_interest_rows,
    short_interest_jumps_from_rows,
)


def _row(symbol="ABCD", *, current=4_600_000, prev=3_100_000, adv=800_000,
         dtc=6.2, split="N", revised="N", settlement="2026-06-15"):
    """A FINRA ConsolidatedShortInterest row (raw upstream field names)."""
    return {
        "symbolCode": symbol,
        "settlementDate": settlement,
        "currentShortPositionQuantity": None if current is None else str(current),
        "previousShortPositionQuantity": None if prev is None else str(prev),
        "averageDailyVolumeQuantity": None if adv is None else str(adv),
        "daysToCoverQuantity": None if dtc is None else str(dtc),
        "stockSplitFlag": split,
        "revisionFlag": revised,
    }


def test_material_jump_in_band_emits_one_discovery_candidate():
    ems = short_interest_jumps_from_rows([_row()], "2026-06-15")
    assert len(ems) == 1
    e = ems[0]
    assert e.ticker == "ABCD"
    assert e.signal == "finra:short_interest_jump"
    assert e.is_discovery is True
    # strength = base(0.35) + w_jump(0.35) * min(1, jump_pct/jump_ref=4.0); jump_pct ~= 0.484
    assert 0.38 < e.strength < 0.41
    assert "48%" in e.evidence and "6.2" in e.evidence


# --- jump magnitude + division guards (scan must never raise) ----------------

def test_jump_below_threshold_rejected():
    # +6.5% rise is real but below min_jump_pct (0.25): noise, not a candidate.
    assert short_interest_jumps_from_rows([_row(current=3_300_000, prev=3_100_000)],
                                          "2026-06-15") == []


def test_zero_prev_short_skipped_without_raising():
    # A brand-new short position (prev=0) has no relative jump; skip, never ZeroDivide.
    assert short_interest_jumps_from_rows([_row(current=1_000_000, prev=0)],
                                          "2026-06-15") == []


def test_none_prev_short_skipped():
    assert short_interest_jumps_from_rows([_row(prev=None)], "2026-06-15") == []


def test_zero_prev_never_divides_even_with_zero_floor():
    # Even with a pathological min_prev_short_shares=0 config, prev=0 must skip — the
    # "never raises on a bad row" invariant can't depend on the floor being > 0.
    assert short_interest_jumps_from_rows(
        [_row(current=1_000_000, prev=0)], "x", min_prev_short_shares=0) == []


def test_none_current_short_skipped():
    assert short_interest_jumps_from_rows([_row(current=None)], "2026-06-15") == []


def test_non_finite_numeric_strings_skipped_without_raising():
    # A pathological "inf"/"nan" numeric string must not slip past the guards and blow up
    # round(jump*100) — the aggregator's "never raises on a bad row" invariant.
    for bad in ("inf", "Infinity", "nan"):
        assert short_interest_jumps_from_rows([_row(current=bad)], "x") == [], bad


def test_negative_prev_short_skipped():
    # Malformed negative prior must not invert the sign into a phantom jump.
    assert short_interest_jumps_from_rows([_row(current=1_000_000, prev=-500_000)],
                                          "2026-06-15") == []


# --- corporate-action / sentinel guards --------------------------------------

def test_split_flag_row_dropped():
    # A split inflates the share-count jump spuriously (mirrors the bridge's not-split gate).
    assert short_interest_jumps_from_rows([_row(split="Y")], "2026-06-15") == []


def test_revision_flag_kept():
    # A restatement is not a corporate-action artifact — keep it.
    ems = short_interest_jumps_from_rows([_row(revised="Y")], "2026-06-15")
    assert len(ems) == 1


def test_dtc_zero_volume_sentinel_dropped():
    # FINRA's 999.99 zero-volume sentinel must NOT rank as maximally squeezable.
    assert short_interest_jumps_from_rows([_row(dtc=999.99)], "2026-06-15") == []


# --- the non-extreme crowding band (floors AND ceilings) ---------------------

def test_dtc_below_band_rejected():
    assert short_interest_jumps_from_rows([_row(dtc=2.0)], "2026-06-15") == []


def test_dtc_above_band_rejected():
    # Exclude the extreme-DTC falling-knife tail (Hong et al 2016) — a ceiling, not a floor.
    assert short_interest_jumps_from_rows([_row(dtc=15.0)], "2026-06-15") == []


def test_jump_off_extreme_prior_base_rejected():
    # prior_dtc = prev/adv = 3.1M/200k = 15.5 > max_prior_dtc: already-crowded structural
    # short adding more, NOT a fresh low->elevated jump (Cohen-Diether-Malloy).
    assert short_interest_jumps_from_rows([_row(prev=3_100_000, adv=200_000, dtc=6.0)],
                                          "2026-06-15") == []


# --- liquidity floor + symbol shape (OTC/derivative junk) --------------------

def test_low_liquidity_rejected():
    # adv 50k < min_avg_daily_volume; prior_dtc kept small (100k/50k=2) to isolate the floor.
    assert short_interest_jumps_from_rows(
        [_row(current=200_000, prev=100_000, adv=50_000, dtc=5.0)], "2026-06-15") == []


def test_non_common_stock_symbol_dropped():
    # A 6-char / non-alpha symbol is a warrant/unit/derivative, not common stock.
    assert short_interest_jumps_from_rows([_row(symbol="ABCDEF")], "2026-06-15") == []
    assert short_interest_jumps_from_rows([_row(symbol="ABC.WS")], "2026-06-15") == []


def test_fifth_letter_security_suffix_symbols_dropped():
    # 5-letter symbols whose 5th letter is a security-type code are NOT US common stock:
    # F=foreign ordinary (the live *F OTC junk), Y=ADR, W=warrant, U=unit, R=rights, Q=bankruptcy.
    for sym in ("AWMLF", "ABCDY", "ABCDW", "ABCDU", "ABCDR", "ABCDQ"):
        assert short_interest_jumps_from_rows([_row(symbol=sym)], "2026-06-15") == [], sym
    # ...but real ≤4-char tickers and legit 5-letter commons (class shares) are KEPT.
    assert short_interest_jumps_from_rows([_row(symbol="WOOF")], "2026-06-15")   # 4-char, ends F
    assert short_interest_jumps_from_rows([_row(symbol="CMCSA")], "2026-06-15")  # 5-char common
    assert short_interest_jumps_from_rows([_row(symbol="GOOGL")], "2026-06-15")


def test_deny_list_excludes_etfs_and_funds():
    # FINRA short interest covers ETFs/CEFs (e.g. VXUS); the deny_list drops known ones
    # (scorer abstention is the backstop for the long tail). Case/separator-insensitive.
    assert short_interest_jumps_from_rows([_row(symbol="VXUS")], "x", deny_list=["VXUS"]) == []
    assert short_interest_jumps_from_rows([_row(symbol="VXUS")], "x", deny_list=["vxus"]) == []
    assert short_interest_jumps_from_rows([_row(symbol="ABCD")], "x", deny_list=["VXUS"])  # kept


def test_tiny_prior_base_ramp_rejected():
    # prev ~14k shares -> a +43000% "jump" is an economic from-zero ramp, not a real jump.
    # Guard with an absolute prior-base floor (min_prev_short_shares).
    assert short_interest_jumps_from_rows(
        [_row(symbol="FTGC", current=6_100_000, prev=14_000, adv=2_000_000, dtc=4.4)],
        "2026-06-15") == []


def test_strength_spreads_across_jump_magnitudes():
    # jump_ref=4.0 means typical 100-300% jumps DON'T all saturate -> top_n is meaningful.
    small = short_interest_jumps_from_rows(
        [_row(current=2_000_000, prev=1_000_000, adv=2_000_000, dtc=5.0)], "x")  # +100%
    big = short_interest_jumps_from_rows(
        [_row(current=4_000_000, prev=1_000_000, adv=2_000_000, dtc=5.0)], "x")  # +300%
    assert small[0].strength < big[0].strength
    assert big[0].strength < 0.70   # +300% still below the +400% saturation point


# --- strength shaping + ranking ----------------------------------------------

def test_strength_saturates_on_large_jump_and_ignores_extreme_dtc():
    # jump >= jump_ref saturates the jump term -> base + w_jump = 0.70 (DTC never adds).
    ems = short_interest_jumps_from_rows(
        [_row(current=10_000_000, prev=1_000_000, adv=2_000_000, dtc=5.0)], "2026-06-15")
    assert ems[0].strength == pytest.approx(0.70, abs=1e-9)


def test_top_n_caps_and_orders_by_strength():
    syms = ["AAA", "AAB", "AAC", "AAD", "AAE"]
    rows = [_row(symbol=s, current=1_000_000 + i * 200_000, prev=1_000_000,
                 adv=2_000_000, dtc=5.0) for i, s in enumerate(syms, start=1)]
    ems = short_interest_jumps_from_rows(rows, "2026-06-15", top_n=2)
    assert len(ems) == 2
    # highest jump first (strength descending)
    assert ems[0].strength >= ems[1].strength


# --- sync fetcher: shares the harness FinraSource cache, writes the FULL row set --------

def _fake_partitions(_timeout):
    return {"availablePartitions": [{"partitions": ["2026-06-15"]}]}


def test_fetch_caches_full_unfiltered_rows(tmp_path):
    # A row the AGGREGATOR would filter (6-char symbol) MUST still be cached, so the async
    # harness FinraSource reading the same file sees every symbol (the crowded_short flag).
    junk = _row(symbol="ZZZZZZ")
    good = _row(symbol="ABCD")
    calls = {"pages": 0}

    def fake_pages(settlement, _timeout):
        calls["pages"] += 1
        assert settlement == "2026-06-15"
        return [good, junk]

    rows, settlement = fetch_short_interest_rows(
        cache_dir=str(tmp_path), _fetch_partitions=_fake_partitions, _fetch_pages=fake_pages)
    assert settlement == "2026-06-15"
    idx = finra_leaf.index_rows(rows)
    assert "ZZZZZZ" in idx and "ABCD" in idx          # full, unfiltered

    # warm re-read hits the on-disk cache (no second page fetch) and still holds the full set
    rows2, _ = fetch_short_interest_rows(
        cache_dir=str(tmp_path), _fetch_partitions=_fake_partitions, _fetch_pages=fake_pages)
    assert calls["pages"] == 1
    assert "ZZZZZZ" in finra_leaf.index_rows(rows2)


def test_fetch_no_partition_returns_empty(tmp_path):
    rows, settlement = fetch_short_interest_rows(
        cache_dir=str(tmp_path),
        _fetch_partitions=lambda _t: {"availablePartitions": []},
        _fetch_pages=lambda s, t: [])
    assert rows == [] and settlement is None


# --- the signal class: cadence gate + graceful degradation -------------------

def _patch_fetch(monkeypatch, rows, settlement):
    import shortlist.scout.short_interest as si_mod
    monkeypatch.setattr(si_mod, "fetch_short_interest_rows",
                        lambda cache_dir=".cache/finra", timeout=30.0: (rows, settlement))


def test_signal_emits_on_a_fresh_cycle(monkeypatch):
    from shortlist.scout.signals import FinraShortInterestSignal
    _patch_fetch(monkeypatch, [_row()], "2026-06-15")
    sig = FinraShortInterestSignal(last_settlement=None)
    ems = sig.scan(date(2026, 6, 20))
    assert len(ems) == 1 and ems[0].signal == "finra:short_interest_jump"
    assert sig.settlement == "2026-06-15"            # exposed so daily.py can persist it
    assert sig.available()[0] is True


def test_signal_suppresses_an_already_processed_cycle(monkeypatch):
    from shortlist.scout.signals import FinraShortInterestSignal
    _patch_fetch(monkeypatch, [_row()], "2026-06-15")
    sig = FinraShortInterestSignal(last_settlement="2026-06-15")
    assert sig.scan(date(2026, 6, 20)) == []         # same cycle -> no re-emission
    assert "already processed" in sig.available()[1]


def test_signal_degrades_on_fetch_error_without_raising(monkeypatch):
    import shortlist.scout.short_interest as si_mod
    from shortlist.scout.signals import FinraShortInterestSignal

    def boom(cache_dir=".cache/finra", timeout=30.0):
        raise RuntimeError("finra 503 https://api.finra.org/data?apikey=SECRET")

    monkeypatch.setattr(si_mod, "fetch_short_interest_rows", boom)
    sig = FinraShortInterestSignal()
    assert sig.scan(date(2026, 6, 20)) == []
    ok, detail = sig.available()
    assert ok is False
    assert "SECRET" not in detail                    # error string is redacted


def test_signal_is_registered():
    from shortlist.scout.signals import build_signals
    (sig,) = build_signals(["finra_short_interest"])
    assert sig.name == "finra_short_interest" and sig.is_discovery is True
