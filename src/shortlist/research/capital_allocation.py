"""The "Capital allocation" section of the research brief: where the cash went and what the
reported capital earned, computed from the card's SEC statement series.

DISPLAY ONLY, AND NEVER SHOWN TO THE MODEL. Computed after `assess()` returns and stored with
the brief; not in the prompt and not a grounding segment. Not a score, gate or flag. It reads
only data already on the card, so it makes no request.

Deliberately not in `cachekey._PROMPT_MODULES`: the brief stores the rendered section, so a
wording change here reaches new briefs when the cache's day bucket turns.

Every line abstains on its own, with its reason; a name with nothing to show gets one
"Not shown: ..." sentence, never silence. NEVER RAISES (`section`)."""
from __future__ import annotations

import sys
from typing import Any, Optional

from ..data.bridge import ROIC_TAX_RATE
from ..env import redact_secrets

HEADING = "Capital allocation (computed from SEC data — not LLM-generated)"
SHOWN, NOT_SHOWN = "shown", "not_shown"

# Below this growth of invested capital (a share of its starting level) ΔNOPAT / ΔIC is a ratio
# of two small differences, so incremental ROIC and the reinvestment rate abstain.
MIN_IC_GROWTH = 0.05
# A resolved ratio past this magnitude is printed as a bound, not as a number.
_RATIO_CAP = 1.0

_SECTOR_LEGS = ("roic", "fcf_yield")

NOT_SHOWN_SECTOR = "Not shown: not meaningful for this sector."
NOT_SHOWN_NO_DATA = "Not shown: no SEC statement data on the card."
NOT_SHOWN_NOTHING = "Not shown: the statements on file do not resolve any of its lines."
NOT_SHOWN_UNREADABLE = "Not shown: the statement data on the card could not be read."


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _year(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _money(v: float) -> str:
    sign, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.1f}B"
    return f"{sign}${a / 1e6:,.0f}M"


def _pct(x: float, places: int = 0) -> str:
    return f"{x * 100:.{places}f}%"


def _ratio(x: float) -> str:
    if x > _RATIO_CAP:
        return f"more than {_pct(_RATIO_CAP)}"
    if x < -_RATIO_CAP:
        return f"less than -{_pct(_RATIO_CAP)}"
    return _pct(x)


def _span(first: int, last: int) -> str:
    return f"FY{first}" if first == last else f"FY{first}–FY{last}"


def _run(rows: list[dict], keys: tuple[str, ...]) -> list[tuple[int, dict]]:
    """The newest run of consecutive fiscal years whose `keys` all resolve, newest-first, as
    (year, {key: value})."""
    out: list[tuple[int, dict]] = []
    for r in rows:
        if not isinstance(r, dict):
            if out:
                break
            continue
        y = _year(r.get("fiscal_year"))
        vals = {k: _num(r.get(k)) for k in keys}
        ok = y is not None and all(v is not None for v in vals.values())
        if not ok or (out and y != out[-1][0] - 1):
            if out:
                break
            continue
        out.append((y, vals))
    return out


def _cash_line(fs: list[dict]) -> Optional[str]:
    run = _run(fs, ("operating_cash_flow", "free_cash_flow"))
    if not run:
        return None
    ocf = sum(v["operating_cash_flow"] for _, v in run)
    fcf = sum(v["free_cash_flow"] for _, v in run)
    span = _span(run[-1][0], run[0][0])
    how = "summed" if len(run) > 1 else "only"
    return (f"- Cash, {span} {how}: operating cash flow {_money(ocf)}, capital spending "
            f"{_money(ocf - fcf)}, free cash flow {_money(fcf)}.")


def _returns_line(m: Any, fs: list[dict]) -> Optional[str]:
    buy, div = _num(getattr(m, "repurchases", None)), _num(getattr(m, "dividends_paid", None))
    if buy is None and div is None:
        return None
    latest = fs[0] if fs and isinstance(fs[0], dict) else {}
    year = _year(latest.get("fiscal_year"))
    label = f"FY{year} only" if year is not None else "Latest fiscal year"
    parts = [f"buybacks {_money(abs(buy))}" if buy is not None else "no buyback tag",
             f"dividends {_money(abs(div))}" if div is not None else "no dividend tag"]
    fcf = _num(latest.get("free_cash_flow"))
    if fcf is not None and fcf > 0:
        returned = abs(buy or 0.0) + abs(div or 0.0)
        share = f"together {_pct(returned / fcf)} of that year's free cash flow"
    else:
        share = "not shown as a share of free cash flow, which was not positive"
    return f"- {label}: {', '.join(parts)}; {share}."


def _shares_line(fs: list[dict]) -> Optional[str]:
    run = _run(fs, ("diluted_shares",))
    if len(run) < 2 or run[-1][1]["diluted_shares"] <= 0:
        return None
    change = run[0][1]["diluted_shares"] / run[-1][1]["diluted_shares"] - 1.0
    shown = f"{change * 100:+.1f}%"
    if shown in ("+0.0%", "-0.0%"):
        shown, direction = "0.0%", "unchanged"
    else:
        direction = "fewer shares" if change < 0 else "more shares"
    return (f"- Diluted share count: {shown} from FY{run[-1][0]} to FY{run[0][0]} "
            f"({direction}).")


