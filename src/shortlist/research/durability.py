"""The "ROIC persistence" section of the research brief: one sec.gov fetch, the profile, and
the words.

DISPLAY ONLY, AND NEVER SHOWN TO THE MODEL. The section is computed after `assess()` returns
and is attached to the assessment for the report. It is not in the prompt, so it cannot shape
the thesis or the call, and it is not in `bundle.segments()`, so it cannot verify a quote.
Giving it to the model is a separate decision that needs its own measurement of MISUSE (a
"confirmed moat" story); see docs/audits/2026-10-08-moat-durability-phase1.md.

WHAT IT MAY SAY is fixed by docs/audits/2026-10-04-moat-durability-verdict.md and its two
addenda. Every sentence below is licensed there, and the caveats are part of the licence:
- the heading is "ROIC persistence", never "moat": the study measured an accounting ratio;
- the base rate leaves out firms with no usable annual data three years later, and says so;
- capital growth always carries the split (a little over half is capital that kept growing
  slowly; the profit part is not established on the later cohorts) and the untested causes;
- steadiness is always labelled a track record, with the two cohort years that went the
  other way;
- a company gets its THIRD among top-fifth firms, never a probability, and never a raw spread.
The fixed words are tied to the table by tests/test_durability_equivalence.py.

A NAME THE STUDY DOES NOT COVER GETS ONE SENTENCE SAYING WHY (`not_shown`). Silence would
read as "nothing to report", and stderr never reaches Telegram.

NEVER RAISES (`fetch_section`). Real company facts can be malformed (a `val` that is not a
number, a list where a dict is expected) and `annual_series` then raises; one optional
section must never cost a brief."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from ..durability import TAX, compact_facts
from ..durability_profile import (
    FACTS_LAG_FILING,
    LATEST_YEAR_UNUSABLE,
    LOW_CAPITAL,
    NO_ANNUAL_FACTS,
    NO_PREDICTOR,
    NOT_TOP_FIFTH,
    REFERENCE_OUT_OF_DATE,
    REVENUE_BELOW_FLOOR,
    SECTOR_NOT_COVERED,
    SHOWN,
    SIC_UNKNOWN,
    STALE,
    UNAVAILABLE,
    DurabilityProfile,
    cut_points,
    load_table,
    profile,
    sector_reason,
)
from ..edgar.sec_throttle import sec_throttle
from ..env import redact_secrets

HEADING = "ROIC persistence (computed from SEC data — not LLM-generated, not filing text)"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
DEFAULT_CACHE_DIR = ".cache/durability-live"
DEFAULT_DEADLINE_S = 15.0
# The largest company-facts bodies are tens of megabytes. Past this, the body is not one.
MAX_BYTES = 64 * 1024 * 1024
CACHE_KEEP_DAYS = 7
# A 10-K is filed 30 to 90 days after its year end. When the brief's 10-K was filed more than
# this long after the latest year end in the facts, the facts do not hold that 10-K's year: a
# same-day cache from before the filing, or the API lagging it. (A 10-K that is itself this
# late is far outside the study's 120-day rule.)
LAG_DAYS = 300

_SCOPE = ("The study covers only non-financial 10-K filers with revenue of $100M or more and a "
          "top-fifth ROIC. Absence says nothing about this company.")
_REASONS = {
    NOT_TOP_FIFTH: "ROIC {roic} for the year ended {period_end} (the study's basis) is below the "
                   "{floor} top-fifth cutoff",
    SECTOR_NOT_COVERED: "banks, insurers and REITs are outside the study",
    SIC_UNKNOWN: "no SIC code is available, so the sector rule cannot be applied",
    NO_ANNUAL_FACTS: "the SEC has no 10-K financial data for it (a foreign filer, a fund or a new "
                     "registrant)",
    STALE: "the latest annual data is for the year ended {period_end}; a newer year should be on file",
    LATEST_YEAR_UNUSABLE: "the latest year has no operating income, equity, total assets or revenue "
                          "under the tags the study reads, or its 10-K was filed more than 120 days "
                          "after the year end",
    LOW_CAPITAL: "invested capital is negative or under 10% of assets, so this ROIC is not defined "
                 "(common after large buybacks)",
    REVENUE_BELOW_FLOOR: "revenue is under $100M",
    REFERENCE_OUT_OF_DATE: "the reference table (fiscal {table_year}) does not cover the year ended "
                           "{period_end}",
    NO_PREDICTOR: "neither capital growth nor ROIC steadiness can be computed from its history",
    FACTS_LAG_FILING: "SEC data does not yet include the latest 10-K",
    UNAVAILABLE: "SEC data could not be read",
}
_GROWTH_THIRDS = ("fastest-growing", "middle", "slowest-growing")
_STEADY_THIRDS = ("least steady", "middle", "steadiest")
_WORDS = {2: "two", 3: "three", 6: "Six"}


def _today() -> date:               # seam for tests, options.py pattern
    return date.today()


# ---------------------------------------------------------------- numbers

def _round(x: float, places: int = 0) -> Decimal:
    """Half up, on the decimal the float prints as. `round()` is half-even and `-0` would print."""
    q = Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return q + 0                    # Decimal("-0") + 0 == Decimal("0")


def _pct(x: float, places: int = 0) -> str:
    return f"{_round(x * 100, places)}%"


def _signed_pct(x: float) -> str:
    v = _round(x * 100)
    return f"+{v}%" if v > 0 else f"{v}%"


def _points(x: float, places: int = 0) -> str:
    return str(_round(x * 100, places))


def _signed_points(x: float) -> str:
    v = _round(x * 100)
    return f"+{v}" if v > 0 else str(v)


def _span(years: list[int]) -> str:
    return f"{years[0]}-{years[1]}"


def _effect(effect: dict, windows: dict) -> str:
    """'by 15 points (95% interval 6 to 24) in the 2011-2017 cohorts and 20 points (9 to 31) in
    2018-2021'. Two point estimates, each with its own interval: not a range."""
    d, h = effect["discovery"], effect["holdout"]
    return (f"by {_points(d['beta'])} points (95% interval {_points(d['ci'][0])} to "
            f"{_points(d['ci'][1])}) in the {_span(windows['discovery']['years'])} cohorts and "
            f"{_points(h['beta'])} points ({_points(h['ci'][0])} to {_points(h['ci'][1])}) in "
            f"{_span(windows['holdout']['years'])}")


