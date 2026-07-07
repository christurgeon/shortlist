#!/usr/bin/env bash
#
# Deploy the shortlist autonomous scout to /opt/shortlist and schedule it to run
# once daily (22:30 UTC, after the US equity close) via a system-level systemd
# timer. The service runs as a NORMAL login user (not a service account) so the
# claude-CLI research layer keeps its auth in that user's ~/.claude.
#
# By default the run-user is whoever invoked sudo (SUDO_USER); override any of the
# settings below via the environment, e.g.:
#
#   sudo SHORTLIST_USER=deploy bash deploy/install_opt_shortlist.sh
#
# Idempotent: safe to re-run (rsync refresh + venv sync + unit reinstall).
#
set -euo pipefail

# --- settings (all overridable via env) ---------------------------------------
# Source defaults to the repo this script lives in; dest + user are configurable.
SRC="${SHORTLIST_SRC:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"
DEST="${SHORTLIST_DEST:-/opt/shortlist}"
RUN_USER="${SHORTLIST_USER:-${SUDO_USER:-}}"
UNIT_DIR="${SHORTLIST_UNIT_DIR:-/etc/systemd/system}"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: must run as root (use: sudo bash $0)" >&2
  exit 1
fi
if [[ -z $RUN_USER || $RUN_USER == root ]]; then
  echo "ERROR: set the run-user (a normal login account, not root):" >&2
  echo "       sudo SHORTLIST_USER=<user> bash $0" >&2
  exit 1
fi
if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "ERROR: user '$RUN_USER' does not exist" >&2
  exit 1
fi
if [[ ! -d $SRC ]]; then
  echo "ERROR: source $SRC not found" >&2
  exit 1
fi

RUN_GROUP="$(id -gn "$RUN_USER")"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z $RUN_HOME ]]; then
  echo "ERROR: could not resolve home directory for '$RUN_USER'" >&2
  exit 1
fi

echo "==> deploying $SRC -> $DEST, service runs as $RUN_USER ($RUN_HOME)"

echo "==> 1/7  Create $DEST (owned by $RUN_USER)"
mkdir -p "$DEST"
chown "$RUN_USER:$RUN_GROUP" "$DEST"

echo "==> 2/7  Sync repo $SRC -> $DEST (excluding venv/caches/runtime artifacts)"
# NOTE: runtime-output excludes are ANCHORED with a leading '/' so they match only
# the repo-root dirs, NOT the like-named source packages src/shortlist/{scout,research}.
# (__pycache__ stays unanchored — strip it at every level.)
rsync -a \
  --exclude='/.venv/' \
  --exclude='__pycache__/' \
  --exclude='/.pytest_cache/' \
  --exclude='/.cache/' \
  --exclude='/.xbrl_cache/' \
  --exclude='/.ruff_cache/' \
  --exclude='/research/' \
  --exclude='/scout/' \
  --exclude='/state/' \
  --exclude='/backtest_*.json' \
  --exclude='/backtest_*.err' \
  "$SRC"/ "$DEST"/
chown -R "$RUN_USER:$RUN_GROUP" "$DEST"
# .env carries secrets -> lock it down
if [[ -f "$DEST/.env" ]]; then chmod 600 "$DEST/.env"; fi

echo "==> 3/7  Build the venv in place (uv sync --extra scout --extra edgar) as $RUN_USER"
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && uv sync --extra scout --extra edgar"

echo "==> 4/7  Smoke-test the deployed entrypoint (offline --demo, no API/Telegram)"
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && './.venv/bin/shortlist-scout' --demo >/dev/null && echo '    demo OK'"

echo "==> 5/7  Install systemd units"
cat > "$UNIT_DIR/shortlist-scout.service" <<UNIT
[Unit]
Description=shortlist autonomous scout — daily candidate discovery + report
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$DEST
# The claude CLI lives in the run-user's ~/.local/bin; uv + the venv on /usr/local/bin.
# systemd's default PATH has neither, so the research layer's shutil.which("claude")
# would fail. HOME must also point at the run-user so the CLI finds its auth.
Environment=HOME=$RUN_HOME
Environment=PATH=$DEST/.venv/bin:$RUN_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$DEST/.venv/bin/shortlist-scout
# Do NOT add Restart=/auto-retry: the run is marked complete only after delivery, so an
# auto-restart before that re-runs discovery and re-hits the unofficial Yahoo endpoint
# (see CLAUDE.md "Yahoo WAF gotcha"). Type=oneshot already means no restart — keep it.
# Optional failure ping (uncomment if oracle-alert-failure@.service is deployed):
# OnFailure=oracle-alert-failure@%n.service
TimeoutStartSec=1800
# No [Install] section: this oneshot is driven solely by shortlist-scout.timer.
UNIT

