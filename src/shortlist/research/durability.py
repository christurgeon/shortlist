"""The "ROIC persistence" section of the research brief: one sec.gov fetch, the profile, and
the words.

DISPLAY ONLY, AND NEVER SHOWN TO THE MODEL. The section is computed after `assess()` returns
and is attached to the assessment for the report. It is not in the prompt, so it cannot shape
the thesis or the call, and it is not in `bundle.segments()`, so it cannot verify a quote.
Giving it to the model is a separate decision that needs its own measurement of MISUSE (a
"confirmed moat" story); see docs/audits/2026-10-08-moat-durability-phase1.md.

WHAT IT MAY SAY is fixed by docs/audits/2026-10-04-moat-durability-verdict.md and its
addenda. Every sentence below is licensed there, and the caveats are part of the licence:
- the heading is "ROIC persistence", never "moat": the study measured an accounting ratio;
- the base rate leaves out firms with no usable annual data three years later, and says so;
- capital growth always carries the split (a little over half is capital that kept growing
  slowly; the profit part is not established on the later cohorts) and the untested causes;
- steadiness is always labelled a track record, with the two cohort years that went the
  other way;
- an effect is the gap between the two EXTREME firms of a straight-line fit, with its interval,
  and is said that way: beside a company's third it would otherwise read as third against third;
- a company gets its THIRD among top-fifth firms, never a probability, and never a raw spread.
The fixed words are tied to the table by tests/test_durability_equivalence.py.

A NAME THE STUDY DOES NOT COVER GETS ONE SENTENCE SAYING WHY (`not_shown`). Silence would
read as "nothing to report", and stderr never reaches Telegram.

NEVER RAISES (`fetch_section`). Real company facts can be malformed (a `val` that is not a
number, a list where a dict is expected) and `annual_series` then raises; one optional
section must never cost a brief."""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import threading
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from ..durability import TAX, compact_facts
from ..durability_profile import (
    BEFORE_REFERENCE,
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
_CACHE_DAY = re.compile(r"CIK\d{10}-(\d{4}-\d{2}-\d{2})\.json")
# A 10-K is filed 30 to 90 days after its year end. When the brief's 10-K was filed more than
# this long after the latest year end in the facts, the facts do not hold that 10-K's year: a
# same-day cache from before the filing, or the API lagging it. (A 10-K that is itself this
# late is far outside the study's 120-day rule.)
LAG_DAYS = 300

# Two endings. A name the study does not cover is told what the study covers; a name whose
# DATA could not be used is told that, so a fetch failure never reads as "not covered".
_SCOPE = ("The study covers only non-financial 10-K filers with revenue of $100M or more and a "
          "top-fifth ROIC. Absence says nothing about this company.")
_DATA_LIMIT = "This is a limit of the data, not a reading of the company."
_REASONS = {
    NOT_TOP_FIFTH: "ROIC {roic} for the year ended {period_end} (the study's basis) is below "
                   "{floor}, the top-fifth cutoff of fiscal {table_year}",
    SECTOR_NOT_COVERED: "banks, lenders, brokers, insurers and REITs are outside the study",
    SIC_UNKNOWN: "no SIC code is available, so the study cannot tell whether it is a bank, an "
                 "insurer or a REIT",
    NO_ANNUAL_FACTS: "the SEC has no 10-K financial data for it (a foreign filer, a fund or a new "
                     "registrant)",
    STALE: "the latest annual data is for the year ended {period_end}; a newer year should be on "
           "file, or the company has stopped filing",
    LATEST_YEAR_UNUSABLE: "the latest year lacks at least one of operating income, equity, total "
                          "assets or revenue under the tags the study reads, or its 10-K was filed "
                          "more than 120 days after the year end, which the study excludes",
    LOW_CAPITAL: "invested capital (equity plus tagged debt) is zero, negative or under 10% of "
                 "total assets, so the study cannot compute a ROIC. This says nothing about how "
                 "much the business earns",
    REVENUE_BELOW_FLOOR: "revenue is under $100M",
    REFERENCE_OUT_OF_DATE: "the reference table (fiscal {table_year}) is too old for the year "
                           "ended {period_end}",
    BEFORE_REFERENCE: "its latest year on file ended {period_end}, which is before the year the "
                      "reference table is built for (fiscal {table_year}); the section needs its "
                      "newer 10-K",
    NO_PREDICTOR: "neither capital growth nor ROIC steadiness can be computed from its history",
    FACTS_LAG_FILING: "SEC data does not yet include the latest 10-K",
    UNAVAILABLE: "SEC data could not be read",
}
_DATA_REASONS = (STALE, REFERENCE_OUT_OF_DATE, BEFORE_REFERENCE, FACTS_LAG_FILING, UNAVAILABLE)
_GROWTH_THIRDS = ("fastest-growing", "middle", "slowest-growing")
_STEADY_THIRDS = ("least steady", "middle", "steadiest")
_WORDS = {2: "two", 3: "Three", 6: "six", 11: "Eleven"}


def _today() -> date:               # seam for tests, options.py pattern
    return date.today()


# ---------------------------------------------------------------- numbers

def _round(x: float, places: int = 0) -> Decimal:
    """Half up, on the decimal the float prints as. `round()` is half-even and `-0` would print."""
    q = Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return q + 0                    # Decimal("-0") + 0 == Decimal("0")


def _pct(x: float, places: int = 0) -> str:
    return f"{_round(x * 100, places)}%"


def _signed_pct(x: float, places: int = 0) -> str:
    v = _round(x * 100, places)
    return f"+{v}%" if v > 0 else f"{v}%"


def _points(x: float, places: int = 0) -> str:
    return str(_round(x * 100, places))


def _signed_points(x: float) -> str:
    v = _round(x * 100)
    return f"+{v}" if v > 0 else str(v)


def _span(years: list[int]) -> str:
    return f"{years[0]}-{years[1]}"


def _effect(effect: dict, slow: str, fast: str) -> str:
    """Two point estimates, each with its own interval and its own cohort years: not a range.
    The coefficient is the change from rank 0 to rank 1 on a straight-line fit, so it is said
    as a gap between the two EXTREME firms, never between thirds."""
    d, h = effect["discovery"], effect["holdout"]
    return (f"a firm at the {slow} extreme is estimated to have been {_points(d['beta'])} "
            f"percentage points more likely (95% interval {_points(d['ci'][0])} to "
            f"{_points(d['ci'][1])}) than one at the {fast} extreme to hold a top-fifth ROIC three "
            f"years later in the {_span(d['years'])} cohorts, and {_points(h['beta'])} points "
            f"({_points(h['ci'][0])} to {_points(h['ci'][1])}) in {_span(h['years'])}")


# ---------------------------------------------------------------- the words

def _company_line(p: DurabilityProfile) -> str:
    line = (f"- This company: ROIC {_pct(p.roic, 1)} for the year ended {p.period_end} (operating "
            f"income x {_round(1 - TAX, 2)} / (equity + tagged debt); the study's basis, which can "
            f"differ from the scorecard's ROIC). That is above {_pct(p.floor, 1)}, the top-fifth "
            f"cutoff among the {p.universe_n:,} firms in the study's fiscal-{p.reference_year} "
            f"universe (non-financial 10-K filers with revenue of $100M or more and a computable "
            f"ROIC).")
    if p.bucket != p.reference_year:
        line += (f" The study has no cutoff for this company's fiscal year yet, so the "
                 f"fiscal-{p.reference_year} cutoff is used.")
    if p.roic - p.floor < 0.01:
        line += " It is within 1 percentage point of the cutoff."
    if not p.has_debt:
        line += " No debt is reported in the tagged data, so invested capital here is book equity."
    return line


def _cohorts_line(table: dict) -> str:
    d, h = table["cohorts"]["discovery"], table["cohorts"]["holdout"]
    return (f"- Past cohorts: {_pct(d['hold_rate'])} of top-fifth firms (cohorts formed "
            f"{_span(d['years'])}) and {_pct(h['hold_rate'])} ({_span(h['years'])}) were again at "
            f"or above the top-fifth cutoff three years later. Only firms that still filed usable "
            f"10-K data then are counted. The {_pct(d['not_counted'])} and {_pct(h['not_counted'])} "
            f"that did not (stopped filing, for example after a takeover or a failure; filed late; "
            f"or left an input untagged) are left out. A few counted firms, under 4%, had too "
            f"little invested capital for a ROIC by then and count as held because their operating "
            f"income was positive. This is a group average, not adjusted for this company's ROIC "
            f"level.")


def _growth_line(p: DurabilityProfile, table: dict) -> str:
    inv = table["effects"]["investment"]
    low, high = cut_points(table)["investment"]
    share = inv["capital_share"]
    return (f"- Capital growth: invested capital {_signed_pct(p.capital_growth, 1)} in the last "
            f"year, "
            f"the {_GROWTH_THIRDS[p.investment_third]} third of top-fifth firms (slowest third: "
            f"{_signed_pct(low, 1)} or less; fastest third: over {_signed_pct(high, 1)}; all "
            f"sectors pooled, not sector-adjusted). Within the same two-digit SIC sector and cohort "
            f"year, "
            f"with ROIC rank and revenue rank held fixed, "
            f"{_effect(inv, 'slowest-growing', 'fastest-growing')}. These are the two ends of a "
            f"straight-line fit, not a gap between thirds. Among firms still profitable three "
            f"years later, a little over half of the link with a higher later ROIC "
            f"({_pct(share['discovery'])}, {_pct(share['holdout'])}) runs through capital that kept "
            f"growing slowly. The rest runs through profit, which is positive but not "
            f"distinguishable from zero in {_span(inv['holdout']['years'])}. This split is "
            f"descriptive. Not tested: acquired capital, cash and payouts (buybacks lower invested "
            f"capital), a one-year profit spike. This is a pattern in the ratio and in capital "
            f"growth, not a finding about the business.")


def _steadiness_line(p: DurabilityProfile, table: dict) -> str:
    stab = table["effects"]["stability"]
    low, high = cut_points(table)["stability"]
    years = ("its last 4 years" if p.years_seen == 4
             else f"the {p.years_seen} of its last 4 years that have a ROIC")
    return (f"- ROIC steadiness (a track-record line: a longer view of the same ROIC level, not "
            f"evidence of a separate trait): over {years} its ROIC percentile rank in the study's "
            f"universe had a standard deviation of {_points(p.stability_spread, 1)} percentile "
            f"points, the {_STEADY_THIRDS[p.stability_third]} third of top-fifth firms (steadiest "
            f"third: {_points(low, 1)} or less; least steady third: over {_points(high, 1)}). With "
            f"the same controls, {_effect(stab, 'steadiest', 'least steady')}. The "
            f"{_span(stab['holdout']['years'])} result comes from the 2020 and 2021 cohorts; in "
            f"2018 and 2019 the effect went the other way.")


def brief_section(p: DurabilityProfile, table: dict) -> str:
    """The section body for a top-fifth company. Plain lines, no markup beyond the leading
    "- ": the markdown brief and the Telegram report both print it as it is."""
    lines = ["A historical pattern in an accounting ratio, not a forecast for this company. It "
             "says nothing about moat, price or returns.",
             _company_line(p), _cohorts_line(table)]
    if p.capital_growth is not None:
        lines.append(_growth_line(p, table))
    if p.stability_spread is not None:
        lines.append(_steadiness_line(p, table))
    run, tested, passed = table["tests_run"], table["predictors_tested"], len(table["passed"])
    first, later = (_span(table["cohorts"][w]["years"]) for w in ("discovery", "holdout"))
    survivors = table["discovery_survivors"]
    # The pass rule INCLUDES the later cohorts. "Two passed and were then checked" would read
    # as a pass plus a confirmation; a third test cleared the first cohorts and failed there.
    lines.append(f"- {_WORDS.get(run, run)} tests were run on {_WORDS.get(tested, tested)} "
                 f"predictors. {_WORDS.get(survivors, survivors)} cleared the {first} cohorts; "
                 f"{_WORDS.get(passed, passed)} of them (capital growth and steadiness) also "
                 f"cleared the {later} cohorts, which share firms with the earlier ones, so the "
                 f"second stage is not an independent sample.")
    return "\n".join(lines)


def not_shown(reason: str, detail: Optional[dict] = None) -> str:
    """The section body for a name the study does not cover, or whose data could not be used."""
    d = dict(detail or {})
    if "roic" in d and "floor" in d:
        # Never "ROIC 15.5% is below 15.5%": a second decimal when the first cannot tell them apart.
        places = 1 if _pct(d["roic"], 1) != _pct(d["floor"], 1) else 2
        d["roic"], d["floor"] = _pct(d["roic"], places), _pct(d["floor"], places)
    tail = _DATA_LIMIT if reason in _DATA_REASONS else _SCOPE
    return f"Not shown: {_REASONS[reason].format(**d)}. {tail}"


# ---------------------------------------------------------------- the fetch

def _cache_path(cache_dir: Path, cik: int, today: date) -> Path:
    return cache_dir / f"CIK{cik:010d}-{today.isoformat()}.json"


def _write_cache(path: Path, record: dict, today: date) -> None:
    """Best-effort. A unique temp name, then a rename: two share classes of one company can be
    researched in two threads and would write the same path. Old days are pruned here, for
    every CIK, or names researched once would stay for ever."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, separators=(",", ":")))
        os.replace(tmp, path)
        cutoff = (today - timedelta(days=CACHE_KEEP_DAYS)).isoformat()
        for old in path.parent.glob("CIK*-*.json*"):        # the day files and any orphan temp
            day = _CACHE_DAY.match(old.name)
            if day and day.group(1) < cutoff:
                old.unlink(missing_ok=True)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _stream(cik: int, identity: str, deadline_s: float, transport, stop: threading.Event) -> bytes:
    import httpx

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
            if stop.is_set() or time.monotonic() - start > deadline_s:
                raise TimeoutError("the response is over the deadline")
            chunks.append(chunk)
    return b"".join(chunks)


def _download(cik: int, identity: str, deadline_s: float, transport=None) -> bytes:
    """The response body, within `deadline_s` of wall clock and under a size cap. The research
    phase has about 100 s of slack against `research_phase_budget_s`; past it EVERY brief of
    the run is lost, so one optional section must never be able to wait.

    WHY A THREAD. An httpx timeout is per phase and per read, not for the request. A server
    that drips one header byte inside every read timeout is never timed out at all (measured
    2026-10-09 on a loopback socket: 79 s against a 1 s timeout, ended only by the header size
    limit), and name resolution is outside every httpx timeout. So the request runs in a
    daemon thread and the caller waits `deadline_s` for it, no longer. It cannot delay a brief.

    AN ABANDONED THREAD IS NOT STOPPED. It holds one socket until the server ends the response
    or one of its own read timeouts fires, and `stop` is seen only between two body chunks. A
    brief makes at most two requests (the lag re-read), so the section costs at most twice
    `deadline_s`."""
    sec_throttle()("durability")                # the one process-wide sec.gov budget
    box: dict = {}
    stop = threading.Event()

    def work() -> None:
        try:
            box["body"] = _stream(cik, identity, deadline_s, transport, stop)
        except Exception as e:          # noqa: BLE001 — carried to the caller below
            box["error"] = e

    worker = threading.Thread(target=work, name="durability-fetch", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        stop.set()
        raise TimeoutError("the request is over the deadline")
    if "error" in box:
        raise box["error"]
    return box["body"]              # KeyError if the worker died without either: caught above us


def fetch_compacted(cik: int, cfg: dict, *, today: date, use_cache: bool = True,
                    transport=None) -> tuple[dict, Optional[Path]]:
    """(the company's facts compacted by the study's own `compact_facts`, the cache path to
    write them to — None when they came from the cache). A filer with no annual 10-K fact gives
    an empty record. RAISES on any failure; `fetch_section` is the guard.

    NOTHING IS CACHED HERE. The caller writes the record only after `profile` has read it: a
    body can pass the checks below and still hold a row that makes `annual_series` raise, and
    cached, it would fail every later brief of the day without a new request."""
    path = _cache_path(Path(cfg.get("cache_dir") or DEFAULT_CACHE_DIR), cik, today)
    if use_cache:
        try:
            return json.loads(path.read_text()), None
        except (OSError, ValueError):
            pass
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise RuntimeError("SEC_IDENTITY is not set")
    body = json.loads(_download(cik, identity, _number(cfg, "deadline_s", DEFAULT_DEADLINE_S),
                                transport))
    # An SEC block page is not JSON; an error object has no `facts`; another company's body
    # would be a wrong section with nothing to show for it.
    if (not isinstance(body, dict) or not isinstance(body.get("facts"), dict)
            or int(body["cik"]) != cik):
        raise ValueError("the response is not the company facts of this CIK")
    return compact_facts(body) or {"facts": {"us-gaap": {}}}, path


def _cik(ticker: str) -> int:
    """Off a fresh edgartools `Company`, NOT the `company_tickers.json` map: that map sends XOM
    to a fee-filing shell (data/sources/edgar.py)."""
    from edgar import Company  # lazy: optional [edgar] extra

    from .filings import require_identity

    require_identity()
    return int(Company(ticker).cik)


def _number(cfg: dict, key: str, default: float) -> float:
    """A positive number from the config, or the default: a null or mistyped knob must not turn
    every section into "SEC data could not be read"."""
    value = cfg.get(key)
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return float(value) if ok else float(default)


def _lags(bundle, period_end: Optional[str]) -> bool:
    filed = str(getattr(getattr(bundle, "tenk", None), "filing_date", "") or "")[:10]
    try:
        return (date.fromisoformat(filed) - date.fromisoformat(period_end or "")).days > LAG_DAYS
    except ValueError:              # no date, or one that is not ISO: nothing to compare
        return False


def _section(card, bundle, config: dict, today: date, transport) -> tuple[str, str]:
    cfg = (config.get("research") or {}).get("durability")
    cfg = cfg if isinstance(cfg, dict) else {}
    table = load_table()
    if table is None:
        return not_shown(UNAVAILABLE), UNAVAILABLE
    sic = getattr(getattr(card, "metrics", None), "sic", None)
    reason = sector_reason(sic, config)
    if reason:                                  # no request for a bank
        return not_shown(reason), reason
    cik = _cik(card.ticker)
    max_gap = int(_number(cfg, "max_table_gap_years", 1))

    def read(use_cache: bool):
        compacted, fresh = fetch_compacted(cik, cfg, today=today, use_cache=use_cache,
                                           transport=transport)
        result = profile(compacted, today, table, sic=sic, config=config, max_gap=max_gap)
        if fresh is not None:                   # only a record that `profile` could read
            _write_cache(fresh, compacted, today)
        return result

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
        with contextlib.suppress(Exception):    # a closed stderr must not cost the brief either
            print(f"research: ROIC-persistence section failed for {getattr(card, 'ticker', '?')}: "
                  f"{type(e).__name__}: {redact_secrets(str(e))[:200]}", file=sys.stderr)
        return not_shown(UNAVAILABLE), UNAVAILABLE
