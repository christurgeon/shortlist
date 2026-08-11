"""Position-monitor payload: which held names have a fresh clean-negative 8-K.

Pure leaf (stdlib only). Reads the daily run's already-computed veto_map — zero fetches.
See docs/POSITION_MONITOR.md §5. The alert routes to the filing; it emits no stance.
"""
from __future__ import annotations

KNOWN_BREACH_KINDS = frozenset({"8k_negative"})

# v1 subset of the negative-8-K item set — the clean, unambiguous negatives (§5.1).
DEFAULT_ITEMS = ("1.03", "2.04", "4.02")

ITEM_MEANINGS = {
    "1.03": "filed for bankruptcy",
    "2.04": "a lender is calling debt due early (default/acceleration)",
    "4.02": "its past financial statements can no longer be relied on — a restatement is coming",
}


def compute_alerts(positions: dict, veto_map: dict, items, seen) -> list[dict]:
    """One alert dict per held ticker with a fresh subset-item 8-K not already seen.

    positions: the {ticker: record} map (store["positions"]).
    veto_map:  {ticker: {"items": [...], "adsh": str, "last_date": iso}}.
    items:     iterable of item codes to alert on (the subset).
    seen:      set of already-surfaced "8k:<adsh>" keys.
    """
    wanted = set(items)
    out: list[dict] = []
    for ticker, rec in positions.items():
        v = veto_map.get(ticker)
        if not v:
            continue
        hit = [it for it in (v.get("items") or []) if it in wanted]
        if not hit:
            continue
        adsh = v.get("adsh")
        key = f"8k:{adsh}"
        if key in seen:
            continue
        lead = hit[0]
        out.append({
            "ticker": ticker,
            "kind": "8k_negative",
            "key": key,
            "adsh": adsh,
            "items": hit,
            "date": v.get("last_date"),
            "meaning": ITEM_MEANINGS.get(lead, "a material negative event was filed"),
            "thesis": rec.get("thesis"),
        })
    return out


def heartbeat(positions: dict, session_iso: str) -> dict:
    return {"count": len(positions), "as_of": session_iso}
