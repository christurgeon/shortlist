"""Finnhub reports `marketCapitalization` in the issuer's NATIVE currency.

Found 2026-08-06 by following the selection ledger: `TSM` was recorded at
**$63.9 trillion**. Finnhub returns 60,163,096 for TSM with `currency: "TWD"` — millions of
Taiwan dollars — which the normalizer converted straight to absolute "USD".

This is not cosmetic. `market_cap` is the denominator of the insider net-flow ratio and the
input to the `below_min_mktcap` hard gate, and CLAUDE.md states both "assume dollars". Worse,
`scoring.py` only trips that gate when the cap is BELOW the floor, so an inflated cap
silently PASSES: a genuinely small foreign issuer in a weak currency (TWD ~32/USD, JPY ~150,
KRW ~1300) gets a 30-1000x overstated cap and clears the $2B microcap gate it should have
tripped — quietly favouring exactly the foreign nano-caps the composition audit complains of.

Abstaining is the fix, not conversion: the repo has no FX source, and abstain-never-guess is
the standing rule. FMP outranks Finnhub in the merge, so a USD figure still wins where FMP
has one; only the FMP-gated foreign case loses the cap (and with it the insider ratio),
which is strictly better than corrupting a hard gate with a 32x-wrong number.
"""
from shortlist.data.sources.finnhub import _normalize_finnhub


def _raw(cap, currency):
    p = {"name": "X", "marketCapitalization": cap, "exchange": "E", "country": "C"}
    if currency is not None:
        p["currency"] = currency
    return {"profile": p}


def test_usd_market_cap_is_converted_from_millions():
    snap = _normalize_finnhub("AAPL", _raw(4538789.95, "USD"))
    assert snap.profile.market_cap == 4538789.95 * 1e6
    assert snap.profile.currency == "USD"


def test_a_non_usd_market_cap_ABSTAINS_rather_than_being_read_as_dollars():
    """TSM: 60,163,096 millions TWD is ~$1.9T USD, not $60.2T."""
    snap = _normalize_finnhub("TSM", _raw(60163096.4, "TWD"))
    assert snap.profile.market_cap is None
    assert snap.profile.currency == "TWD"      # the currency itself is still reported


def test_eur_abstains_too_even_though_the_error_would_be_small():
    """ASML is only ~10% overstated in EUR, but a silent 10% error on a gate input is still
    a wrong number, and 'small enough to ignore' is not a rule anyone can apply later."""
    assert _normalize_finnhub("ASML", _raw(562510.8, "EUR")).profile.market_cap is None


def test_absent_currency_abstains():
    """Cannot verify the unit -> cannot use the number. Finnhub populates `currency` for
    every live US-listed name checked (AAPL/NBIS/TSM/ASML), so this costs nothing in
    practice and refuses to guess when it would."""
    assert _normalize_finnhub("X", _raw(1234.5, None)).profile.market_cap is None


def test_currency_check_is_case_insensitive_and_whitespace_tolerant():
    assert _normalize_finnhub("X", _raw(100.0, " usd ")).profile.market_cap == 100.0 * 1e6


def test_absent_market_cap_stays_none_regardless_of_currency():
    assert _normalize_finnhub("X", _raw(None, "USD")).profile.market_cap is None
