from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from ...stats import residual_momentum as _stats_residual_momentum
from ..models import Price, TickerSnapshot

# --- Yahoo price math (pure, unit-tested) ---------------------------------

_YH_SIX_MONTHS = 126   # ~trading days in 6 months
_YH_VOL_WINDOW = 252   # ~trading days in 1 year


def _yh_sma(xs: list[float], n: int) -> Optional[float]:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _yh_ret_over(xs: list[float], n: int) -> Optional[float]:
    return xs[-1] / xs[-1 - n] - 1.0 if len(xs) > n and xs[-1 - n] else None


def ret_between(xs: list[float], start_back: int, end_back: int) -> Optional[float]:
    """Return over a window of a price series: xs[-end_back] / xs[-start_back] - 1.

    `start_back` is the older endpoint, `end_back` the newer one (`end_back=1` is the
    latest close). A zero denominator or a too-short series gives None. `(127, 1)` is
    the trailing 6m return; `(274, 22)` is the 12-1 return."""
    if end_back < 1 or start_back <= end_back or len(xs) < start_back:
        return None
    denom = xs[-start_back]
    if not denom:
        return None
    return xs[-end_back] / denom - 1.0


# Pure functions over a daily adjusted-close series, oldest -> newest.
_MOM_SKIP = 22          # skip the most recent ~21 trading days (1m reversal guard)
_MOM_12_1_BACK = 274    # 274 - 22 == 252 td (12-month) formation window


def mom_6m(closes: list[float]) -> Optional[float]:
    """Absolute trailing 6-month return (== _yh_ret_over(closes, _YH_SIX_MONTHS))."""
    return ret_between(closes, _YH_SIX_MONTHS + 1, 1)


def mom_12_1(closes: list[float]) -> Optional[float]:
    """Canonical 12-1 momentum: 252-td formation return ending ~21 td (one month) back,
    skipping the most recent month to avoid short-term reversal."""
    return ret_between(closes, _MOM_12_1_BACK, _MOM_SKIP)


def _yh_annualized_vol(xs: list[float], window: int = _YH_VOL_WINDOW) -> Optional[float]:
    rets = [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs)) if xs[i - 1]][-window:]
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5)


def _yh_max_drawdown(xs: list[float], window: int = _YH_VOL_WINDOW) -> Optional[float]:
    s = xs[-window:]
    if len(s) < 2:
        return None
    peak = s[0]
    mdd = 0.0
    for px in s:
        peak = max(peak, px)
        if peak:
            mdd = min(mdd, px / peak - 1.0)
    return mdd


# --- Price-refinement measurement axes (backtest-only) ---
# They read only trailing windows, so they are point-in-time when the caller passes closes
# truncated to as_of. No production leg reads them.
_PCT_52W_HIGH_WINDOW = 252        # ~trading days in 52 weeks
_PCT_52W_HIGH_MIN_HISTORY = 200   # ~a full year before calling it a "52-week" high
_MAX_RET_WINDOW = 21              # ~trading days in 1 month (MAX-effect formation)
_VOL_SCALE_VOL_WINDOW = 126       # 6-month scaler, not the 252-day risk default
_VOL_FLOOR = 1e-4                 # annualized-vol floor: at/below this, vol_scaled abstains


def pct_to_52w_high(closes: list[float]) -> Optional[float]:
    """Nearness to the trailing 52-week high (George-Hwang 2004): closes[-1] / max(last
    252), in (0, 1]. Abstains below _PCT_52W_HIGH_MIN_HISTORY closes and on a
    non-positive window max. With 200-251 closes the high spans less than 52 weeks."""
    if len(closes) < _PCT_52W_HIGH_MIN_HISTORY:
        return None
    hi = max(closes[-_PCT_52W_HIGH_WINDOW:])
    if hi <= 0:
        return None
    return closes[-1] / hi


def max_daily_return(closes: list[float], window: int = _MAX_RET_WINDOW) -> Optional[float]:
    """Largest single-day simple return over the trailing `window` days (the Bali-
    Cakici-Whitelaw "MAX effect", a negative return predictor). A non-positive prior
    close contributes 0.0, as in _yh_annualized_vol. Abstains on fewer than `window`
    returns."""
    if len(closes) < window + 1:
        return None
    seg = closes[-(window + 1):]
    rets = [seg[i] / seg[i - 1] - 1.0 if seg[i - 1] > 0 else 0.0 for i in range(1, len(seg))]
    return max(rets)


