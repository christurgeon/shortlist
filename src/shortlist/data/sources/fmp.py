from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from ..._util import first as _first
from ..._util import pct as _pct
from ...providers._fmp_insider import classify_tx, tx_value
from ...stats import avg_roic, median_pe
from ..models import (
    Analyst,
    Fundamentals,
    Insider,
    InsiderTxn,
    Price,
    Profile,
    SourceResult,
    Statements,
    TickerSnapshot,
)
from .base import _fetch_sections, _KeyedHttpSource

# --- FMP: primary, broad coverage ----------------------------------------

# Cap on valued insider trades netted into net_value_6m (filters run before it).
#
# Unverified assumption: truncation keeps the rows FMP returned first, which is safe only
# if the endpoint is "most recent N" as its docs imply. On a reversed feed the retained
# set would be the oldest trades. The 183-day window bounds the damage. Re-check before
# trusting FMP insider data on a paid tier. Truncation past this cap is silent.
_MAX_TRADES = 60


def _sum_or_none(*vals: Optional[float]) -> Optional[float]:
    """Sum values, treating None as 0. Returns None only when every value is None;
    a sum of real zeros (unanimous buy ratings) is complete data."""
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


class FMPSource(_KeyedHttpSource):
    name = "fmp"
    # `/stable/` only: the legacy `/v3`-`/v4` endpoints were retired for new keys on
    # 2025-08-31. Every endpoint takes `?symbol=`.
    BASE = "https://financialmodelingprep.com/stable"
    _AUTH_PARAM = "apikey"
    _ENV_VAR = "FMP_API_KEY"
    _PROVIDER = "fmp"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *,
                 cache=None, config: Optional[dict] = None):
        super().__init__(api_key, timeout, cache=cache)
        # Per-minute 429s clear on backoff; daily-quota 429s do not, so max_retries is small.
        fmp_cfg = (config or {}).get("fmp") or {}
        self._max_retries = int(fmp_cfg.get("max_retries", 2))
        # The insider endpoint is paid (402 on free plans). config.yaml sets
        # fetch_insider: false to save the quota call. An absent key defaults to True.
        self._fetch_insider = bool(fmp_cfg.get("fetch_insider", True))

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        sections = {
            "profile": ("profile", {"symbol": ticker}),
            "quote": ("quote", {"symbol": ticker}),
            "ratios_ttm": ("ratios-ttm", {"symbol": ticker}),
            "ratios_annual": ("ratios", {"symbol": ticker, "period": "annual", "limit": 5}),
            "key_metrics_ttm": ("key-metrics-ttm", {"symbol": ticker}),
            "key_metrics_annual": ("key-metrics", {"symbol": ticker, "period": "annual", "limit": 5}),
            "income": ("income-statement", {"symbol": ticker, "period": "annual", "limit": 5}),
            "balance": ("balance-sheet-statement", {"symbol": ticker, "period": "annual", "limit": 5}),
            "cashflow": ("cash-flow-statement", {"symbol": ticker, "period": "annual", "limit": 5}),
            "price_target": ("price-target-consensus", {"symbol": ticker}),
            "grades": ("grades-consensus", {"symbol": ticker}),
            "insider": ("insider-trading/search", {"symbol": ticker, "page": 0, "limit": 100}),
            "price_change": ("stock-price-change", {"symbol": ticker}),
        }
        if not self._fetch_insider:
            del sections["insider"]
        await _fetch_sections(res, self._get, sections)
        res.partial = _normalize_fmp(ticker, res.raw)
        return res


