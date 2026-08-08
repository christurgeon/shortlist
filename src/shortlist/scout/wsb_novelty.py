"""Rank-novelty qualification for the WSB discovery signal — pure, no I/O.

**The problem this solves.** ApeWisdom's board is a mention-volume leaderboard, and the
shipped `wsb_hype` rule qualified names by *velocity on that board*. Both keys concentrate
into names that are always on it: replayed over 42 cached boards the shipped rule selected a
**$230.8B median market cap**, with only **7%** of picks in the $0.3–10B band where a
retail-scale book can plausibly be early. That composition — not any return reading — is why
the originator was demoted on 2026-07-26.

**The instrument.** A ticker qualifies only when it is **not a board regular**: its best
(numerically lowest) rank across the prior N boards must be *worse* than `max_regular_rank`,
or it must be absent from every one of them. Rank is measured **relative to the board**, and
mega-caps occupy the top ~30 regardless of news, so rank-regularity excludes them close to
deterministically. Replayed on the same 42 boards this moves the median selected cap to
**$34.3B** and the $0.3–10B share to **39%**.

**Why not the two rules that were tried and rejected first** (both measured; do not rebuild):

* *Spike vs the ticker's own trailing median mention count.* Assumes mega-cap chatter is a
  stable high plateau a self-relative ratio normalizes away. It is a **volatile** plateau:
  AAPL exceeds 2× its own 14-day median on **38% of days**, so the rule fires on it constantly
  and composition barely moves.
* *A market-cap ceiling.* Excludes large names that are genuinely NOT board regulars — UNH,
  CAT, NKE, UPS, NVO all surface here, and the informative part is precisely that a name of
  that size has appeared at all.

**Absence is censored, not missing.** A ticker absent from a top-100 board had fewer mentions
than the rank-100 name that day — a real upper bound, not a data gap — so absence is treated
as maximal novelty rather than abstained on.

Evidence, holdout and the honest limits: `docs/audits/2026-08-07-wsb-novelty-rule.md`.
Replay: `docs/audits/scripts/wsb_novelty_replay.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

# A board rank is "regular" at or below this; configurable via scout.wsb_hype.novelty.
DEFAULT_MAX_REGULAR_RANK = 50
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MIN_MENTIONS = 20
DEFAULT_MIN_HISTORY_DAYS = 5

# Strength floor so a barely-qualifying name still outranks nothing at all, and the
# saturation point (mentions at which strength reaches 1.0) as a multiple of the floor.
# BOTH ARE UNFITTED PRIORS — nothing measures them; they only order names *within* a
# night's WSB emissions, and `interest` decides slots only when candidates exceed daily_x.
_STRENGTH_FLOOR = 0.3
_STRENGTH_SATURATION_MULT = 5.0


@dataclass(frozen=True)
class NoveltyVerdict:
    """One ticker's qualification outcome. `reason` is empty when `qualifies` is True."""
    qualifies: bool
    strength: float = 0.0
    best_prior_rank: Optional[int] = None   # None = absent from every prior board
    reason: str = ""


def board_regulars(boards: Iterable[Mapping[str, object]],
                   max_regular_rank: int = DEFAULT_MAX_REGULAR_RANK) -> dict[str, int]:
    """Collapse prior daily boards to ``{ticker: best (lowest) rank seen}``.

    ``boards`` is any iterable of ``{TICKER: row}`` maps where a row exposes a ``rank``
    (either a mapping with a ``"rank"`` key or an object with a ``.rank`` attribute).
    Rows without a usable integer rank are skipped — never counted as rank 0, which would
    make the ticker look maximally regular.

    Returns every ticker seen with its best rank, NOT only the regulars: the caller needs
    the distinction between "seen but never prominent" and "never seen at all", and
    ``max_regular_rank`` is applied by :func:`assess`. The parameter is accepted here only
    so callers can pass it positionally without it silently doing nothing.
    """
    best: dict[str, int] = {}
    for board in boards or ():
        for ticker, row in (board or {}).items():
            rank = _rank_of(row)
            if rank is None:
                continue
            key = (ticker or "").upper()
            if not key:
                continue
            prior = best.get(key)
            if prior is None or rank < prior:
                best[key] = rank
    return best