cat > "$UNIT_DIR/shortlist-scout.timer" <<'UNIT'
[Unit]
Description=Run the shortlist scout once daily after the US close

[Timer]
# 22:30 UTC ~= 18:30 ET, after the close. Persistent reruns a missed timer.
OnCalendar=*-*-* 22:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

echo "==> 6/7  Reload systemd, enable + start the timer"
systemctl daemon-reload
systemctl enable --now shortlist-scout.timer

# --- OPTIONAL: daily snapshot accumulation (OFF by default) -------------------
# Enable with SHORTLIST_ACCUMULATE=1. Builds the >=24-day point-in-time history the
# snapshot-replay backtest needs (unblocks SUE / Lazy-Prices validation). Staggered to
# 21:30 UTC — one hour BEFORE the scout (22:30) so the two harness runs never overlap:
# the EDGAR concurrency semaphore is PER-PROCESS, so concurrent runs would double SEC load
# and compete for FMP's 250/day cap + the Yahoo endpoint. Memory is a non-issue (~48 MB).
# Override the store dir with SHORTLIST_ACCUMULATE_ROOT (default: $DEST/state/snapshots,
# which the rsync preserves across deploys). The backtest must read the SAME --root.
if [[ "${SHORTLIST_ACCUMULATE:-0}" == "1" ]]; then
  ACCUM_ROOT="${SHORTLIST_ACCUMULATE_ROOT:-$DEST/state/snapshots}"
  echo "==> 6b/7  (opt-in) Install + enable the daily accumulate timer (21:30 UTC) -> $ACCUM_ROOT"
  cat > "$UNIT_DIR/shortlist-accumulate.service" <<UNIT
[Unit]
Description=shortlist daily point-in-time snapshot capture
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$DEST
Environment=HOME=$RUN_HOME
Environment=PATH=$DEST/.venv/bin:$RUN_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Nice=10
# --max-tickers 42 = whole watchlist, so breadth can clear the backtest's 30-name
# trust floor (FMP 429s past ~19 names; overflow saves as THIN keyless-only snapshots).
# --sources adds edgar: keyless + VPS-reachable, supplies statements/insider/SIC for
# FMP-quota-gated names; needs the edgartools extra (present in the /opt venv -- the
# scout uses it) and SEC_IDENTITY via .env. The CLI *default* stays fmp,finnhub (a
# default that degrades without the optional extra is a footgun).
ExecStart=$DEST/.venv/bin/shortlist-accumulate run --root $ACCUM_ROOT --max-tickers 42 --sources fmp,finnhub,edgar
TimeoutStartSec=1800
UNIT
  cat > "$UNIT_DIR/shortlist-accumulate.timer" <<'UNIT'
[Unit]
Description=Daily shortlist snapshot capture (staggered 1h before the scout)

[Timer]
OnCalendar=*-*-* 21:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now shortlist-accumulate.timer
  echo "    accumulate timer enabled (21:30 UTC); seed day 1 now with:"
  echo "      sudo systemctl start shortlist-accumulate.service"
fi

echo "==> 7/7  Restart the interactive bot so it picks up the synced code"
# shortlist-bot is a long-running Type=simple service: it loads its modules at startup and
# keeps running the OLD code after an rsync until restarted. `try-restart` updates it IFF
# it is already running — a no-op on hosts without the bot, and it never force-starts an
# unconfigured one (so a missing token / not-yet-installed bot can't fail the deploy).
# NOTE the asymmetry: the scout above is a oneshot driven by its timer and is deliberately
# NOT restarted here — bouncing an in-flight scout run re-hits the unofficial Yahoo endpoint
# (see the scout-unit comment). Only the stateful long-running bot needs the bounce.
systemctl try-restart shortlist-bot.service 2>/dev/null \
  && echo "    bot restarted" \
  || echo "    bot not running — skipped (start it with: systemctl enable --now shortlist-bot.service)"

echo
echo "===== DONE. Timer status: ====="
systemctl list-timers shortlist-scout.timer --no-pager || true
echo
echo "Next:"
echo "  - One real validation run now:   sudo systemctl start shortlist-scout.service"
echo "  - Watch it:                      journalctl -u shortlist-scout.service -f"
