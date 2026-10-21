"""The "Capital allocation" section of the research brief: where the cash went and what the
reported capital earned, computed from the card's statement series.

DISPLAY ONLY, AND NEVER SHOWN TO THE MODEL. Computed after `assess()` returns and stored with
the brief; not in the prompt and not a grounding segment. Not a score, gate or flag. It reads
only data already on the card, so it makes no request.

Deliberately not in `cachekey._PROMPT_MODULES`: the brief stores the rendered section, so a
wording change here never invalidates a cached brief and reaches only briefs written after it.

The card does not record which source supplied the statements (EDGAR or FMP), so the wording
says "filed statements" and "not on file", never "SEC" or "not tagged".

Every line abstains on its own, with its reason; a name with nothing to show gets one
"Not shown: ..." sentence, never silence. NEVER RAISES (`section`)."""
from __future__ import annotations

import sys
from datetime import date
from typing import Any, Optional

from ..data.bridge import ROIC_TAX_RATE
from ..env import redact_secrets

HEADING = "Capital allocation (computed from filed statements — not LLM-generated)"
SHOWN, NOT_SHOWN = "shown", "not_shown"

# Below this growth of invested capital (a share of its starting level) ΔNOPAT / ΔIC is a ratio
# of two small differences, so the added-capital lines abstain.
MIN_IC_GROWTH = 0.05
# A resolved ratio past this magnitude is printed as a bound, not as a number.
_RATIO_CAP = 1.0

# A bank, insurer or REIT has these legs masked; its free cash flow and debt-plus-equity
# invested capital do not mean what they mean elsewhere.
_SECTOR_LEGS = ("roic", "fcf_yield")

NOT_SHOWN_SECTOR = "Not shown: not meaningful for this sector."
NOT_SHOWN_NO_DATA = "Not shown: no statement data on the card."
NOT_SHOWN_NOTHING = "Not shown: the statements on file do not resolve any of its lines."
NOT_SHOWN_UNREADABLE = "Not shown: the statement data on the card could not be read."

