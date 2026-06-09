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


def is_buy(tx: dict) -> bool:
    """FMP ``transactionType`` convention: P-prefixed codes are purchases
    (acquisitions), everything else is treated as a sale."""
    return (tx.get("transactionType") or "").upper().startswith("P")


def net_value(txns: Iterable[dict], limit: int = _WINDOW) -> float:
    """Signed net insider flow over the trailing ``limit`` transactions —
    purchases positive, sales negative."""
    net = 0.0
    for tx in list(txns)[:limit]:
        v = tx_value(tx)
        net += v if is_buy(tx) else -v
    return net
