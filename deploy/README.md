# Deploy units

This directory holds **sample** systemd units for the optional background jobs.
**None are auto-installed or enabled** by copying — use the turnkey installer below, or
copy them manually after reviewing paths.

- **Interactive bot** (`shortlist-bot.service`) — always-on Telegram bot; operator
  triggers `/screen`, `/deep`, and `/portfolio` on demand. See [Interactive Bot](#interactive-bot).
- **Snapshot accumulation** (`shortlist-accumulate.{service,timer}`) — builds the
  daily-snapshot history the backtest replay needs. See [Snapshot accumulation](#snapshot-accumulation-disabled-by-default).

---

# Install and update

## Turnkey installer

`install_opt_shortlist.sh` automates the whole install: it syncs the repo to
`/opt/shortlist`, builds the venv (`--extra bot --extra edgar`), installs the
units, and restarts the bot. It runs as a **normal login user**
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
> excludes for `/research/` and `/state/` are **anchored** with a leading `/`
> on purpose — unanchored, they would also match the like-named source packages
> `src/shortlist/research` and ship a broken wheel.

**If a step fails**, the installer aborts with a `DEPLOY FAILED ... during: <stage>`
banner on stderr and exits with the failing command's status. Stopping there is
deliberate — it never restarts the bot onto code that fails the offline smoke test, so a
running bot keeps serving its old in-memory code. But the tree in `/opt/shortlist` has
already been replaced by then, and `shortlist-bot.service` carries `Restart=on-failure`:
the next crash or reboot starts the bot on that untested tree. Fix the cause and re-run,
or roll the checkout back (`cd /opt/shortlist && sudo git checkout -- .`) before walking
away. The banner prints both commands.

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

# 3. The bot is restarted automatically by the installer. To check it:
systemctl status shortlist-bot.service
cd /opt/shortlist && ./.venv/bin/shortlist --demo   # offline smoke test, no Telegram
```

What needs what:

| Change | What picks it up |
|--------|------------------|
| Python source (`src/shortlist/**`) | rsync only — editable install means it's live immediately; a venv rebuild isn't required, but re-running the installer is harmless |
| New/updated dependency (`pyproject.toml` / `uv.lock`) | needs `uv sync` → **re-run the installer** |
| `config.yaml` thresholds/weights | rsync (re-run installer, or `rsync` just that file) |
| systemd unit edits | re-run installer (it rewrites + `daemon-reload`s the units) |
| API keys / `claude` CLI availability | restart the bot (`systemctl restart shortlist-bot.service`) |

Caveats:

- The installer's `rsync` has **no `--delete`**, so a **renamed or removed** source file
  leaves a stale copy in `/opt/shortlist/src`. After a rename/delete, clear it first:
  `rm -rf /opt/shortlist/src && sudo bash deploy/install_opt_shortlist.sh`.
- **Running the installer FROM `/opt/shortlist` is a silent no-op.** `SRC` derives from the
  script's own path, so `SRC == DEST` and it rsyncs onto itself — while still reporting
  success. Either `cd /opt/shortlist && sudo git pull && sudo bash
  deploy/install_opt_shortlist.sh`, or run it from a **separate** up-to-date checkout.
  Always verify with `git -C /opt/shortlist log --oneline -1`.

```bash
# Watch the bot:
journalctl -u shortlist-bot.service -f
```

## Install steps (manual)

```bash
# 1. Adjust WorkingDirectory and ExecStart in shortlist-bot.service to match
#    your install location (default assumes /opt/shortlist; see below).

# 2. Copy units to systemd
# NOTE: this MANUAL route uses the static unit files. `install_opt_shortlist.sh`
# does NOT -- it generates its own units inline. The two can drift; a Service
# setting added to one must be added to the other. (Bitten 2026-07-30.)
sudo cp deploy/shortlist-bot.service /etc/systemd/system/

# 3. Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable --now shortlist-bot.service

# 4. Verify it is running
systemctl status shortlist-bot.service
journalctl -u shortlist-bot.service -f
```

## Paths

The units ship with VPS defaults:

| Setting | Value |
|---------|-------|
| `WorkingDirectory` | `/opt/shortlist` |
| `ExecStart` | `/opt/shortlist/.venv/bin/shortlist-bot` |
| `User` | `oracle` |

**Adjust these to your actual install location** before copying. Everything runs
from inside the repo so that `.env` is found by `env.py:load_env()`.

## Required environment variables

Set these in the repo-root `.env` (gitignored) or export them in the shell:

| Variable | Purpose | Required |
|----------|---------|----------|
| `FINNHUB_API_KEY` | Fundamentals + news boost | Yes (free tier OK) |
| `FMP_API_KEY` | Deep-screen fundamentals | Yes (free tier OK; ~19 tickers/day) |
| `TELEGRAM_BOT_TOKEN` | Bot auth (`/screen`, `/deep`, `/portfolio`) | Yes |
| `TELEGRAM_CHAT_ID` | Target chat/channel ID | Yes |
| `SEC_IDENTITY` | SEC EDGAR fair-access header (e.g. `you@email.com`) | Recommended |

A missing key degrades gracefully: the affected signal or data source is skipped
and the coverage gap is surfaced in the report rather than silently dropped.

## Research phase (`claude` CLI)

`/deep` enriches a name with a Claude-written 10-K brief. This requires:

1. The `claude` CLI on PATH and authenticated (`claude --version` works).
2. The `[edgar]` extra installed: `uv sync --extra edgar`.

If either is absent the research phase is skipped and the reply notes it.

## Kill-switch

Two ways to disable auto-research without redeploying:

```bash
# Option 1: file-based (persists across restarts)
touch research/STOP_RESEARCH

# Option 2: environment variable (one process)
SHORTLIST_NO_RESEARCH=1 shortlist-bot
```



## Failure alerts

A configured-but-failed Telegram delivery makes the unit exit non-zero, so an
`OnFailure` hook surfaces it. The oracle-daily-report pattern uses an alert service:

```ini
# In a [Service] section:
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
| `/screen NVDA, LMT, MSFT` | Fast scores + gates report; replies with a PNG dashboard + HTML deep-dive |
| `/deep TSLA` | Same as `/screen` but also runs the Claude 10-K research brief (slower) |
| `/explain 13d` | Plain-English definition of a term, gate or flag used in these reports |
| `/add NVDA 12` | Track a holding in the bot-owned `positions.json` store (shares optional; bulk `/add NVDA, MSFT, LMT`) |
| `/thesis NVDA <why you own it>` | Record why you hold a tracked name (the only command taking free-text prose) |
| `/hold NVDA <note>` | After an alert, log that you looked and chose to keep the position (`decisions.jsonl`) |
| `/remove NVDA <reason>` | Stop tracking a holding (non-destructive — embeds the full record first; alias `/sold`) |
| `/portfolio` | Re-screens your tracked holdings from `positions.json`; adds a Portfolio section with exposure, sector concentration, and deterioration alerts. Cap: `config.yaml: portfolio.max_holdings` |
| `/help` | Lists available commands |

**There is no position-monitor section and no daily digest.** Held-name 8-K alerting was
removed in `de3c9f8` along with `bot/monitor.py` and the `portfolio.monitor` config block —
it needed a nightly scheduler, whose only producer was the retired scout. The position store
and its commands above are unaffected. Historical design:
[`../docs/POSITION_MONITOR.md`](../docs/POSITION_MONITOR.md).

> **This unit is NOT auto-installed.** Copy it manually after reviewing the paths
> for your install location.

## Guardrails

- **Chat allowlist.** The bot only answers the `TELEGRAM_CHAT_ID` configured in
  `.env`. Requests from any other chat or user are silently ignored — there is no
  error reply.
- **Soft per-request caps.** `bot.max_screen` and `bot.max_deep` in
  `config.yaml` limit how many tickers a single command may screen. Commands that
  exceed the cap are rejected with a friendly message rather than burning quota.
- **No hard FMP quota guard.** The HTTP cache (`cache.py`) makes warm re-screens
  of the same basket free within TTL. Cold requests degrade honestly via the
  coverage layer rather than failing outright.

## Single instance only

Run exactly **one** `shortlist-bot` instance. Two concurrent `getUpdates` pollers on the
same token trigger a Telegram `409 Conflict` and neither receives reliably.

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
> The `/deep` command shells out to the `claude` CLI. systemd's
> minimal default `PATH` excludes a user's `~/.local/bin`, so under a bare
> `User=oracle` unit `shutil.which("claude")` returns `None` and `/deep` silently
> degrades to "research skipped" (while `/screen` keeps working). To enable `/deep`,
> run the unit as the **same login user the installer uses** (not the `oracle`
> service account) and add its `HOME`/`PATH` to the `[Service]` section:
> ```ini
> User=<login-user>
> Environment=HOME=/home/<login-user>
> Environment=PATH=/opt/shortlist/.venv/bin:/home/<login-user>/.local/bin:/usr/local/bin:/usr/bin:/bin
> ```
> This is what `install_opt_shortlist.sh` already sets up.
> `/screen` works under any user; only the Claude research brief needs this.

## Required environment variables

Set in the repo-root `.env` (gitignored) or exported in the
shell before the service starts:

| Variable | Purpose | Required |
|----------|---------|----------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | Yes |
| `TELEGRAM_CHAT_ID` | Allowlisted chat/channel ID | Yes |
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

## Breadth + free-tier caveat (read before scaling the watchlist)

The snapshot-replay backtest needs **≥ 30 names/date** to clear the trust floor
(`engine._TRUST_MIN_BREADTH`), so the bundled watchlist is **42** names and the timer
install (below) runs `--max-tickers 42`. The *library* `--max-tickers` default stays
**15** for ad-hoc runs, so a bare `shortlist-accumulate run` truncates to 15 and stays
*below* the floor — pass `--max-tickers 42` (the installer does) to accumulate breadth.

The harness makes ~13 FMP calls/ticker, and FMP's free tier is **250/day ≈ 19
tickers/day**, so a 42-name run 429s past ~19 names. That's fine: `coverage()` is
field-based, so the FMP-gated overflow still saves on keyless coverage (Yahoo / EDGAR /
Finnhub / FINRA ≥ 0.5) — only the FMP-only value legs (PEG, analyst upside) go thin, and
the snapshot backtest's target axes (momentum, SUE, fundamentals) are keyless anyway.
FMP's paid Starter tier (~$14–20/mo) lifts the gating; Finnhub (60/min) and Yahoo
(keyless) are comfortable either way.

## Enabling the daily timer (opt-in — only when you decide to)

**Easiest (system unit, staggered, real paths filled in):** the deploy script installs
and enables it for you when you pass the opt-in flag — at **21:30 UTC**. It is the only
scheduled unit on the box:

```bash
sudo SHORTLIST_ACCUMULATE=1 bash deploy/install_opt_shortlist.sh
# store defaults to /opt/shortlist/state/snapshots (override: SHORTLIST_ACCUMULATE_ROOT=...)
sudo systemctl start shortlist-accumulate.service              # optional: seed day 1 now
/opt/shortlist/.venv/bin/shortlist-accumulate status --root /opt/shortlist/state/snapshots
```

The backtest must later read the **same** `--root`. Memory footprint is ~48 MB (measured),
so it's well within the 1.9 GB box.

---

**Manual (user unit) alternative:**

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
