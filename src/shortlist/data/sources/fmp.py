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

# Cap on insider transactions netted into net_value_6m. Since the non-trade and
# unvaluable filters now run BEFORE it, this bounds REAL TRADES (it used to bound raw
# rows, most of which could be awards). Restored as a named constant when the old
# `_fmp_insider._WINDOW` was deleted with its dead netting helper — a bare `60` in the
# loop left the magnitude unexplained.
#
# KNOWN, UNVERIFIED ASSUMPTION: truncation keeps whichever rows FMP returned first, so
# it is only safe if the endpoint really is "most recent N" as its docs imply. We have
# no recorded payload to confirm that ordering (fetch_insider ships false, so nothing is
# cached) — same class of assumption, and same blast radius, as classify_tx's
# `<CODE>-<Description>` split. On a reversed feed the retained set would be the OLDEST
# trades. The 183-day window bounds the damage; re-check before trusting FMP insider
# data on a paid tier. Truncation past this cap is currently silent.
_MAX_TRADES = 60


def _sum_or_none(*vals: Optional[float]) -> Optional[float]:
    """Sum values, treating a missing (None) one as 0 — but return None only when
    EVERY value is missing, never when the sum is a legitimate 0 (e.g. unanimous
    buy ratings: sell=strongSell=0 is complete data, not an absent field)."""
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


class FMPSource(_KeyedHttpSource):
    name = "fmp"
    # FMP's `/stable/` API; the legacy `/v3`–`/v4` endpoints were retired for
    # new keys on 2025-08-31. Every endpoint takes `?symbol=`.
    BASE = "https://financialmodelingprep.com/stable"
    _AUTH_PARAM = "apikey"
    _ENV_VAR = "FMP_API_KEY"
    _PROVIDER = "fmp"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, *,
                 cache=None, config: Optional[dict] = None):
        super().__init__(api_key, timeout, cache=cache)
        # FMP opts into Retry-After-aware retry (per-minute 429s clear on backoff; a
        # recovered call caches a real 200, an exhausted 429 -> coverage rate_limited_429,
        # a persistent 5xx -> the generic "error" status). Daily-quota 429s won't clear,
        # so max_retries is deliberately small (config: fmp.max_retries, default 2).
        fmp_cfg = (config or {}).get("fmp") or {}
        self._max_retries = int(fmp_cfg.get("max_retries", 2))
        # The /stable/ insider endpoint is PAID (402 on free plans) — config.yaml
        # ships fetch_insider: false so the guaranteed-to-fail request stops
        # burning ~1 of the ~13 quota calls/ticker. Default True: an absent key
        # keeps the historical fetch-everything behavior.
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
            del sections["insider"]     # paid endpoint disabled -> save the quota call
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
        # WINDOW the raw list before netting: the endpoint returns "the most recent
        # N transactions" with no date scope, which for a low-velocity name spans
        # years — un-windowed, the sum mislabels itself as net_value_6m. Undated
        # rows are dropped (can't be confirmed in-window; ISO dates compare
        # lexicographically). The window matches EdgarSource's lookback (183d) so
        # the two sources' figures describe the same period in _merge_insider.
        cutoff = (date.today() - timedelta(days=183)).isoformat()
        insiders = [tx for tx in insiders
                    if (tx.get("transactionDate") or "") >= cutoff]
        # Keep only VALUED TRADES — one predicate, applied BEFORE the cap.
        #
        # Non-trades (awards/exercises/gifts/tax-withholding/conversions) carry no
        # insider signal. A row we cannot value (missing/zero/negative price or share
        # count) is dropped ENTIRELY rather than counted at zero. Two reasons:
        #
        # 1. COHERENCE. Counting an unvalued row lets `sell_count` describe transactions
        #    deliberately excluded from `net_value_6m`, and pollutes `recent` — which
        #    feeds the research.insider_detail context line — with value=0 rows. The
        #    emitted record now describes exactly the trades it valued.
        # 2. THE FABRICATED-ZERO CLOBBER. An all-unvaluable batch used to emit
        #    net_value_6m == 0, and `_is_present(0)` is True, so that fabricated zero won
        #    _merge_insider wholesale (fmp precedes edgar) and discarded EDGAR's real
        #    aggregate. Such a batch now abstains, and EDGAR wins.
        #
        # SCOPE — do not over-read this. It does NOT stop FMP outranking EDGAR when FMP
        # can value only a little: 1 priced $1 buy + 59 unpriced sales still emits
        # net_value_6m == +1.0 and still wins the merge over EDGAR's −$4M. That figure is
        # now at least HONEST (it describes exactly one valued trade, with sell_count 0
        # rather than 59) instead of incoherent, and the scored net is unchanged either
        # way — but "should fmp outrank edgar for the insider txn group at all?" is a
        # separate, already-logged design question (TODO.md 2026-08-04), deliberately not
        # smuggled in here as a coverage heuristic.
        #
        # `> 0` (not `is not None`) is load-bearing: tx_value is shares*price with no
        # sign handling, so a negative share count would otherwise net WITH the buy/sell
        # sign applied — making a sale ADD to net insider buying.
        #
        # Filtering before the cap also stops a burst of RSU vesting from starving real
        # purchases out of it (59 award rows + 5 purchases => 4 purchases silently lost).
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
            # No separate presence flag: every kept row is a valued trade, so a
            # non-empty `trades` IS the abstain guard.
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