# ---------------------------------------------------------------- the words

def _company_line(p: DurabilityProfile) -> str:
    line = (f"- This company: ROIC {_pct(p.roic, 1)} for the year ended {p.period_end} (operating "
            f"income x {_round(1 - TAX, 2)} / (equity + tagged debt); the study's basis, which can "
            f"differ from the scorecard's ROIC). That is above {_pct(p.floor, 1)}, the top-fifth "
            f"cutoff among {p.universe_n:,} non-financial 10-K filers with revenue of $100M or more "
            f"in fiscal {p.reference_year}.")
    if p.bucket != p.reference_year:
        line += " The cutoff for this company's own fiscal year is not built yet."
    if p.roic - p.floor < 0.01:
        line += " It is within 1 point of the cutoff."
    if not p.has_debt:
        line += " No debt is reported in the tagged data, so invested capital here is book equity."
    return line


def _cohorts_line(table: dict) -> str:
    d, h = table["cohorts"]["discovery"], table["cohorts"]["holdout"]
    return (f"- Past cohorts: of top-fifth firms that still reported usable annual data three years "
            f"later, {_pct(d['hold_rate'])} (cohorts formed {_span(d['years'])}) and "
            f"{_pct(h['hold_rate'])} ({_span(h['years'])}) were still in the top fifth. Firms with "
            f"no usable annual data by then are not counted: {_pct(d['not_counted'])} and "
            f"{_pct(h['not_counted'])} of those cohorts. This is a group average, not adjusted for "
            f"this company's ROIC level.")


