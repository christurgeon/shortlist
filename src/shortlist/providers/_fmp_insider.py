"""Shared FMP insider-transaction primitives: the buy/sell/other classification
and the shares*price value formula.

Dependency-free leaf used by the harness ``FMPSource`` — same pattern as
``_form4.py``. Edit the interpretation of FMP's Form-4-ish ``insider-trading``
rows here. ``FMPSource`` builds per-transaction ``InsiderTxn`` records but shares
these primitives so the classification has a single source of truth.

**The netting loop deliberately does NOT live here.** It lives in
``data/sources/fmp.py`` because it is inseparable from two things this leaf cannot
see: the 183-day window (matched to ``EdgarSource``'s lookback so both sources
describe the same period in ``_merge_insider``) and the abstain guard that stops a
fabricated ``net_value_6m == 0`` from winning that merge. A convenience
``net_value()`` helper used to sit here with neither, which made it a second,
subtly-divergent definition of "FMP net insider flow"; it had no production caller
and was removed 2026-08-04 rather than left as a trap.
"""


def tx_value(tx: dict) -> float:
    """Dollar value of one FMP insider transaction (shares * price).

    TOTAL BY DESIGN — returns 0.0 rather than raising on a malformed row. This is
    load-bearing, not defensive habit: `_normalize_fmp` has no try/except of its own,
    and `collector` degrades a source that raises to an errored-empty SourceResult — so
    ONE bad insider row would otherwise cost the ticker its entire FMP snapshot
    (profile, quote, statements included), not merely its insider section. Returning
    0.0 instead feeds the caller's `> 0` valued-trade predicate, which drops just that row.

    Numeric strings are COERCED (JSON APIs commonly emit numbers as strings), so a
    string-encoded row is recovered as a real trade rather than discarded; genuinely
    non-numeric values fall through to 0.0. Without the coercion, `"1000" * 10` silently
    produced a 40-character STRING that then raised on the `> 0` comparison.
    """
    try:
        return float(tx.get("securitiesTransacted") or 0) * float(tx.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_tx(tx: dict) -> str:
    """Map an FMP insider row to 'buy' | 'sell' | 'other'.

    FMP's ``transactionType`` is an ENRICHED string (``"P-Purchase"``), unlike
    edgartools' bare single-letter ``Code`` column that ``_form4.classify_code``
    parses with an exact ``== "P"`` — do NOT swap one for the other, in either
    direction. Feeding ``"P-Purchase"`` to ``classify_code`` returns ``"other"``
    and would classify every FMP transaction as a non-trade.

    ASSUMPTION, unverified against a live response: FMP emits ``<CODE>-<Description>``
    (``"P-Purchase"``, ``"A-Award"``). No recorded FMP insider payload exists in this
    repo to confirm it — ``fmp.fetch_insider`` ships false, so the endpoint is never
    called and the HTTP cache holds no sample; the test fixtures assert the same
    assumption rather than evidencing it. A dashless payload (``"Purchase"``) would
    classify as ``other``, and since an all-``other`` batch abstains, that degrades to
    "EDGAR wins the merge" rather than to wrong numbers — fail-safe, but re-check this
    against a real response before relying on FMP insider data on a paid tier.
    """
    raw = (tx.get("transactionType") or "").strip()
    code = raw.split("-", 1)[0].strip().upper()
    if code == "P":
        return "buy"
    if code == "S":
        return "sell"
    return "other"
