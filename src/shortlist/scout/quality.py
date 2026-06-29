"""Pure quality predicates for SC 13D discovery (spec §6, §14 #9/#11/#14).

The raw SCHEDULE 13D firehose is noise-dominated (verified 2026-06-28): SPAC shells,
foreign holdcos, micro-cap shells, and affiliate/sponsor filings whose filer name echoes
the subject. These predicates raise the signal-to-noise of the *digest* — the scorer + the
market-cap gate remain the downstream skeptic.
"""
from __future__ import annotations

import re

_INITIAL_FORMS = {"SCHEDULE 13D", "SC 13D"}
_SPAC_MARKERS = ("acquisition corp", "acquisition company", "acquisition holdings",
                 "blank check", "spac")
# stripped before affiliate overlap so funds don't collide on generic words. _norm() has
# already converted punctuation to spaces, so entries are bare tokens (no "l.p."/"inc.").
_GENERIC_TOKENS = {"llc", "lp", "inc", "corp", "co", "company",
                   "capital", "management", "mgmt", "partners", "holdings", "holding",
                   "group", "fund", "funds", "trust", "ltd", "plc", "the", "and", "advisors",
                   "associates", "investment", "investments", "value", "global", "master"}

# normalized-substring alias map: canonical -> distinctive fragments that identify the filer.
# Activists file under many entity variants/SPVs, so match on a distinctive fragment, not the
# full legal name (spec §14 #11). Curated prior, extensible — not exhaustive. NOTE: matching is
# substring-anywhere, so short bare fragments ("jana", "pershing") could in principle match an
# unrelated name; acceptable in the narrow 13D filer-name space (and only BOOSTS strength).
_MARQUEE: dict[str, tuple[str, ...]] = {
    "Elliott":         ("elliott",),
    "Icahn":           ("icahn",),
    "Starboard":       ("starboard",),
    "Trian":           ("trian",),
    "Pershing Square": ("pershing square", "pershing"),
    "ValueAct":        ("valueact", "value act"),
    "Third Point":     ("third point",),
    "Jana":            ("jana partners", "jana"),
    "Engine Capital":  ("engine capital",),
    "Engaged Capital": ("engaged capital",),
    "Sachem Head":     ("sachem head",),
    "Corvex":          ("corvex",),
    "Ancora":          ("ancora",),
    "Legion":          ("legion partners",),
    "Politan":         ("politan",),
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def is_initial_13d(form: str) -> bool:
    """True for an initial SCHEDULE 13D (or legacy SC 13D); False for /A amendments."""
    return (form or "").strip().upper() in _INITIAL_FORMS


def is_spac_or_shell(subject_name: str) -> bool:
    n = _norm(subject_name)
    return any(mark in n for mark in _SPAC_MARKERS)


def _distinctive_tokens(name: str) -> set[str]:
    return {t for t in _norm(name).split() if t not in _GENERIC_TOKENS and len(t) > 2}


def is_affiliate_filing(activist: str, subject: str) -> bool:
    """A filer whose distinctive (non-generic) name tokens overlap the subject's is an
    affiliate/control transaction, not outside activism (e.g. Hawkeye HoldCo ↔ Hawkeye
    Systems)."""
    a, s = _distinctive_tokens(activist), _distinctive_tokens(subject)
    return bool(a & s)


def marquee_activist(activist: str) -> str | None:
    """Canonical fund name if the filer matches the curated alias map, else None."""
    n = _norm(activist)
    for canonical, fragments in _MARQUEE.items():
        if any(frag in n for frag in fragments):
            return canonical
    return None
