"""SEC/EDGAR client library — importable, with no orchestrator on top of it.

These modules were the data-fetching leaves of the retired autonomous scout
(`docs/audits/2026-08-11-scout-retirement.md`). The nightly funnel that called them is
gone; the clients survive because the data is still worth reaching by hand during
research: what a marquee fund just filed, who bought insider stock, what 8-K items an
issuer reported.

**No production caller.** Nothing in `shortlist` imports these on the `/screen` or `/deep`
paths. CI keeps pinning their PARSE shapes; it does NOT catch SEC or edgartools changing
shape upstream, because the live fetch tests are `pytest.mark.live` + skipif on
`SEC_IDENTITY` and skip by default. Run `SEC_IDENTITY=... uv run pytest -m live` before
trusting a client after a long gap — `edgartools` concept drift has broken extraction once
already (`docs/audits/2026-07-31-edgar-concept-match.md`).

Every sec.gov caller here shares one process-wide ~6 req/s budget via `sec_throttle`.
Never give a client its own throttle — that broke the funnel outright on 2026-08-04
(`docs/audits/2026-08-05-discovery-funnel-audit.md`).
"""
