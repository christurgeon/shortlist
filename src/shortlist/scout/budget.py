"""Select the top-X candidates that fit today's deep-screen ceiling (§4.1)."""
from __future__ import annotations

from collections import Counter
from typing import Mapping, Optional

from .models import Candidate

# Emission strings that are ONE originator for cap + confluence purposes.
# `EdgarThirteenFSignal` emits two kinds (new position, material add) from a single filing
# pair, and two funds agreeing is not two originators agreeing — which is exactly the
# invariant `originator()` exists to hold. Without this, fund-A-new + fund-B-add would read
# as confluence (cap-exempt, rank-boosted) while fund-A-new + fund-B-new would not.
# An EXPLICIT map, not a ":"-prefix rule: prefix collapsing would catch unrelated pairs such
# as `edgar:8k` / `edgar:8k_negative` by accident.
_SIGNAL_FAMILIES = {
    "edgar:13f_new_position": "edgar:13f",
    "edgar:13f_material_add": "edgar:13f",
}


def signal_family(signal: str) -> str:
    """The cap/confluence identity of an emission signal string — identity for everything but
    the 13F kinds. `caps` keys MUST use this vocabulary, since `select` looks them up with
    `originator()`'s return value."""
    return _SIGNAL_FAMILIES.get(signal, signal)


def originator(candidate: Candidate) -> Optional[str]:
    """The single discovery signal a candidate is charged to, or None if uncharged.

    Returns None in two distinct cases the caller treats alike (both are exempt from any
    cap) but which mean different things:

    * **Confluence** — two or more DISTINCT discovery signals found this ticker. Two
      independent originators agreeing is the strongest thing this funnel produces and a
      quota must never delete it.
    * **No discovery emission** — cannot normally reach `select` (`funnel.prefilter` drops
      booster-only candidates), but tolerated rather than raising.

    Counts **distinct `signal` strings**, not emissions: `EdgarThirteenFSignal` emits once
    per fund, so two marquee funds opening the same position yield two `edgar:13f`
    emissions on one candidate — which is one originator agreeing with itself, not
    confluence. Only `is_discovery` emissions count; boosters run before `select`
    (`daily.py`), so counting all emissions would read a booster as an originator.

    Signal strings are collapsed through `signal_family` first, so the two `edgar:13f` kinds
    count as one originator (see `_SIGNAL_FAMILIES`).
    """
    signals = {signal_family(e.signal) for e in candidate.emissions
               if getattr(e, "is_discovery", False)}
    if len(signals) == 1:
        return next(iter(signals))
    return None


def select(candidates: list[Candidate], daily_x: int,
           caps: Optional[Mapping[str, int]] = None
           ) -> tuple[list[Candidate], int, list[tuple[Candidate, str]]]:
    """Return ``(chosen, dropped_count, capped)``. Chosen = top ``daily_x`` by interest.

    ``caps`` maps a signal FAMILY (``signal_family`` — the emission signal string itself for
    every signal but the two ``edgar:13f`` kinds, which collapse to one) to the maximum
    number of slots that originator may claim. Keying it by a raw signal string where a
    family exists silently loses the cap. **Absent, empty, or non-binding ⇒ behaviour is
    identical to the uncapped ranking**, and ``capped`` is ``[]``.

    Two properties are deliberate and load-bearing:

    * **The cap only engages under contention.** At or below ``daily_x`` candidates there
      is nothing to arbitrate, so it is skipped entirely — capping there would drop names
      while slots sat empty.
    * **A cap never wastes a slot.** Names set aside by a quota are *backfilled* if the
      other originators cannot fill ``daily_x``. So the cap gives everyone else first
      refusal on slots beyond the quota rather than reserving them.

    Together these mean the cap is a **re-ordering of the drop set, not a hard quota**: an
    originator supplying every candidate on a quiet night still takes every slot. That is
    intended — crowd-out only exists when somebody is being crowded out — but it does mean
    the cap changes nothing on the nights when one originator dominates a *short* list.

    ``capped`` carries ``(candidate, reason)`` so the caller can name each drop; the
    funnel's other drop seams (veto, quality floor, investability floor) return their
    dropped candidates the same way rather than a bare count.
    """
    ordered = sorted(candidates, key=lambda c: c.interest, reverse=True)

    if not caps or len(ordered) <= daily_x:
        chosen = ordered[:daily_x]
        return chosen, max(0, len(ordered) - len(chosen)), []

    used: Counter[str] = Counter()
    chosen: list[Candidate] = []
    deferred: list[Candidate] = []
    for cand in ordered:
        if len(chosen) >= daily_x:
            break
        sig = originator(cand)
        limit = caps.get(sig) if sig is not None else None
        if limit is None or used[sig] < limit:
            if sig is not None and limit is not None:
                used[sig] += 1
            chosen.append(cand)
        else:
            deferred.append(cand)

    if deferred:
        room = max(0, daily_x - len(chosen))
        chosen.extend(deferred[:room])                  # never waste a slot

    chosen.sort(key=lambda c: c.interest, reverse=True)

    # A name is CAPPED only if the uncapped ranking would have screened it and the cap
    # displaced it. Reporting every deferred name would mislabel ordinary below-the-cut
    # drops — those are `dropped_for_budget` and always were.
    chosen_ids = {id(c) for c in chosen}
    capped = [(c, f"{originator(c) or '?'} quota "
                  f"{caps.get(originator(c) or '', 0)} (interest {c.interest:.2f})")
              for c in ordered[:daily_x] if id(c) not in chosen_ids]

    return chosen, max(0, len(ordered) - len(chosen)), capped
