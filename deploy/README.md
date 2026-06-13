# Deploy units

This directory holds **sample** systemd units for the optional background jobs.
**None are auto-installed or enabled** — copy them manually after reviewing paths.
Two independent jobs live here:

- **Autonomous scout** (`shortlist-scout.{service,timer}`) — daily candidate
  discovery + ranked Telegram report. See [Autonomous Scout](#autonomous-scout).
- **Interactive bot** (`shortlist-bot.service`) — always-on Telegram bot; operator
  triggers `/screen`, `/deep`, and `/portfolio` on demand. See [Interactive Bot](#interactive-bot).
- **Snapshot accumulation** (`shortlist-accumulate.{service,timer}`) — builds the
  daily-snapshot history the backtest replay needs. See [Snapshot accumulation](#snapshot-accumulation-disabled-by-default).

---

# Autonomous Scout

The two `shortlist-scout` units run `shortlist-scout` once daily after the US
equity close (22:30 UTC / 18:30 ET) and deliver a ranked Telegram report.

> **These units are NOT auto-installed.** Copy them manually after reviewing the
> paths for your install location.

## Turnkey installer

`install_opt_shortlist.sh` automates the whole install: it syncs the repo to
`/opt/shortlist`, builds the venv (`--extra scout --extra edgar`), installs both
units, and enables the daily timer. It runs the service as a **normal login user**
(not the `oracle` service account) so the `claude`-CLI research layer keeps its auth
in that user's `~/.claude`, and sets `HOME`/`PATH` accordingly. Idempotent — safe to
re-run.

```bash
# Runs as whoever invoked sudo (SUDO_USER) by default:
sudo bash deploy/install_opt_shortlist.sh

# ...or pick the run-user / paths explicitly:
sudo SHORTLIST_USER=deploy SHORTLIST_DEST=/opt/shortlist bash deploy/install_opt_shortlist.sh
```

> Source path, install dir, and run-user are configurable at the top of the script
> (env-overridable: `SHORTLIST_SRC` / `SHORTLIST_DEST` / `SHORTLIST_USER`). The rsync
> excludes for `/scout/`, `/research/`, `/state/` are **anchored** with a leading `/`
> on purpose — unanchored, they would also match the like-named source packages
> `src/shortlist/{scout,research}` and ship a broken wheel.

For a manual install on a different host, follow the steps below instead.

## Updating after a code change

The deploy is an **editable install** (`/opt/shortlist/.venv` points back at
`/opt/shortlist/src` via a `.pth` file), so refreshing the box is just "get the new
code into `/opt/shortlist`, then re-sync the venv." Re-running the installer does both
and is idempotent:

```bash
# 1. Land the new code in the dev tree (wherever you cloned the repo)
cd /path/to/shortlist
git checkout main && git pull          # (or merge your feature branch)

# 2. Re-deploy: rsync src -> /opt/shortlist, uv sync deps, reinstall units
sudo bash deploy/install_opt_shortlist.sh

# 3. It runs on the next timer fire (22:30 UTC). To exercise it now:
cd /opt/shortlist && ./.venv/bin/shortlist-scout --demo   # offline, no Telegram
```

What needs what:

| Change | What picks it up |
|--------|------------------|
| Python source (`src/shortlist/**`) | rsync only — editable install means it's live immediately; a venv rebuild isn't required, but re-running the installer is harmless |
| New/updated dependency (`pyproject.toml` / `uv.lock`) | needs `uv sync` → **re-run the installer** |
| `config.yaml` thresholds/weights | rsync (re-run installer, or `rsync` just that file) |
| systemd unit edits | re-run installer (it rewrites + `daemon-reload`s the units) |
| API keys / `claude` CLI availability | **no redeploy** — auto-detected on the next run |

Caveats:

- The installer's `rsync` has **no `--delete`**, so a **renamed or removed** source file
  leaves a stale copy in `/opt/shortlist/src`. After a rename/delete, clear it first:
  `rm -rf /opt/shortlist/src && sudo bash deploy/install_opt_shortlist.sh`.
- The run is **idempotent per session date** — a manual `systemctl start` (or a second
  run) for a session that already completed prints "already completed; nothing to do"
  and skips. To force a re-run for testing, drop that date from
  `/opt/shortlist/state/scout_state.json` (`runs` array) first.

```bash
# Trigger a real (Telegram-delivering) run on demand and watch it:
sudo systemctl start shortlist-scout.service
journalctl -u shortlist-scout.service -f
```

## Install steps (manual)

```bash
# 1. Adjust WorkingDirectory and ExecStart in shortlist-scout.service to match
#    your install location (default assumes /opt/shortlist; see below).

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
| `WorkingDirectory` | `/opt/shortlist` |
| `ExecStart` | `/opt/shortlist/.venv/bin/shortlist-scout` |
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

# Interactive Bot

`shortlist-bot` is an **always-on** Telegram bot (`Type=simple`, `Restart=on-failure`)
that long-polls Telegram's `getUpdates` API. No inbound ports or webhook is needed —
the process reaches out; nothing needs to reach in.

The operator drives screening by chatting:

| Command | What it does |
|---------|--------------|
| `/screen NVDA, LMT, MSFT` | Fast scores + gates report; same PNG dashboard + HTML deep-dive the daily push sends |
| `/deep TSLA` | Same as `/screen` but also runs the Claude 10-K research brief (slower) |
| `/portfolio` | Re-screens your holdings from a gitignored `portfolio.csv` (`ticker,shares`, in the bot's working dir); adds a Portfolio section with exposure, sector concentration, and deterioration alerts. Cap: `config.yaml: portfolio.max_holdings` |
| `/help` | Lists available commands |

> **This unit is NOT auto-installed.** Copy it manually after reviewing the paths
> for your install location.

## Guardrails

- **Chat allowlist.** The bot only answers the `TELEGRAM_CHAT_ID` configured in
  `.env`. Requests from any other chat or user are silently ignored — there is no
  error reply.
- **Soft per-request caps.** `scout.bot.max_screen` and `scout.bot.max_deep` in
  `config.yaml` limit how many tickers a single command may screen. Commands that
  exceed the cap are rejected with a friendly message rather than burning quota.
- **No hard FMP quota guard.** The HTTP cache (`cache.py`) makes warm re-screens
  of the same basket free within TTL. Cold requests degrade honestly via the
  coverage layer rather than failing outright.

## Coexistence with the daily push

`shortlist-bot` shares `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` with the
autonomous scout. Long-polling (`getUpdates`) and the daily push (`sendMessage`)
can coexist on one token — the only conflict is running **two concurrent
`getUpdates` pollers**, which triggers a Telegram `409 Conflict`. Run exactly
**one** bot instance.

The autonomous daily push is **feature-flagged OFF by default**
(`scout.daily_push.enabled: false` in `config.yaml`). The interactive bot is
the primary driver. The `shortlist-scout.timer` should be left disabled; set
`scout.daily_push.enabled: true` and re-enable the timer to re-arm the daily
report later if desired.

## Install steps (manual)

```bash
sudo cp deploy/shortlist-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shortlist-bot.service
journalctl -u shortlist-bot.service -f
```

## Paths

The unit ships with VPS defaults:

| Setting | Value |
|---------|-------|
| `WorkingDirectory` | `/opt/shortlist` |
| `ExecStart` | `/opt/shortlist/.venv/bin/shortlist-bot` |
| `User` | `oracle` |

**Adjust these to your actual install location** before copying. The bot runs
from inside the repo so that `.env` is found by `env.py:load_env()`.

> **`/deep` needs the `claude` CLI on PATH and its auth in `~/.claude`.** Like the
> scout's research phase, the `/deep` command shells out to the `claude` CLI. systemd's
> minimal default `PATH` excludes a user's `~/.local/bin`, so under a bare
> `User=oracle` unit `shutil.which("claude")` returns `None` and `/deep` silently
> degrades to "research skipped" (while `/screen` keeps working). To enable `/deep`,
> run the unit as the **same login user the scout installer uses** (not the `oracle`
> service account) and add its `HOME`/`PATH` to the `[Service]` section:
> ```ini
> User=<login-user>
> Environment=HOME=/home/<login-user>
> Environment=PATH=/opt/shortlist/.venv/bin:/home/<login-user>/.local/bin:/usr/local/bin:/usr/bin:/bin
> ```
> This mirrors what `install_opt_shortlist.sh` already does for `shortlist-scout`.
> `/screen` works under any user; only the Claude research brief needs this.

## Required environment variables

Same as the scout — set in the repo-root `.env` (gitignored) or exported in the
shell before the service starts:

| Variable | Purpose | Required |
|----------|---------|----------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (shared with scout) | Yes |
| `TELEGRAM_CHAT_ID` | Allowlisted chat/channel ID (shared with scout) | Yes |
| `FMP_API_KEY` | Deep-screen fundamentals | Yes (free tier OK) |
| `FINNHUB_API_KEY` | Fundamentals fallback | Yes (free tier OK) |
| `SEC_IDENTITY` | SEC EDGAR fair-access header (e.g. `you@email.com`) | Recommended |

## Single-instance caveat and graceful shutdown

**Run only one instance.** A second `getUpdates` poller on the same token
immediately triggers a Telegram `409 Conflict` error and both pollers become
unreliable. The unit comment enforces this by design — do not run the bot as both
a systemd service and a foreground process simultaneously.

**Graceful shutdown takes up to ~40 s.** `SIGTERM` is delivered immediately, but
the in-flight long-poll socket read is not interrupted — the signal only takes
effect when the current `getUpdates` call returns (poll timeout ≈ 25 s), after
which the worker thread has 5 s to join. `TimeoutStopSec=50` is set deliberately
above that budget; if it were left at the systemd default (90 s is fine, but some
distributions default to 30 s), systemd would `SIGKILL` the process mid-poll on
every restart.

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
