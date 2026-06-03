from __future__ import annotations

# Illustrative sample bundles for offline demo / tests. Prices, targets, and
# statement figures are approximate for late May 2026; some fields are rounded
# placeholders. NOT verified fundamentals — run a live source for real numbers.

from .models import (
    Analyst, Fundamentals, Insider, InsiderTxn, Price, Profile, Statements,
    TickerSnapshot,
)


def _snap(ticker, profile, fund, stmts, analyst, insider, price):
    def make(t: str) -> TickerSnapshot:
        s = TickerSnapshot(ticker=t)
        s.profile, s.fundamentals, s.statements = profile, fund, stmts
        s.analyst, s.insider, s.price = analyst, insider, price
        return s
    return make


SAMPLE = {
    "GEV": {
        "raw_echo": {"note": "illustrative", "ticker": "GEV"},
        "snapshot": _snap(
            "GEV",
            Profile(name="GE Vernova Inc.", sector="Industrials", industry="Electrical Equipment",
                    exchange="NYSE", currency="USD", country="US", market_cap=260e9, beta=1.3),
            Fundamentals(pe_ttm=28.2, peg=1.4, roe=0.44, roic=0.18, gross_margin=0.20,
                         net_margin=0.13, operating_margin=0.11, debt_to_equity=0.12,
                         interest_coverage=20.0, current_ratio=1.1, fcf_yield=0.022),
            Statements(fiscal_years=[2025, 2024, 2023], revenue=[38.07e9, 34.94e9, 33.24e9],
                       gross_profit=[7.6e9, 6.3e9, 5.4e9], net_income=[4.88e9, 1.55e9, -0.44e9],
                       operating_cash_flow=[3.9e9, 2.4e9, 1.3e9], free_cash_flow=[3.1e9, 1.7e9, 0.6e9],
                       total_debt=[0.6e9, 0.7e9, 0.9e9], total_equity=[10.5e9, 9.8e9, 9.2e9]),
            Analyst(buy=29, hold=6, sell=0, target_median=1217, target_high=1424, target_low=836),
            Insider(net_value_6m=-9.0e6, buy_count=1, sell_count=6, sentiment_mspr=-0.25,
                    recent=[InsiderTxn(date="2026-05-18", name="Officer", role="EVP",
                                       kind="sell", shares=8000, price=1000, value=-8.0e6)]),
            Price(price=969, ma50=1010, ma200=775, year_high=1181.95, year_low=458.65,
                  ret_1m=-0.04, ret_3m=0.06, ret_6m=0.20, ret_12m=1.05, rel_strength_6m=0.12),
        ),
    },
    "LMT": {
        "raw_echo": {"note": "illustrative", "ticker": "LMT"},
        "snapshot": _snap(
            "LMT",
            Profile(name="Lockheed Martin Corporation", sector="Industrials",
                    industry="Aerospace & Defense", exchange="NYSE", currency="USD",
                    country="US", market_cap=122e9, beta=0.5),
            Fundamentals(pe_ttm=20.0, peg=2.1, roe=0.80, roic=0.20, gross_margin=0.12,
                         net_margin=0.067, operating_margin=0.10, debt_to_equity=2.6,
                         interest_coverage=8.0, current_ratio=1.1, fcf_yield=0.046),
            Statements(fiscal_years=[2025, 2024, 2023], revenue=[75.05e9, 71.04e9, 67.57e9],
                       gross_profit=[9.0e9, 8.9e9, 8.6e9], net_income=[5.02e9, 5.34e9, 6.92e9],
                       operating_cash_flow=[7.0e9, 7.6e9, 7.9e9], free_cash_flow=[5.5e9, 6.2e9, 6.2e9],
                       total_debt=[19.6e9, 19.3e9, 17.3e9], total_equity=[6.7e9, 6.7e9, 6.8e9]),
            Analyst(buy=6, hold=14, sell=1, target_median=615, target_high=700, target_low=480),
            Insider(net_value_6m=-0.4e6, buy_count=0, sell_count=2, sentiment_mspr=0.0, recent=[]),
            Price(price=531, ma50=525, ma200=590, year_high=692.0, year_low=410.11,
                  ret_1m=-0.02, ret_3m=0.08, ret_6m=-0.05, ret_12m=-0.18, rel_strength_6m=-0.13),
        ),
    },
    "SCHW": {
        "raw_echo": {"note": "illustrative", "ticker": "SCHW"},
        "snapshot": _snap(
            "SCHW",
            Profile(name="The Charles Schwab Corporation", sector="Financial Services",
                    industry="Capital Markets", sic="6211", exchange="NYSE", currency="USD",
                    country="US", market_cap=152e9, beta=1.0),
            Fundamentals(pe_ttm=17.0, peg=0.9, roe=0.17, roic=None, gross_margin=None,
                         net_margin=0.36, operating_margin=0.45, debt_to_equity=None,
                         interest_coverage=None, current_ratio=None, fcf_yield=None),
            Statements(fiscal_years=[2025, 2024, 2023], revenue=[22.9e9, 19.6e9, 18.8e9],
                       gross_profit=[], net_income=[8.2e9, 5.9e9, 5.1e9],
                       operating_cash_flow=[9.0e9, 6.5e9, 6.0e9], free_cash_flow=[8.5e9, 6.0e9, 5.5e9],
                       total_debt=[40e9, 42e9, 38e9], total_equity=[48e9, 45e9, 41e9]),
            Analyst(buy=17, hold=2, sell=1, target_median=115, target_high=139, target_low=88),
            Insider(net_value_6m=-1.2e6, buy_count=1, sell_count=3, sentiment_mspr=-0.05, recent=[]),
            Price(price=87, ma50=90, ma200=92, year_high=98.0, year_low=66.0,
                  ret_1m=-0.06, ret_3m=-0.10, ret_6m=-0.12, ret_12m=0.05, rel_strength_6m=-0.20),
        ),
    },
    "TMO": {
        "raw_echo": {"note": "illustrative", "ticker": "TMO"},
        "snapshot": _snap(
            "TMO",
            Profile(name="Thermo Fisher Scientific Inc.", sector="Healthcare",
                    industry="Diagnostics & Research", exchange="NYSE", currency="USD",
                    country="US", market_cap=195e9, beta=0.9),
            Fundamentals(pe_ttm=22.5, peg=1.8, roe=0.13, roic=0.09, gross_margin=0.42,
                         net_margin=0.15, operating_margin=0.20, debt_to_equity=0.7,
                         interest_coverage=9.0, current_ratio=1.5, fcf_yield=0.045),
            Statements(fiscal_years=[2025, 2024, 2023], revenue=[43.5e9, 42.9e9, 42.7e9],
                       gross_profit=[18.3e9, 17.6e9, 17.4e9], net_income=[6.3e9, 6.0e9, 5.9e9],
                       operating_cash_flow=[8.6e9, 8.4e9, 8.5e9], free_cash_flow=[6.8e9, 6.9e9, 7.0e9],
                       total_debt=[30e9, 32e9, 34e9], total_equity=[38e9, 36e9, 34e9]),
            Analyst(buy=20, hold=4, sell=0, target_median=620, target_high=700, target_low=520),
            Insider(net_value_6m=-2.0e6, buy_count=0, sell_count=3, sentiment_mspr=0.05, recent=[]),
            Price(price=510, ma50=500, ma200=515, year_high=627.0, year_low=480.0,
                  ret_1m=0.02, ret_3m=0.0, ret_6m=-0.05, ret_12m=-0.08, rel_strength_6m=-0.10),
        ),
    },
    "GOOGL": {
        "raw_echo": {"note": "illustrative", "ticker": "GOOGL"},
        "snapshot": _snap(
            "GOOGL",
            Profile(name="Alphabet Inc.", sector="Communication Services",
                    industry="Internet Content & Information", exchange="NASDAQ", currency="USD",
                    country="US", market_cap=2.3e12, beta=1.05),
            Fundamentals(pe_ttm=24.0, peg=1.3, roe=0.32, roic=0.27, gross_margin=0.58,
                         net_margin=0.28, operating_margin=0.32, debt_to_equity=0.08,
                         interest_coverage=40.0, current_ratio=1.8, fcf_yield=0.035),
            Statements(fiscal_years=[2025, 2024, 2023], revenue=[380e9, 350e9, 307e9],
                       gross_profit=[220e9, 203e9, 174e9], net_income=[100e9, 94e9, 74e9],
                       operating_cash_flow=[120e9, 110e9, 102e9], free_cash_flow=[75e9, 69e9, 69e9],
                       total_debt=[13e9, 14e9, 14e9], total_equity=[320e9, 283e9, 256e9]),
            Analyst(buy=45, hold=8, sell=1, target_median=215, target_high=250, target_low=170),
            Insider(net_value_6m=-5.0e6, buy_count=0, sell_count=4, sentiment_mspr=-0.10, recent=[]),
            Price(price=190, ma50=185, ma200=175, year_high=208.0, year_low=140.0,
                  ret_1m=0.03, ret_3m=0.10, ret_6m=0.18, ret_12m=0.30, rel_strength_6m=0.10),
        ),
    },
}
