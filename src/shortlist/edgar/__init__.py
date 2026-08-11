"""SEC/EDGAR clients: filings, 13F holdings, Form 4 insider trades, 8-K items.

Importable, with no production caller — `/screen` and `/deep` do not use them. CI pins the
parse shapes; the live fetch tests are `pytest.mark.live` and skip by default, so run
`SEC_IDENTITY=... pytest -m live` before trusting a client after a long gap.

Every sec.gov caller here shares one process-wide rate budget via `sec_throttle`.
"""