def vol_scaled_momentum(closes: list[float]) -> Optional[float]:
    """Risk-managed momentum (Barroso-Santa-Clara 2015): 12-1 momentum / trailing 6-month
    annualized realized vol. Abstains when mom_12_1 is unavailable or the vol is None or
    <= _VOL_FLOOR (a near-flat window would give a huge finite value that pollutes the
    rank IC). Cross-sectionally this is risk-adjusted raw momentum; the paper's gain is
    time-series vol-targeting, so a null rank IC does not refute it."""
    mom = mom_12_1(closes)
    if mom is None:
        return None
    vol = _yh_annualized_vol(closes, _VOL_SCALE_VOL_WINDOW)
    if vol is None or vol <= _VOL_FLOOR:
        return None
    return mom / vol


def _closes_from_chart(raw: Any) -> list[float]:
    try:
        result = raw["chart"]["result"][0]
        series = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return []
    return [c for c in series if isinstance(c, (int, float))]


def _chart_ts_and_series(raw: Any) -> tuple[Optional[list], Optional[list]]:
    """The chart payload's (timestamp, adjclose) arrays as a pair, or (None, None) on a
    malformed shape. Not used by `_closes_from_chart`, which must accept a payload with
    closes but no timestamps (older cached payloads)."""
    try:
        result = raw["chart"]["result"][0]
        return result["timestamp"], result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return None, None


def _dates_from_chart(raw: Any) -> list[date]:
    """Bar dates aligned 1:1 to `_closes_from_chart(raw)`, oldest->newest. Returns []
    when timestamps are absent or misaligned, so the caller falls back to the date-less
    path."""
    ts, series = _chart_ts_and_series(raw)
    if not ts or not series or len(ts) != len(series):
        return []
    out: list[date] = []
    for t, c in zip(ts, series, strict=False):
        if not isinstance(c, (int, float)):
            continue  # mirror _closes_from_chart: drop non-numeric closes + their date
        out.append(datetime.fromtimestamp(t, tz=timezone.utc).date())
    return out


def _monthly_closes_from_chart(raw: Any) -> list[list]:
    """Down-sample to the last valid close of each calendar month, oldest->newest as
    [iso, close]. Returns [] if timestamps are absent or the payload is malformed."""
    ts, series = _chart_ts_and_series(raw)
    if not ts or not series:
        return []
    by_month: dict[str, list] = {}
    for t, c in zip(ts, series, strict=False):
        if not isinstance(c, (int, float)):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date()
        by_month[f"{d.year}-{d.month:02d}"] = [d.isoformat(), float(c)]
    return [by_month[k] for k in sorted(by_month)]


def _normalize_yahoo(ticker: str, closes: list[float], spy_closes: list[float],
                     dates: Optional[list] = None,
                     spy_dates: Optional[list] = None) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    if not closes:
        return snap
    stock_6m = _yh_ret_over(closes, _YH_SIX_MONTHS)
    spy_6m = _yh_ret_over(spy_closes, _YH_SIX_MONTHS) if spy_closes else None
    rel = stock_6m - spy_6m if (stock_6m is not None and spy_6m is not None) else None
    # Residual momentum needs date-aligned stock and SPY series; the date-less path leaves it None.
    resid = None
    if dates is not None and spy_dates is not None:
        resid = _stats_residual_momentum(dates, closes, spy_dates, spy_closes)
    snap.price = Price(
        price=closes[-1],
        ma200=_yh_sma(closes, 200),
        ret_6m=stock_6m,
        rel_strength_6m=rel,
        realized_vol=_yh_annualized_vol(closes),
        max_drawdown=_yh_max_drawdown(closes),
        residual_momentum=resid,
        pct_to_52w_high=pct_to_52w_high(closes),
        max_daily_return=max_daily_return(closes),
        vol_scaled_momentum=vol_scaled_momentum(closes),
    )
    return snap


def snapshot_from_closes(ticker: str, closes: list[float],
                         spy_closes: list[float]) -> TickerSnapshot:
    """Public seam: a point-in-time Price snapshot from a close series, using the live
    Yahoo math. Pass closes truncated to the as-of date to avoid look-ahead.
    Date-less: residual_momentum stays None. Use snapshot_from_closes_dated for it."""
    return _normalize_yahoo(ticker, closes, spy_closes)


def snapshot_from_closes_dated(ticker: str, dates: list, closes: list[float],
                               spy_dates: list, spy_closes: list[float]) -> TickerSnapshot:
    """Like snapshot_from_closes, plus residual momentum. The series are date-inner-
    joined in stats.residual_momentum, so they need not share length or listing dates.
    Pass both truncated to as_of to avoid look-ahead."""
    return _normalize_yahoo(ticker, closes, spy_closes, dates, spy_dates)

