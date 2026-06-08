from shortlist.providers.fmp import FMPProvider


class _FakeFMP(FMPProvider):
    def __init__(self, income):
        self._income = income
        self.key = "x"
        self.fetch_insider = False
        self.timeout = 15
        self.max_retries = 2
        self.cache = None

    def _get(self, path, **kw):
        if path == "income-statement":
            return self._income
        return []


def test_revenue_and_ebitda_from_income_statement():
    income = [{"revenue": 1000.0, "ebitda": 250.0, "grossProfit": 400.0, "netIncome": 90.0},
              {"revenue": 900.0, "ebitda": 220.0, "grossProfit": 360.0, "netIncome": 80.0},
              {"revenue": 800.0, "ebitda": 200.0, "grossProfit": 320.0, "netIncome": 70.0}]
    m = _FakeFMP(income).fetch("AAPL")
    assert m.revenue == 1000.0
    assert m.ebitda == 250.0