def _roic_line(ics: list[dict]) -> Optional[str]:
    by_year = {}
    for r in ics:
        if not isinstance(r, dict):
            continue
        y, nopat, ic = _year(r.get("fiscal_year")), _num(r.get("nopat")), _num(r.get("invested_capital"))
        if y is not None and nopat is not None and ic is not None and ic > 0:
            by_year[y] = nopat / ic
    if not by_year:
        return None
    return "- ROIC: " + ", ".join(f"FY{y} {_pct(by_year[y], 1)}" for y in sorted(by_year)) + "."


def _incremental_line(ics: list[dict]) -> str:
    head = "- Incremental ROIC and reinvestment rate: not shown"
    run = _run(ics, ("nopat", "invested_capital"))
    if len(run) < 2:
        return f"{head} (fewer than two consecutive fiscal years of ROIC inputs)."
    (y1, new), (y0, old) = run[0], run[-1]
    d_ic = new["invested_capital"] - old["invested_capital"]
    if d_ic <= 0:
        return f"{head} (invested capital fell from FY{y0} to FY{y1})."
    if d_ic < MIN_IC_GROWTH * old["invested_capital"]:
        return (f"{head} (invested capital changed by less than {_pct(MIN_IC_GROWTH)} of its "
                f"starting level from FY{y0} to FY{y1}).")
    d_nopat = new["nopat"] - old["nopat"]
    moved = "up" if d_nopat >= 0 else "down"
    line = (f"- Incremental ROIC, FY{y0} to FY{y1}: {_ratio(d_nopat / d_ic)} (NOPAT {moved} "
            f"{_money(abs(d_nopat))}, invested capital up {_money(d_ic)}).")
    earned = sum(v["nopat"] for _, v in run[:-1])
    span = _span(y0 + 1, y1)
    if earned <= 0:
        return line + f" Reinvestment rate: not shown (NOPAT over {span} was not positive)."
    return line + (f" Reinvestment rate: {_ratio(d_ic / earned)} (rise in invested capital over "
                   f"NOPAT of {_money(earned)} in {span}).")


def _basis_line() -> str:
    return ("- Basis: buybacks and dividends are the cash paid in the year, read from cash-flow "
            "tags, and only the latest year's are on file. ROIC is operating income × "
            f"(1 − {_pct(ROIC_TAX_RATE)}) over total debt plus total equity at year end. "
            "Incremental ROIC and the reinvestment rate are shown only when invested capital "
            f"grew by at least {_pct(MIN_IC_GROWTH)} of its starting level.")


def _intro(fs: list[dict], ics: list[dict]) -> str:
    years = {y for r in (*fs, *ics) if isinstance(r, dict)
             and (y := _year(r.get("fiscal_year"))) is not None}
    span = (f"over {_span(min(years), max(years))} ({len(years)} fiscal "
            f"year{'s' if len(years) != 1 else ''})" if years else "for the latest fiscal year")
    return (f"Accounting data from SEC filings {span}. It shows where cash went and what the "
            "reported capital earned; it is not a judgement of returns or business quality.")


def _masked(card: Any) -> bool:
    return any(isinstance(a, dict) and a.get("field") in _SECTOR_LEGS
               and a.get("reason") == "inapplicable"
               for a in (getattr(card, "abstentions", None) or []))


def _section(card: Any) -> tuple[str, str]:
    if _masked(card):
        return NOT_SHOWN_SECTOR, NOT_SHOWN
    m = getattr(card, "metrics", None)
    fs = list(getattr(m, "financial_series", None) or [])
    ics = list(getattr(m, "invested_capital_series", None) or [])
    if m is None or not (fs or ics or _num(getattr(m, "repurchases", None)) is not None
                         or _num(getattr(m, "dividends_paid", None)) is not None):
        return NOT_SHOWN_NO_DATA, NOT_SHOWN
    cash, returns, shares, roic = _cash_line(fs), _returns_line(m, fs), _shares_line(fs), _roic_line(ics)
    if not any((cash, returns, shares, roic)):
        return NOT_SHOWN_NOTHING, NOT_SHOWN
    lines = [
        _intro(fs, ics),
        cash or ("- Cash flow: not shown (no fiscal year on file has both operating and free "
                 "cash flow)."),
        returns or ("- Buybacks and dividends: not shown (neither is tagged in the latest "
                    "cash-flow statement)."),
        shares or "- Diluted share count: not shown (fewer than two consecutive fiscal years on file).",
        roic or ("- ROIC: not shown (operating income, debt and equity do not all resolve for "
                 "any year with positive invested capital)."),
        _incremental_line(ics),
        _basis_line(),
    ]
    return "\n".join(lines), SHOWN


def section(card: Any) -> tuple[str, str]:
    """(body, status) for the brief. `body` is plain lines with "- " bullets; `status` is
    SHOWN or NOT_SHOWN. Never raises."""
    try:
        return _section(card)
    except Exception as e:  # one optional section must never cost a brief
        print(f"[capital-allocation] {getattr(card, 'ticker', '?')}: {redact_secrets(e)}",
              file=sys.stderr)
        return NOT_SHOWN_UNREADABLE, NOT_SHOWN