def _growth_line(p: DurabilityProfile, table: dict) -> str:
    inv = table["effects"]["investment"]
    low, high = cut_points(table)["investment"]
    later = _span(table["cohorts"]["holdout"]["years"])
    share = inv["capital_share"]
    return (f"- Capital growth: invested capital {_signed_pct(p.capital_growth)} in the last year: "
            f"the {_GROWTH_THIRDS[p.investment_third]} third of top-fifth firms (thirds split at "
            f"{_signed_pct(low)} and {_signed_pct(high)}, across all sectors, not within a sector). "
            f"Among firms of the same two-digit SIC sector and cohort year, with ROIC rank and "
            f"revenue rank held fixed, the slowest-growing end held more often than the "
            f"fastest-growing end: {_effect(inv, table['cohorts'])}, on a straight-line fit across "
            f"the rank range. A little over half of the link between slow capital growth and a "
            f"higher later ROIC ({_pct(share['discovery'])}, {_pct(share['holdout'])}) is capital "
            f"that kept growing slowly. The rest is profit, which is positive but not "
            f"distinguishable from zero in {later}. Not tested: acquired capital, retained cash, a "
            f"one-year profit spike. This is a pattern in the ratio, not a finding about the "
            f"business.")


def _steadiness_line(p: DurabilityProfile, table: dict) -> str:
    stab = table["effects"]["stability"]
    low, high = cut_points(table)["stability"]
    later = _span(table["cohorts"]["holdout"]["years"])
    by_year = ", ".join(_signed_points(stab["holdout_beta_by_year"][y])
                        for y in sorted(stab["holdout_beta_by_year"]))
    return (f"- ROIC steadiness (a track-record measure: a longer view of the same ROIC level, not "
            f"a separate trait): its ROIC percentile rank varied by {_points(p.stability_spread, 1)} "
            f"points over the {p.years_seen} years with a ROIC out of its last four: the "
            f"{_STEADY_THIRDS[p.stability_third]} third of top-fifth firms (thirds split at "
            f"{_points(low, 1)} and {_points(high, 1)} points). Steadier firms held more often: "
            f"{_effect(stab, table['cohorts'])}. By cohort year in {later}: {by_year} points. The "
            f"later result comes from the 2020 and 2021 cohorts; 2018 and 2019 went the other way.")


def brief_section(p: DurabilityProfile, table: dict) -> str:
    """The section body for a top-fifth company. Plain lines, no markup beyond the leading
    "- ": the markdown brief and the Telegram report both print it as it is."""
    lines = ["Historical frequencies for an accounting ratio. Not a forecast for this company. It "
             "says nothing about moat, price or returns.",
             _company_line(p), _cohorts_line(table)]
    if p.capital_growth is not None:
        lines.append(_growth_line(p, table))
    if p.stability_spread is not None:
        lines.append(_steadiness_line(p, table))
    tested, passed = table["predictors_tested"], len(table["passed"])
    lines.append(f"- {_WORDS.get(tested, tested)} predictors were tested. These "
                 f"{_WORDS.get(passed, passed)} passed a check on later years that shares firms "
                 f"with the first period.")
    return "\n".join(lines)


def not_shown(reason: str, detail: Optional[dict] = None) -> str:
    """The section body for a name the study does not cover, or whose data could not be read."""
    d = dict(detail or {})
    for key in ("roic", "floor"):
        if key in d:
            d[key] = _pct(d[key], 1)
    return f"Not shown: {_REASONS[reason].format(**d)}. {_SCOPE}"


# ---------------------------------------------------------------- the fetch

def _cache_path(cache_dir: Path, cik: int, today: date) -> Path:
    return cache_dir / f"CIK{cik:010d}-{today.isoformat()}.json"