_PAYOUT_BASIS = ("- Basis: buybacks and dividends are the cash paid in the year, from the "
                 "cash-flow statement; only the latest year's are on file.")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _year(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _end(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _money(v: float) -> str:
    sign, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.1f}B"
    if a >= 0.5e6:
        return f"{sign}${a / 1e6:,.0f}M"
    return f"{sign}${a:,.0f}"


def _pct(x: float, places: int = 0) -> str:
    return f"{x * 100:.{places}f}%"


def _ratio(x: float) -> str:
    if x > _RATIO_CAP:
        return f"more than {_pct(_RATIO_CAP)}"
    if x < -_RATIO_CAP:
        return f"less than -{_pct(_RATIO_CAP)}"
    return _pct(x)


# A year is (fiscal_year, period_end or None). A December year end reads as "FY2025"; any other
# is named by its period end, because filers label those years inconsistently.
Year = tuple[int, Optional[date]]


def _dec(y: Year) -> bool:
    return y[1] is None or y[1].month == 12


def _when(y: Year) -> str:
    return f"FY{y[0]}" if _dec(y) else f"year ended {y[1]:%b %Y}"


def _span(first: Year, last: Year) -> str:
    if first == last:
        return _when(first)
    if _dec(first) and _dec(last):
        return f"FY{first[0]}–FY{last[0]}"
    if not _dec(first) and not _dec(last):
        return f"years ended {first[1]:%b %Y}–{last[1]:%b %Y}"
    return f"{_when(first)}–{_when(last)}"


def _the(y: Year) -> str:
    return _when(y) if _dec(y) else "the " + _when(y)


def _from_to(first: Year, last: Year) -> str:
    return f"from {_the(first)} to {_the(last)}"


def _run(rows: list, keys: tuple[str, ...]) -> list[tuple[Year, dict]]:
    """The newest run of consecutive fiscal years whose `keys` all resolve, newest-first."""
    out: list[tuple[Year, dict]] = []
    for r in rows:
        y = _year(r.get("fiscal_year")) if isinstance(r, dict) else None
        vals = {k: _num(r.get(k)) for k in keys} if isinstance(r, dict) else {}
        ok = y is not None and all(v is not None for v in vals.values())
        if not ok or (out and y != out[-1][0][0] - 1):
            if out:
                break
            continue
        out.append(((y, _end(r.get("period_end"))), vals))
    return out


def _cash_line(fs: list) -> Optional[str]:
    run = _run(fs, ("operating_cash_flow", "free_cash_flow"))
    if not run:
        return None
    ocf = sum(v["operating_cash_flow"] for _, v in run)
    fcf = sum(v["free_cash_flow"] for _, v in run)
    how = "summed" if len(run) > 1 else "only"
    return (f"- Cash, {_span(run[-1][0], run[0][0])} {how}: operating cash flow {_money(ocf)}, "
            f"capital spending {_money(ocf - fcf)}, free cash flow {_money(fcf)}.")


def _returns_line(m: Any, fs: list, with_share: bool) -> Optional[str]:
    buy, div = _num(getattr(m, "repurchases", None)), _num(getattr(m, "dividends_paid", None))
    if buy is None and div is None:
        return None
    latest = fs[0] if fs and isinstance(fs[0], dict) else {}
    year = _year(latest.get("fiscal_year"))
    label = (_when((year, _end(latest.get("period_end")))) if year is not None
             else "latest fiscal year")
    parts = [f"buybacks {_money(abs(buy))}" if buy is not None else "buybacks not on file",
             f"dividends {_money(abs(div))}" if div is not None else "dividends not on file"]
    head = f"- {label[0].upper()}{label[1:]} only: {', '.join(parts)}"
    if not with_share:
        return head + "."
    fcf = _num(latest.get("free_cash_flow"))
    if fcf is None:
        return head + "; share of free cash flow not shown (free cash flow not on file)."
    if fcf <= 0:
        return head + "; share of free cash flow not shown (free cash flow was not positive)."
    share = _pct((abs(buy or 0.0) + abs(div or 0.0)) / fcf)
    floor = "together" if buy is not None and div is not None else "at least"
    return head + f"; {floor} {share} of that year's free cash flow."


def _shares_line(fs: list) -> Optional[str]:
    run = _run(fs, ("diluted_shares",))
    if len(run) < 2 or run[-1][1]["diluted_shares"] <= 0:
        return None
    change = run[0][1]["diluted_shares"] / run[-1][1]["diluted_shares"] - 1.0
    shown = f"{change * 100:+.1f}%"
    if shown in ("+0.0%", "-0.0%"):
        shown, direction = "0.0%", "unchanged"
    else:
        direction = "fewer shares" if change < 0 else "more shares"
    return (f"- Diluted share count: {shown} {_from_to(run[-1][0], run[0][0])} "
            f"({direction}).")


def _roic_line(ics: list) -> Optional[str]:
    by_year: dict[Year, float] = {}
    for r in ics:
        if not isinstance(r, dict):
            continue
        y, nopat, ic = _year(r.get("fiscal_year")), _num(r.get("nopat")), _num(r.get("invested_capital"))
        if y is not None and nopat is not None and ic is not None and ic > 0:
            by_year[(y, _end(r.get("period_end")))] = nopat / ic
    if not by_year:
        return None
    return "- ROIC: " + ", ".join(f"{_when(y)} {_pct(by_year[y], 1)}"
                                  for y in sorted(by_year)) + "."


def _added_capital_line(ics: list) -> str:
    head = "- Return on added capital: not shown"
    run = _run(ics, ("nopat", "invested_capital"))
    if len(run) < 2:
        return f"{head} (fewer than two consecutive fiscal years of ROIC inputs)."
    (y1, new), (y0, old) = run[0], run[-1]
    d_ic = new["invested_capital"] - old["invested_capital"]
    if d_ic <= 0:
        return f"{head} (invested capital fell {_from_to(y0, y1)})."
    if d_ic < MIN_IC_GROWTH * old["invested_capital"]:
        return (f"{head} (invested capital changed by less than {_pct(MIN_IC_GROWTH)} of its "
                f"starting level {_from_to(y0, y1)}).")
    d_nopat = new["nopat"] - old["nopat"]
    moved = "up" if d_nopat >= 0 else "down"
    line = (f"- Return on added capital, {_when(y0)} to {_when(y1)}: {_ratio(d_nopat / d_ic)} "
            f"(NOPAT {moved} {_money(abs(d_nopat))}, invested capital up {_money(d_ic)}).")
    earned = sum(v["nopat"] for _, v in run[:-1])
    span = _span(run[-2][0], y1)
    if earned <= 0:
        return line + f" Added capital / NOPAT: not shown (NOPAT over {span} was not positive)."
    return line + f" Added capital / NOPAT: {_ratio(d_ic / earned)} (NOPAT of {_money(earned)} in {span})."


def _basis_line() -> str:
    return (_PAYOUT_BASIS + " ROIC is operating income × "
            f"(1 − {_pct(ROIC_TAX_RATE)}) over total debt plus total equity at year end. "
            "Invested capital includes cash held and acquired goodwill, so a change in it can "
            "reflect cash raised or retained, not only investment. The added-capital lines are "
            f"shown only when invested capital grew by at least {_pct(MIN_IC_GROWTH)} of its "
            "starting level.")


def _intro(fs: list, ics: list) -> str:
    years = {(y, _end(r.get("period_end"))) for r in (*fs, *ics) if isinstance(r, dict)
             and (y := _year(r.get("fiscal_year"))) is not None}
    by_fy = {y: (y, e) for y, e in sorted(years, key=lambda t: (t[0], t[1] is not None))}
    if by_fy:
        first, last = by_fy[min(by_fy)], by_fy[max(by_fy)]
        n = len(by_fy)
        span = f"over {_span(first, last)} ({n} fiscal year{'s' if n != 1 else ''})"
    else:
        span = "for the latest fiscal year"
    return (f"Accounting data from the company's filed statements {span}. It is not a "
            "judgement of returns or business quality.")


def _masked(card: Any) -> bool:
    return any(isinstance(a, dict) and a.get("field") in _SECTOR_LEGS
               and a.get("reason") == "inapplicable"
               for a in (getattr(card, "abstentions", None) or []))


def _section(card: Any) -> tuple[str, str]:
    m = getattr(card, "metrics", None)
    fs = list(getattr(m, "financial_series", None) or [])
    ics = list(getattr(m, "invested_capital_series", None) or [])
    masked = _masked(card)
    if m is None or not (fs or ics or _num(getattr(m, "repurchases", None)) is not None
                         or _num(getattr(m, "dividends_paid", None)) is not None):
        return (NOT_SHOWN_SECTOR if masked else NOT_SHOWN_NO_DATA), NOT_SHOWN
    returns, shares = _returns_line(m, fs, with_share=not masked), _shares_line(fs)
    no_returns = "- Buybacks and dividends: not shown (not on file for the latest year)."
    no_shares = "- Diluted share count: not shown (fewer than two consecutive fiscal years on file)."
    if masked:
        if not (returns or shares):
            return NOT_SHOWN_SECTOR, NOT_SHOWN
        return "\n".join([
            _intro(fs, []), returns or no_returns, shares or no_shares,
            "- Cash-flow sums, payout share of free cash flow, ROIC and added capital: not shown "
            "(not meaningful for this sector).",
            _PAYOUT_BASIS]), SHOWN
    cash, roic = _cash_line(fs), _roic_line(ics)
    if not any((cash, returns, shares, roic)):
        return NOT_SHOWN_NOTHING, NOT_SHOWN
    return "\n".join([
        _intro(fs, ics),
        cash or ("- Cash flow: not shown (no fiscal year on file has both operating and free "
                 "cash flow)."),
        returns or no_returns,
        shares or no_shares,
        roic or ("- ROIC: not shown (operating income, debt and equity do not all resolve for "
                 "any year with positive invested capital)."),
        _added_capital_line(ics),
        _basis_line(),
    ]), SHOWN


def section(card: Any) -> tuple[str, str]:
    """(body, status) for the brief. `body` is plain lines with "- " bullets; `status` is
    SHOWN or NOT_SHOWN. Never raises."""
    try:
        return _section(card)
    except Exception as e:  # one optional section must never cost a brief
        print(f"[capital-allocation] {getattr(card, 'ticker', '?')}: {redact_secrets(e)}",
              file=sys.stderr)
        return NOT_SHOWN_UNREADABLE, NOT_SHOWN
