from shortlist.scout.cik_tickers import build_cik_to_ticker, resolve_ticker


def _raw(rows):
    # company_tickers.json shape: {"0": {"cik_str": int, "ticker": str, "title": str}, ...}
    return {str(i): {"cik_str": c, "ticker": t, "title": t} for i, (c, t) in enumerate(rows)}


def test_first_occurrence_is_authoritative():
    # Common stock listed first must win over a later warrant/unit/right sibling.
    raw = _raw([(2088626, "PECE"), (2088626, "PECEU"), (2088626, "PECER"), (2088626, "PECEW")])
    idx = build_cik_to_ticker(raw)
    assert resolve_ticker("0002088626", idx) == "PECE"
    assert resolve_ticker(2088626, idx) == "PECE"   # int + padded resolve identically


def test_never_prefers_foreign_or_preferred_sibling_over_first():
    # The blanket-suffix bug: EQNR (first/common) must NOT lose to STOHF (a *F pink sibling).
    raw = _raw([(1234567, "EQNR"), (1234567, "STOHF")])
    assert resolve_ticker(1234567, build_cik_to_ticker(_raw([(1234567, "EQNR"), (1234567, "STOHF")]))) == "EQNR"
    # And a preferred sibling never displaces the common.
    raw2 = _raw([(70858, "BAC"), (70858, "BAC-PB")])
    assert resolve_ticker(70858, build_cik_to_ticker(raw2)) == "BAC"


def test_sibling_relative_backstop_only():
    # If a unit/warrant is (wrongly) first AND its base is also a ticker of the SAME cik,
    # prefer the base. BAYAU -> BAYA because BAYA exists for that cik.
    raw = _raw([(999001, "BAYAU"), (999001, "BAYA"), (999001, "BAYAR")])
    assert resolve_ticker(999001, build_cik_to_ticker(raw)) == "BAYA"


def test_never_rejects_sole_ticker_even_if_suffixed():
    # LW is a legitimate common ticker ending in W; with no sibling, keep it.
    raw = _raw([(1679273, "LW")])
    assert resolve_ticker(1679273, build_cik_to_ticker(raw)) == "LW"


def test_unmapped_cik_returns_none():
    assert resolve_ticker(424242, build_cik_to_ticker(_raw([(1, "AAA")]))) is None
