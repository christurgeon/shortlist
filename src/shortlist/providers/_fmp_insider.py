"""Shared FMP insider-transaction primitives: the P-prefix buy/sell sign
convention and the shares*price value formula.

Dependency-free leaf used by the harness ``FMPSource`` — same pattern as
``_form4.py``. Edit the interpretation of FMP's Form-4-ish ``insider-trading``
rows here. ``FMPSource`` builds per-transaction ``InsiderTxn`` records but shares
these primitives so the sign convention has a single source of truth.
"""

from typing import Iterable

_WINDOW = 60  # ~trailing window of transactions to net


def tx_value(tx: dict) -> float:
    """Dollar value of one FMP insider transaction (shares * price)."""
    return (tx.get("securitiesTransacted") or 0) * (tx.get("price") or 0)


def classify_tx(tx: dict) -> str:
    """Map an FMP insider row to 'buy' | 'sell' | 'other'.
    FMP's transactionType is an ENRICHED string ("P-Purchase"), unlike edgartools'
    bare single-letter Code column that _form4.classify_code parses — do not swap
    one for the other."""
    raw = (tx.get("transactionType") or "").strip()
    code = raw.split("-", 1)[0].strip().upper()
    if code == "P":
        return "buy"
    if code == "S":
        return "sell"
    return "other"


def is_buy(tx: dict) -> bool:
    """Back-compat thin wrapper around `classify_tx`. Still returns a real bool
    (existing tests use `is True`/`is False` identity checks)."""
    return classify_tx(tx) == "buy"


def net_value(txns: Iterable[dict], limit: int = _WINDOW) -> float:
    """Signed net insider flow over the trailing ``limit`` transactions —
    purchases positive, sales negative, 'other' (awards/exercises/gifts/tax-
    withholding/conversions) ignored entirely. The window slice applies to the
    raw list before classification, same as today."""
    net = 0.0
    for tx in list(txns)[:limit]:
        classification = classify_tx(tx)
        if classification == "other":
            continue
        v = tx_value(tx)
        net += v if classification == "buy" else -v
    return net
