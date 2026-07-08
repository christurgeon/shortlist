"""Static financial glossary behind the Telegram /explain command.

Pure leaf (stdlib only -- the data/finra.py / _form4.py pattern): imports
nothing from scoring/harness/bot. Entries are SEMANTICS-ONLY by design --
they never quote config.yaml thresholds or weights, so tuning never stales
them. Each body has three beats: what the term is, what it historically
implies about a stock, and how this system treats it (scored leg / hard
gate / advisory flag / discovery-only / research-only).

Completeness is enforced, not hoped for: tests/test_scoring_names.py binds
scoring.KNOWN_GATES/KNOWN_FLAGS to the emission-site literals, and
tests/scout/test_glossary.py asserts every one of those names resolves via
lookup() -- adding a gate/flag without documenting it here fails CI.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_STRIP = re.compile(r"[\s\-_./]+")

CATEGORIES = ("SEC filings", "Sub-scores", "Gates & flags",
              "Finance concepts", "Report mechanics")


def _normalize(term: str) -> str:
    return _STRIP.sub("", term.strip().lower())


@dataclass(frozen=True)
class Entry:
    name: str
    category: str
    aliases: tuple[str, ...]
    text: str


GLOSSARY: list[Entry] = [
    Entry("13D", "SEC filings", ("sc 13d", "schedule 13d"),
          "SEC filing when an investor crosses 5% ownership WITH intent to "
          "influence (vs 13G = passive). Historically precedes positive "
          "drift: activists push operational/strategic change and "
          "re-ratings. Here: the scout surfaces fresh initial 13Ds as watch "
          "candidates, and the activist_13d flag marks one on a screened "
          "name — advisory only, never moves the score."),
    Entry("composite", "Sub-scores", ("score", "overall score"),
          "The headline 0–100 number: a weighted blend of the seven "
          "sub-scores (quality, moat, growth, value, momentum, insider, "
          "risk). Weights are ratios — only their proportions matter. When "
          "a sub-score has no inputs its weight is redistributed across the "
          "rest, never silently zeroed, so a thin name isn't quietly "
          "dragged down. Rank with it, but read gates/flags/confidence "
          "alongside it."),
    Entry("gates vs flags", "Gates & flags", ("gate", "gates", "flag", "flags"),
          "Gates are HARD disqualifiers — a tripped gate means the name "
          "cannot pass or top the ranking regardless of score (shown as "
          "'gated'). Flags are SOFT advisories — context worth knowing "
          "(crowded short, value trap, social hype…) that never affects "
          "passed/composite/scored. Read gates as 'no', flags as 'but "
          "note…'."),
    Entry("CAGR", "Finance concepts", ("compound annual growth rate",),
          "Compound annual growth rate: the smoothed yearly growth between "
          "two endpoints, (end/start)^(1/years) − 1. Report variants: "
          "revenue/FCF/EPS CAGR (higher is better) and share-count CAGR "
          "(LOWER is better — positive means dilution, negative means "
          "buybacks). Endpoint-sensitive: one distorted terminal year can "
          "flatter or hide the trend, so cross-check persistence."),
    Entry("confidence", "Report mechanics", (),
          "The fraction of applicable sub-score weight actually present for "
          "this name — how much of the scorecard the data could fill in. "
          "Low confidence means the composite rests on few inputs (often "
          "FMP free-plan gating; see coverage). Very low confidence drops "
          "the name below the 'scored' validity floor entirely."),
]

_BY_ALIAS: dict[str, Entry] = {}
for _e in GLOSSARY:
    for _key in (_e.name, *_e.aliases):
        _norm = _normalize(_key)
        assert _norm not in _BY_ALIAS or _BY_ALIAS[_norm] is _e, \
            f"glossary alias collision: {_key!r}"
        _BY_ALIAS[_norm] = _e


def lookup(term: str) -> Entry | None:
    return _BY_ALIAS.get(_normalize(term))


def suggest(term: str, n: int = 3) -> list[str]:
    """Closest entry NAMES for an unknown term (deduped, match order kept)."""
    hits = difflib.get_close_matches(_normalize(term), list(_BY_ALIAS), n=n)
    names: list[str] = []
    for h in hits:
        name = _BY_ALIAS[h].name
        if name not in names:
            names.append(name)
    return names


def index_text() -> str:
    lines = ["📖 /explain <term> — glossary. Known terms:"]
    for cat in CATEGORIES:
        names = [e.name for e in GLOSSARY if e.category == cat]
        if names:
            lines.append(f"\n{cat}:\n" + ", ".join(names))
    return "\n".join(lines)


def entry_text(entry: Entry) -> str:
    return f"{entry.name}\n{entry.text}"
