"""Shared FMP insider-transaction primitives: the buy/sell/other classification
and the shares*price value formula.

Dependency-free leaf used by ``FMPSource``, like ``_form4.py``. Edit the
interpretation of FMP's ``insider-trading`` rows here.

The netting loop does not live here. It lives in ``data/sources/fmp.py`` because it
depends on the 183-day window and the abstain guard, which this leaf cannot see. A
second netting helper here would diverge from that definition.
"""


def tx_value(tx: dict) -> float:
    """Dollar value of one FMP insider transaction (shares * price).

    Total by design: returns 0.0 on a malformed row instead of raising. `_normalize_fmp`
    has no try/except and `collector` degrades a raising source to an empty result, so
    one bad insider row would cost the ticker its whole FMP snapshot. The caller's
    `> 0` valued-trade predicate drops the 0.0 row.

    Numeric strings are coerced; non-numeric values give 0.0.
    """
    try:
        return float(tx.get("securitiesTransacted") or 0) * float(tx.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_tx(tx: dict) -> str:
    """Map an FMP insider row to 'buy' | 'sell' | 'other'.

    FMP's ``transactionType`` is an enriched string (``"P-Purchase"``), unlike the bare
    single-letter ``Code`` that ``_form4.classify_code`` matches with ``== "P"``. Do not
    swap them: ``classify_code("P-Purchase")`` returns ``"other"``.

    Unverified assumption: FMP emits ``<CODE>-<Description>``. No recorded payload
    exists (``fmp.fetch_insider`` ships false). A dashless payload classifies as
    ``other``; an all-``other`` batch abstains and EDGAR wins the merge, which is
    fail-safe. Re-check against a real response before relying on FMP insider data on
    a paid tier.
    """
    raw = (tx.get("transactionType") or "").strip()
    code = raw.split("-", 1)[0].strip().upper()
    if code == "P":
        return "buy"
    if code == "S":
        return "sell"
    return "other"
