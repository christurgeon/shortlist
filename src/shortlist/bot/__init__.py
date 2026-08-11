"""Telegram bot surface: /screen, /deep, and the position monitor.

The report renderer under `report/` and the run model in `models.py` are shaped by their
scout-orchestrator origins (funnel counts, per-signal status). The bot fabricates an
interactive `RunManifest` with `signals=[]` to satisfy that API — see
`telegram.py:_interactive_manifest`. Trimming the report down to what /screen and /deep
actually render is tracked follow-up work.
"""
