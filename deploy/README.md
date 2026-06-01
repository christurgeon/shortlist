# Deploy units

This directory holds **sample** systemd units for the optional background jobs.
**None are auto-installed or enabled** — copy them manually after reviewing paths.
Two independent jobs live here:

- **Autonomous scout** (`shortlist-scout.{service,timer}`) — daily candidate
  discovery + ranked Telegram report. See [Autonomous Scout](#autonomous-scout).
- **Snapshot accumulation** (`shortlist-accumulate.{service,timer}`) — builds the
  daily-snapshot history the backtest replay needs. See [Snapshot accumulation](#snapshot-accumulation-disabled-by-default).

---

# Autonomous Scout

The two `shortlist-scout` units run `shortlist-scout` once daily after the US
equity close (22:30 UTC / 18:30 ET) and deliver a ranked Telegram report.

> **These units are NOT auto-installed.** Copy them manually after reviewing the
> paths for your install location.

## Install steps

```bash
# 1. Adjust WorkingDirectory and ExecStart in shortlist-scout.service to match
#    your install location (default assumes /opt/oracle/shortlist; see below).

# 2. Copy units to systemd
sudo cp deploy/shortlist-scout.service /etc/systemd/system/
sudo cp deploy/shortlist-scout.timer   /etc/systemd/system/

# 3. Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable --now shortlist-scout.timer

# 4. Verify the timer is scheduled
systemctl list-timers shortlist-scout.timer
```

To test a one-shot run without waiting for the timer:

```bash
sudo systemctl start shortlist-scout.service
journalctl -u shortlist-scout.service -f
```

## Paths

The units ship with VPS defaults:

| Setting | Value |
|---------|-------|
| `WorkingDirectory` | `/opt/oracle/shortlist` |
| `ExecStart` | `/opt/oracle/shortlist/.venv/bin/shortlist-scout` |
| `User` | `oracle` |

**Adjust these to your actual install location** before copying. The scout runs
from inside the repo so that `.env` is found by `env.py:load_env()`.

## Required environment variables

Set these in the repo-root `.env` (gitignored) or export them in the shell:

| Variable | Purpose | Required |
|----------|---------|----------|
| `FINNHUB_API_KEY` | Fundamentals + news boost | Yes (free tier OK) |
| `FMP_API_KEY` | Deep-screen fundamentals | Yes (free tier OK; ~19 tickers/day) |
| `TELEGRAM_BOT_TOKEN` | Deliver the daily report | Yes |
| `TELEGRAM_CHAT_ID` | Target chat/channel ID | Yes |
| `SEC_IDENTITY` | SEC EDGAR fair-access header (e.g. `you@email.com`) | Recommended |

A missing key degrades gracefully: the affected signal or data source is skipped
and the coverage gap is surfaced in the report rather than silently dropped.

## Research phase (`claude` CLI)

The scout optionally enriches the top-N names with a Claude-written 10-K brief.
This requires:

1. The `claude` CLI on PATH and authenticated (`claude --version` works).
2. The `[edgar]` extra installed: `uv sync --extra edgar`.

If either is absent the research phase is skipped and the report notes it.

## Kill-switch

Two ways to disable auto-research without redeploying:

```bash
# Option 1: file-based (persists across restarts)
touch scout/STOP_RESEARCH

# Option 2: environment variable (one run)
SCOUT_NO_RESEARCH=1 shortlist-scout
```

To disable the scout entirely, stop the timer:

```bash
sudo systemctl stop shortlist-scout.timer
sudo systemctl disable shortlist-scout.timer
```

## Failure alerts

A configured-but-failed Telegram delivery makes the unit exit non-zero, so an
`OnFailure` hook surfaces it. The oracle-daily-report pattern uses an alert service:

```ini
# In shortlist-scout.service [Service] section:
OnFailure=oracle-alert-failure@%n.service
```

Add this if you have `oracle-alert-failure@.service` deployed on your VPS
(it sends a Telegram message on any failed unit). See
`/etc/systemd/system/oracle-alert-failure@.service` for the template.

---

# Snapshot accumulation (DISABLED by default)

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