def _normalize_fmp(ticker: str, raw: dict[str, Any]) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    p = _first(raw.get("profile"))
    if p:
        snap.profile = Profile(
            name=p.get("companyName"), sector=p.get("sector"),
            industry=p.get("industry"), exchange=p.get("exchange"),
            currency=p.get("currency"), country=p.get("country"),
            market_cap=p.get("marketCap"), beta=p.get("beta"),
            description=p.get("description"),
        )
    # PE/PEG come from ratios and ROE/ROIC from key-metrics in the /stable/ API.
    ratios = _first(raw.get("ratios_ttm"))
    km = _first(raw.get("key_metrics_ttm"))
    if ratios or km:
        ratios, km = ratios or {}, km or {}
        ratios_hist = raw.get("ratios_annual")
        km_hist = raw.get("key_metrics_annual")
        snap.fundamentals = Fundamentals(
            pe_ttm=ratios.get("priceToEarningsRatioTTM"),
            pe_median_5y=median_pe(
                [r.get("priceToEarningsRatio") for r in ratios_hist]
            ) if isinstance(ratios_hist, list) else None,
            peg=ratios.get("priceToEarningsGrowthRatioTTM"),
            roe=km.get("returnOnEquityTTM"),
            roic=km.get("returnOnInvestedCapitalTTM"),
            roic_5y_avg=avg_roic(
                [r.get("returnOnInvestedCapital") for r in km_hist]
            ) if isinstance(km_hist, list) else None,
            gross_margin=ratios.get("grossProfitMarginTTM"),
            net_margin=ratios.get("netProfitMarginTTM"),
            operating_margin=ratios.get("operatingProfitMarginTTM"),
            debt_to_equity=ratios.get("debtToEquityRatioTTM"),
            interest_coverage=ratios.get("interestCoverageRatioTTM"),
            current_ratio=ratios.get("currentRatioTTM"),
            fcf_yield=km.get("freeCashFlowYieldTTM"),
        )
    inc, bal, cf = raw.get("income"), raw.get("balance"), raw.get("cashflow")
    if isinstance(inc, list) and inc:
        snap.statements = Statements(
            fiscal_years=[_year(r.get("date")) for r in inc],
            revenue=[r.get("revenue") for r in inc],
            gross_profit=[r.get("grossProfit") for r in inc],
            net_income=[r.get("netIncome") for r in inc],
            operating_cash_flow=[(_match(cf, r) or {}).get("operatingCashFlow") for r in inc],
            free_cash_flow=[(_match(cf, r) or {}).get("freeCashFlow") for r in inc],
            total_debt=[(_match(bal, r) or {}).get("totalDebt") for r in inc],
            total_equity=[(_match(bal, r) or {}).get("totalStockholdersEquity") for r in inc],
            operating_income=[r.get("operatingIncome") for r in inc],
            dep_amort=[r.get("depreciationAndAmortization") for r in inc],
            interest_expense=[r.get("interestExpense") for r in inc],
            ebitda=[r.get("ebitda") for r in inc],
            cash_and_equivalents=[(_match(bal, r) or {}).get("cashAndCashEquivalents") for r in inc],
        )
    pt = _first(raw.get("price_target"))
    grades = _first(raw.get("grades"))
    if pt or grades:
        pt, grades = pt or {}, grades or {}
        snap.analyst = Analyst(
            target_median=pt.get("targetMedian") or pt.get("targetConsensus"),
            target_high=pt.get("targetHigh"),
            target_low=pt.get("targetLow"),
            buy=_sum_or_none(grades.get("strongBuy"), grades.get("buy")),
            hold=grades.get("hold"),
            sell=_sum_or_none(grades.get("sell"), grades.get("strongSell")),
            consensus=grades.get("consensus"),
        )
    q = _first(raw.get("quote")) or {}
    chg = _first(raw.get("price_change")) or {}
    if q or chg:
        snap.price = Price(
            price=q.get("price"), ma50=q.get("priceAvg50"), ma200=q.get("priceAvg200"),
            year_high=q.get("yearHigh"), year_low=q.get("yearLow"),
            ret_1m=_pct(chg.get("1M")), ret_3m=_pct(chg.get("3M")),
            ret_6m=_pct(chg.get("6M")), ret_12m=_pct(chg.get("1Y")),
        )
    insiders = raw.get("insider")
    if isinstance(insiders, list) and insiders:
        # Window before netting: the endpoint returns the most recent N transactions with
        # no date scope, which can span years. Undated rows are dropped. The 183d window
        # matches EdgarSource's lookback, so _merge_insider compares the same period.
        cutoff = (date.today() - timedelta(days=183)).isoformat()
        insiders = [tx for tx in insiders
                    if (tx.get("transactionDate") or "") >= cutoff]
        # Keep only valued trades, before the cap. Non-trades (awards, exercises, gifts,
        # withholding, conversions) carry no signal. A row with no usable price or share
        # count is dropped, not counted at zero:
        #
        # 1. Counting it would let `sell_count` describe trades excluded from
        #    `net_value_6m` and would put value=0 rows in `recent`.
        # 2. An all-unvaluable batch must abstain. A fabricated net_value_6m == 0 is
        #    "present" and would win _merge_insider (fmp precedes edgar) over EDGAR's
        #    real aggregate.
        #
        # A batch of one valued trade still emits that trade's value: honest, not
        # representative. EDGAR outranks FMP (`collector.DEFAULT_PRIORITY`), so this
        # figure is used only when EDGAR has no trades.
        #
        # `> 0` (not `is not None`) is load-bearing: tx_value has no sign handling, so a
        # negative share count would make a sale add to net buying.
        #
        # Filtering before the cap also stops a burst of RSU awards from crowding real
        # purchases out of it.
        trades = [(tx, c, v) for tx in insiders
                  if (c := classify_tx(tx)) != "other" and (v := tx_value(tx)) > 0]
        if trades:
            net = buys = sells = 0
            recent = []
            for tx, classification, val in trades[:_MAX_TRADES]:
                buy = classification == "buy"
                net += val if buy else -val
                buys += buy
                sells += not buy
                if len(recent) < 10:
                    recent.append(InsiderTxn(
                        date=tx.get("transactionDate"), name=tx.get("reportingName"),
                        role=tx.get("typeOfOwner"), kind="buy" if buy else "sell",
                        shares=tx.get("securitiesTransacted"), price=tx.get("price"), value=val,
                    ))
            # Every kept row is a valued trade, so a non-empty `trades` is the abstain guard.
            snap.insider = Insider(net_value_6m=net, buy_count=buys, sell_count=sells, recent=recent)
    return snap


def _year(d: Any) -> Optional[int]:
    try:
        return int(str(d)[:4])
    except (TypeError, ValueError):
        return None


def _match(rows: Any, income_row: dict) -> Optional[dict]:
    if not isinstance(rows, list):
        return None
    for r in rows:
        if r.get("date") == income_row.get("date"):
            return r
    return None
