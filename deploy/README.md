# Snapshot accumulation — deploy (DISABLED by default)

> **These units are NOT installed and NOT enabled.** They are a sample.
> Snapshot accumulation only happens when *you* run `shortlist-accumulate run`
> manually, or after *you* explicitly enable the timer below. Nothing here starts
> capturing on its own.

## Why accumulate

The backtest's snapshot-replay and weight-fitting paths
(`shortlist.backtest`, `ASSESSMENT_GAPS.md` §2.1 Phase 2) are built but **guarded**:
they stay dormant until the store has ≥ 24 organically-captured daily snapshots.
This job builds that history. Check progress any time:

```bash
uv run shortlist-accumulate status --root <STORE_ROOT>
# -> distinct capture dates: N / 24 needed -> snapshot backtest READY|NOT READY
```

## Run it once (manual, no scheduling)

```bash
uv run shortlist-accumulate run --root <STORE_ROOT>          # default watchlist + sources
uv run shortlist-accumulate run --tickers AAPL,MSFT --sources finnhub,yahoo --root <STORE_ROOT>
```

It is idempotent: re-running on the same day skips already-captured tickers and
spends no API calls.

## Free-tier caveat (read before scaling the watchlist)

The harness makes ~13 FMP calls/ticker; FMP's free tier is **250/day ≈ 19
tickers/day**, so `--max-tickers` defaults to **15**. To capture a larger universe
daily you need FMP's paid Starter tier (~$14–20/mo) or the caching layer — or drop
FMP (`--sources finnhub,edgar,yahoo`) and accept a null `value` axis. Finnhub
(60/min) and Yahoo (keyless) are comfortable either way.

## Enabling the daily timer (opt-in — only when you decide to)

1. Edit `<REPO_ROOT>` and `<STORE_ROOT>` in `shortlist-accumulate.service`.
2. Install as a **user** unit (no root needed):
   ```bash
   mkdir -p ~/.config/systemd/user
   cp shortlist-accumulate.service shortlist-accumulate.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now shortlist-accumulate.timer   # <-- this is the opt-in
   systemctl --user list-timers | grep shortlist-accumulate
   ```
3. To stop: `systemctl --user disable --now shortlist-accumulate.timer`.

(`loginctl enable-linger $USER` keeps user timers firing when you're not logged in.)
