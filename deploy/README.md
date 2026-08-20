# Deploy units

Systemd units for shortlist's two background jobs, plus the installer that wires them up.

- **`shortlist-bot.service`** — the always-on Telegram bot. Static and real; the only
  unit the installer reads from disk (see [Manual install](#manual-install)).
- **`shortlist-accumulate.{service,timer}`** — optional daily snapshot capture for the
  backtest. Samples only, **disabled by default**; the installer generates its own units
  when enabled (see [Snapshot accumulation](#snapshot-accumulation-disabled-by-default)).

---

## Turnkey install

```bash
sudo bash deploy/install_opt_shortlist.sh
```

Syncs the repo to `/opt/shortlist`, builds the venv (`--extra bot --extra edgar`), installs
units, and restarts the bot. Idempotent. Runs as whoever invoked `sudo` (`SUDO_USER`) by
default — override with `SHORTLIST_USER`/`SHORTLIST_DEST`/`SHORTLIST_SRC`:

```bash
sudo SHORTLIST_USER=deploy SHORTLIST_DEST=/opt/shortlist bash deploy/install_opt_shortlist.sh
```

Runs as a **normal login user**, not the `oracle` service account, so the `claude` CLI
behind `/deep` keeps its auth in that user's `~/.claude`.

**If a step fails**, the installer prints `DEPLOY FAILED ... during: <stage>` on stderr and
exits nonzero. It aborts before the bot restart on purpose — a running bot keeps serving
its old code — but `/opt/shortlist` may already be modified, and `shortlist-bot.service`'s
`Restart=on-failure` means a later crash or reboot would pick up the untested tree. Fix the
cause and re-run, or roll back: `cd /opt/shortlist && sudo git checkout -- .`

## Updating after a code change

```bash
cd /path/to/shortlist && git checkout main && git pull
sudo bash deploy/install_opt_shortlist.sh
```

The deploy is an editable install (`/opt/shortlist/.venv` points back at
`/opt/shortlist/src`), so an update is just "sync the tree, re-sync deps." Re-running the
installer does both and is idempotent.

| Change | Needs |
|--------|-------|
| Python source | rsync only — editable install is live immediately |
| `pyproject.toml` / `uv.lock` | re-run installer (`uv sync`) |
| `config.yaml` | rsync (re-run installer) |
| systemd unit edits | re-run installer (rewrites + `daemon-reload`s) |
| API keys / `claude` CLI availability | `systemctl restart shortlist-bot.service` |

Caveats:

- **Running the installer from inside `/opt/shortlist`** makes `SRC == DEST`. It detects
  this and skips the rsync rather than copying the tree onto itself — correct only if you
  `git pull` first. Verify with `git -C /opt/shortlist log --oneline -1`.
- rsync has **no `--delete`**, so a renamed/removed source file leaves a stale copy. On the
  supported `git pull` path this cannot happen — git removes deleted files itself. Only a
  deploy from a *separate* checkout needs clearing, and only from that checkout:
  `sudo rm -rf /opt/shortlist/src && sudo bash /path/to/checkout/deploy/install_opt_shortlist.sh`.
  **Never run that from inside `/opt/shortlist`** — `SRC == DEST` skips the rsync (above), so
  the `rm -rf` deletes the only copy of `src/` and nothing restores it.
- Its excludes for `/research/` and `/state/` are **anchored** with a leading `/`; unanchored
  they'd also match `src/shortlist/research` and ship a broken wheel.

## Manual install

For a host other than the VPS defaults, edit `WorkingDirectory`/`ExecStart`/`User` in
`shortlist-bot.service` (defaults: `/opt/shortlist`, `/opt/shortlist/.venv/bin/shortlist-bot`,
`oracle`) to match, then:

```bash
sudo cp deploy/shortlist-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shortlist-bot.service
journalctl -u shortlist-bot.service -f
```

This is the only unit the turnkey installer reads from disk — every other unit it
generates inline, so a `[Service]` change made here must also go there, or they drift.

To enable `/deep`'s research brief under a manual unit, run it as a normal login user (not
`oracle`) with that user's `HOME`/`PATH`, matching what the installer sets up:

```ini
User=<login-user>
Environment=HOME=/home/<login-user>
Environment=PATH=/opt/shortlist/.venv/bin:/home/<login-user>/.local/bin:/usr/local/bin:/usr/bin:/bin
```

Without it, `shutil.which("claude")` returns `None` under systemd's minimal default `PATH`
and `/deep` silently degrades to "research skipped" (`/screen` still works either way).

## Required environment variables

Set in the repo-root `.env` (gitignored):

| Variable | Purpose | Required |
|----------|---------|----------|
| `TELEGRAM_BOT_TOKEN` | Bot auth | Yes |
| `TELEGRAM_CHAT_ID` | Allowlisted chat/channel ID | Yes |
| `FMP_API_KEY` | Deep-screen fundamentals | Yes (free tier OK, ~19 tickers/day) |
| `FINNHUB_API_KEY` | Fundamentals fallback + news | Yes (free tier OK) |
| `SEC_IDENTITY` | SEC EDGAR fair-access header (e.g. `you@email.com`) | Recommended |

A missing key degrades gracefully — the affected source is skipped and the gap shows up
in the report's coverage section.

## Research phase (`claude` CLI)

`/deep` needs the `claude` CLI on `PATH`, authenticated (`claude --version` works), and the
`[edgar]` extra (`uv sync --extra edgar`). Missing either skips the research phase; the
reply says so.

**Kill-switch**, without redeploying:

```bash
touch research/STOP_RESEARCH        # persists across restarts
SHORTLIST_NO_RESEARCH=1 shortlist-bot   # one process only
```

## Failure alerts

`shortlist-alert-failure.sh` sends a redacted Telegram alert when a unit fails. The
installer installs `shortlist-alert-failure@.service` unconditionally; wire any unit to it
with:

```ini
OnFailure=shortlist-alert-failure@%n.service
```

Only `shortlist-accumulate.service` carries this today.

---

## Interactive bot

`shortlist-bot` is an always-on Telegram bot (`Type=simple`, `Restart=on-failure`,
long-polls `getUpdates` — no inbound port needed).

| Command | What it does |
|---------|--------------|
| `/screen NVDA, LMT, MSFT` | Fast scores + gates; PNG dashboard + HTML deep-dive |
| `/deep TSLA` | Same, plus the Claude 10-K research brief (slower) |
| `/explain 13d` | Plain-English definition of a term, gate, or flag |
| `/add NVDA 12` | Track a holding (`positions.json`; shares optional; bulk `/add NVDA, MSFT`) |
| `/thesis NVDA <why>` | Record why you hold a tracked name |
| `/hold NVDA <note>` | Log that you saw an alert and chose to keep the position |
| `/remove NVDA <reason>` | Stop tracking (non-destructive; alias `/sold`) |
| `/portfolio` | Re-screen tracked holdings; adds exposure/concentration/deterioration. Cap: `config.yaml: portfolio.max_holdings` |
| `/help` | Lists commands |

No position-monitor digest or held-name 8-K alerting — removed with the scout (`de3c9f8`);
it needed a nightly scheduler that no longer exists. History:
[`../docs/POSITION_MONITOR.md`](../docs/POSITION_MONITOR.md).

**Guardrails:**

- **Chat allowlist** — only `TELEGRAM_CHAT_ID` gets replies; other chats are silently ignored.
- **Soft per-request caps** — `bot.max_screen`/`bot.max_deep` in `config.yaml`; over-cap
  requests get a friendly rejection, not silent quota burn.
- **No hard FMP quota guard** — the HTTP cache makes warm re-screens free within TTL; cold
  requests degrade honestly via the coverage layer.

**Run exactly one instance** — two concurrent `getUpdates` pollers on the same token 409.

**Graceful shutdown takes ~40s**: `SIGTERM` only takes effect once the in-flight long-poll
returns (poll timeout ~25s) plus a 5s worker join. The unit's `TimeoutStopSec=50` covers
this — don't lower it, or systemd `SIGKILL`s mid-poll on every restart.

---

## Snapshot accumulation (disabled by default)

`shortlist-accumulate.{service,timer}` here are **samples, not installed or enabled**.
Nothing captures snapshots until you run `shortlist-accumulate run` manually or opt in below.

**Why**: the backtest's snapshot-replay and weight-fitting paths stay dormant until the
store has ≥24 organically-captured daily snapshots. Check progress:

```bash
uv run shortlist-accumulate status --root <STORE_ROOT>
```

**Run once, manually:**

```bash
uv run shortlist-accumulate run --root <STORE_ROOT>          # default watchlist + sources
uv run shortlist-accumulate run --tickers AAPL,MSFT --sources finnhub,yahoo --root <STORE_ROOT>
```

Idempotent — re-running the same day skips already-captured tickers.

**Breadth**: the replay backtest needs ≥30 names/date to clear its trust floor, so the
timer install runs `--max-tickers 42` (the full bundled watchlist); the library default of
15 stays under that floor for ad-hoc runs. FMP's free tier (250 calls/day, ~13/ticker) 429s
past ~19 names — the overflow still saves on keyless coverage (Yahoo/EDGAR/Finnhub/FINRA),
so only the FMP-only value legs (PEG, analyst upside) go thin; momentum/SUE/fundamentals
are keyless anyway. Paid FMP Starter (~$14–20/mo) lifts the gating.

**Enable the timer** (system unit, 21:30 UTC, real paths filled in):

```bash
sudo SHORTLIST_ACCUMULATE=1 bash deploy/install_opt_shortlist.sh
# store defaults to /opt/shortlist/state/snapshots (override: SHORTLIST_ACCUMULATE_ROOT=...)
sudo systemctl start shortlist-accumulate.service   # optional: seed day 1 now
```

~48 MB memory footprint — fine on a 1.9 GB box.

**Manual (user unit) alternative**, no root:

```bash
# edit <REPO_ROOT>/<STORE_ROOT> in shortlist-accumulate.service first
mkdir -p ~/.config/systemd/user
cp shortlist-accumulate.service shortlist-accumulate.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now shortlist-accumulate.timer
```

`loginctl enable-linger $USER` keeps it firing while logged out.