def _write_cache(path: Path, record: dict, today: date) -> None:
    """Best-effort. A unique temp name, then a rename: two share classes of one company can be
    researched in two threads and would write the same path. Old days are pruned here, for
    every CIK, or names researched once would stay for ever."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, separators=(",", ":")))
        os.replace(tmp, path)
        cutoff = (today - timedelta(days=CACHE_KEEP_DAYS)).isoformat()
        for old in path.parent.glob("CIK*-*.json"):
            if old.stem[-10:] < cutoff:
                old.unlink(missing_ok=True)
    except OSError:
        pass


def _download(cik: int, identity: str, deadline_s: float, transport=None) -> bytes:
    """The response body. ONE DEADLINE FOR THE WHOLE REQUEST and a size cap: httpx timeouts are
    per phase, so a slow trickle could otherwise run for minutes, and the research phase has
    about 100 s of slack against `research_phase_budget_s`."""
    import httpx

    sec_throttle()("durability")                # the one process-wide sec.gov budget
    start = time.monotonic()
    with httpx.Client(timeout=deadline_s, transport=transport,
                      headers={"User-Agent": identity, "Accept": "application/json"}) as client, \
            client.stream("GET", FACTS_URL.format(cik=cik)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        chunks, size = [], 0
        for chunk in r.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise RuntimeError("the response is over the size cap")
            if time.monotonic() - start > deadline_s:
                raise TimeoutError("the response is over the deadline")
            chunks.append(chunk)
    return b"".join(chunks)


def fetch_compacted(cik: int, cfg: dict, *, today: date, use_cache: bool = True,
                    transport=None) -> dict:
    """The company's facts, compacted by the study's own `compact_facts`: what `profile` reads.
    A filer with no annual 10-K fact gives an empty record. RAISES on any failure;
    `fetch_section` is the guard.

    THE COMPACTED RECORD IS WHAT IS CACHED (kilobytes, for the day), and only after the body
    was checked: an SEC block page or a JSON error object must not be served for a day."""
    path = _cache_path(Path(cfg.get("cache_dir") or DEFAULT_CACHE_DIR), cik, today)
    if use_cache:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise RuntimeError("SEC_IDENTITY is not set")
    deadline = float(cfg.get("deadline_s") or DEFAULT_DEADLINE_S)
    body = json.loads(_download(cik, identity, deadline, transport))
    if (not isinstance(body, dict) or not isinstance(body.get("facts"), dict)
            or int(body["cik"]) != cik):
        raise ValueError("the response is not the company facts of this CIK")
    record = compact_facts(body) or {"facts": {"us-gaap": {}}}
    _write_cache(path, record, today)
    return record


def _cik(ticker: str) -> int:
    """Off a fresh edgartools `Company`, NOT the `company_tickers.json` map: that map sends XOM
    to a fee-filing shell (data/sources/edgar.py)."""
    from edgar import Company  # lazy: optional [edgar] extra

    from .filings import require_identity

    require_identity()
    return int(Company(ticker).cik)


def _lags(bundle, period_end: Optional[str]) -> bool:
    filed = str(getattr(getattr(bundle, "tenk", None), "filing_date", "") or "")[:10]
    if not filed or not period_end:
        return False
    return (date.fromisoformat(filed) - date.fromisoformat(period_end)).days > LAG_DAYS


def _section(card, bundle, config: dict, today: date, transport) -> tuple[str, str]:
    cfg = (config.get("research") or {}).get("durability") or {}
    table = load_table()
    if table is None:
        return not_shown(UNAVAILABLE), UNAVAILABLE
    sic = getattr(getattr(card, "metrics", None), "sic", None)
    reason = sector_reason(sic, config)
    if reason:                                  # no request for a bank
        return not_shown(reason), reason
    cik = _cik(card.ticker)
    max_gap = int(cfg.get("max_table_gap_years", 1))

    def read(use_cache: bool):
        compacted = fetch_compacted(cik, cfg, today=today, use_cache=use_cache, transport=transport)
        return profile(compacted, today, table, sic=sic, config=config, max_gap=max_gap)

    prof, status, detail = read(True)
    if _lags(bundle, prof.period_end if prof else detail.get("period_end")):
        prof, status, detail = read(False)
        if _lags(bundle, prof.period_end if prof else detail.get("period_end")):
            return not_shown(FACTS_LAG_FILING), FACTS_LAG_FILING
    if prof is None:
        return not_shown(status, detail), status
    return brief_section(prof, table), SHOWN


def fetch_section(card, bundle, config: dict, *, today: Optional[date] = None,
                  transport=None) -> tuple[str, str]:
    """(the section body, its status code) for one researched card. NEVER RAISES: any failure
    is the `unavailable` sentence and one redacted stderr line, so a systematic outage does not
    look like "this name is not covered"."""
    try:
        return _section(card, bundle, config, today or _today(), transport)
    except Exception as e:      # noqa: BLE001 — never-raises contract
        print(f"research: ROIC-persistence section failed for {getattr(card, 'ticker', '?')}: "
              f"{type(e).__name__}: {redact_secrets(str(e))[:200]}", file=sys.stderr)
        return not_shown(UNAVAILABLE), UNAVAILABLE