def _rank_of(row: object) -> Optional[int]:
    raw = row.get("rank") if isinstance(row, Mapping) else getattr(row, "rank", None)
    try:
        rank = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def assess(ticker: str,
           mentions: Optional[int],
           regulars: Mapping[str, int],
           *,
           max_regular_rank: int = DEFAULT_MAX_REGULAR_RANK,
           min_mentions: int = DEFAULT_MIN_MENTIONS,
           deny: Optional[frozenset[str]] = None) -> NoveltyVerdict:
    """Qualify one ticker against the prior-window regulars map.

    Abstains (does not qualify) on a missing or sub-floor mention count rather than
    guessing — consistent with the rest of the funnel's abstain-never-guess rule.
    """
    key = (ticker or "").upper()
    if not key:
        return NoveltyVerdict(False, reason="no ticker")
    if deny and key in deny:
        return NoveltyVerdict(False, reason="deny-listed")
    if mentions is None or mentions < min_mentions:
        got = "no mention count" if mentions is None else f"{mentions} mentions"
        return NoveltyVerdict(False, reason=f"{got} below floor {min_mentions}")

    best = regulars.get(key)
    if best is not None and best <= max_regular_rank:
        return NoveltyVerdict(False, best_prior_rank=best,
                              reason=f"board regular (best prior rank {best})")

    return NoveltyVerdict(True, strength=_strength(mentions, min_mentions),
                          best_prior_rank=best)


def _strength(mentions: int, min_mentions: int) -> float:
    """Map mention volume to 0.3–1.0. Volume is the only ordering information available
    here — the novelty test itself is binary, so it cannot rank the names it admits."""
    ceiling = max(1.0, _STRENGTH_SATURATION_MULT * max(1, min_mentions))
    return round(max(_STRENGTH_FLOOR, min(1.0, mentions / ceiling)), 4)


def qualify_board(today: Mapping[str, object],
                  prior_boards: list[Mapping[str, object]],
                  *,
                  max_regular_rank: int = DEFAULT_MAX_REGULAR_RANK,
                  min_mentions: int = DEFAULT_MIN_MENTIONS,
                  min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
                  top_n: int = 15,
                  deny: Optional[frozenset[str]] = None
                  ) -> tuple[Optional[list[tuple[str, NoveltyVerdict, int]]], str]:
    """Qualify a whole board. Returns ``(rows, detail)``; ``rows`` is None on abstention.

    Abstains when fewer than ``min_history_days`` prior boards are readable — with too
    little history almost nothing looks like a regular, so the rule would silently invert
    into a permissive one. Abstaining is deliberately NOT a fallback to the old velocity
    rule: that would reinstate the composition it exists to fix, on exactly the runs
    nobody is watching.

    ``rows`` are sorted by mention volume descending and truncated to ``top_n``; the
    caller is told how many were dropped so truncation is never silent.
    """
    n_prior = len(prior_boards or [])
    if n_prior < min_history_days:
        return None, (f"insufficient history: {n_prior} of {min_history_days} boards "
                      f"(no emission; NOT falling back to the velocity rule)")

    regulars = board_regulars(prior_boards, max_regular_rank)
    hits: list[tuple[str, NoveltyVerdict, int]] = []
    for ticker, row in (today or {}).items():
        mentions = _mentions_of(row)
        verdict = assess(ticker, mentions, regulars,
                         max_regular_rank=max_regular_rank,
                         min_mentions=min_mentions, deny=deny)
        if verdict.qualifies:
            hits.append(((ticker or "").upper(), verdict, mentions or 0))

    hits.sort(key=lambda h: h[2], reverse=True)
    kept, dropped = hits[:top_n], hits[top_n:]
    detail = f"{len(kept)} novel of {len(today or {})} tracked ({n_prior} prior boards)"
    if dropped:
        detail += f"; {len(dropped)} over top_n={top_n}: " + ", ".join(t for t, _, _ in dropped)
    return kept, detail


def _mentions_of(row: object) -> Optional[int]:
    raw = row.get("mentions") if isinstance(row, Mapping) else getattr(row, "mentions", None)
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
