#!/usr/bin/env bash
#
# Deploy shortlist to /opt/shortlist: sync the repo, build the venv, install the
# systemd units, and restart the interactive Telegram bot.
#
# The bot runs as a NORMAL login user (not a service account) so the claude-CLI
# research layer behind /deep keeps its auth in that user's ~/.claude.
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

echo "==> 1/6  Create $DEST (owned by $RUN_USER)"
mkdir -p "$DEST"
chown "$RUN_USER:$RUN_GROUP" "$DEST"

echo "==> 2/6  Sync repo $SRC -> $DEST (excluding venv/caches/runtime artifacts)"
# NOTE: runtime-output excludes are ANCHORED with a leading '/' so they match only the
# repo-root dirs, NOT the like-named source package src/shortlist/research.
# (__pycache__ stays unanchored — strip it at every level.)
rsync -a \
  --exclude='/.venv/' \
  --exclude='__pycache__/' \
  --exclude='/.pytest_cache/' \
  --exclude='/.cache/' \
  --exclude='/.xbrl_cache/' \
  --exclude='/.ruff_cache/' \
  --exclude='/research/' \
  --exclude='/state/' \
  --exclude='/backtest_*.json' \
  --exclude='/backtest_*.err' \
  "$SRC"/ "$DEST"/
chown -R "$RUN_USER:$RUN_GROUP" "$DEST"
# .env carries secrets -> lock it down
if [[ -f "$DEST/.env" ]]; then chmod 600 "$DEST/.env"; fi

echo "==> 3/6  Build the venv in place (uv sync --extra bot --extra edgar) as $RUN_USER"
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && uv sync --extra bot --extra edgar"

echo "==> 4/6  Smoke-test the deployed entrypoint (offline --demo, no API/Telegram)"
# `shortlist --demo` runs the scorer against offline fixtures and writes NOTHING. Keep it
# that way: a smoke test that writes to state/ pollutes live data on every deploy.
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && './.venv/bin/shortlist' --demo >/dev/null && echo '    demo OK'"

echo "==> 5/6  Install systemd units"
# The failure-alert template. Generated inline like every other unit here (the installer
# does NOT read deploy/*.service — see CLAUDE.md) because it needs $DEST/$RUN_USER baked in.
# Its only remaining consumer is the opt-in accumulate timer below, which carries
# OnFailure=shortlist-alert-failure@%n.service — install it unconditionally so enabling
# that timer later can never reference a missing template.
cat > "$UNIT_DIR/shortlist-alert-failure@.service" <<UNIT
[Unit]
Description=Send Telegram alert for failed shortlist unit %i
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$DEST
Environment=HOME=$RUN_HOME
# %i is the failing unit name WITH its .service suffix (OnFailure passes %n).
ExecStart=$DEST/deploy/shortlist-alert-failure.sh %i
StandardOutput=journal
StandardError=journal
SyslogIdentifier=shortlist-alert-failure
UNIT
chmod +x "$DEST/deploy/shortlist-alert-failure.sh"

systemctl daemon-reload


# --- OPTIONAL: daily snapshot accumulation (OFF by default) -------------------
# Enable with SHORTLIST_ACCUMULATE=1. Builds the >=24-day point-in-time history the
# snapshot-replay backtest needs (unblocks SUE / Lazy-Prices validation). Runs at 21:30 UTC.
# Memory is a non-issue (~48 MB).
# Override the store dir with SHORTLIST_ACCUMULATE_ROOT (default: $DEST/state/snapshots,
# which the rsync preserves across deploys). The backtest must read the SAME --root.
if [[ "${SHORTLIST_ACCUMULATE:-0}" == "1" ]]; then
  ACCUM_ROOT="${SHORTLIST_ACCUMULATE_ROOT:-$DEST/state/snapshots}"
  echo "==> 5b/6  (opt-in) Install + enable the daily accumulate timer (21:30 UTC) -> $ACCUM_ROOT"
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
# FMP-quota-gated names; needs the edgartools extra (present in the /opt venv) and
# SEC_IDENTITY via .env. The CLI *default* stays fmp,finnhub (a
# default that degrades without the optional extra is a footgun).
ExecStart=$DEST/.venv/bin/shortlist-accumulate run --root $ACCUM_ROOT --max-tickers 42 --sources fmp,finnhub,edgar
# Failure ping. This is now the ONLY generated unit that carries it — keep it here.
OnFailure=shortlist-alert-failure@%n.service
# 1800s is ample: the cold DERA index build measured 26.9s and a warm read 0.5s.
TimeoutStartSec=1800
UNIT
  cat > "$UNIT_DIR/shortlist-accumulate.timer" <<'UNIT'
[Unit]
Description=Daily shortlist snapshot capture

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

echo "==> 6/6  Restart the interactive bot so it picks up the synced code"
# shortlist-bot is a long-running Type=simple service: it loads its modules at startup and
# keeps running the OLD code after an rsync until restarted. `try-restart` updates it IFF
# it is already running — a no-op on hosts without the bot, and it never force-starts an
# unconfigured one (so a missing token / not-yet-installed bot can't fail the deploy).
systemctl try-restart shortlist-bot.service 2>/dev/null \
  && echo "    bot restarted" \
  || echo "    bot not running — skipped (start it with: systemctl enable --now shortlist-bot.service)"

echo
echo "===== DONE. Unit status: ====="
systemctl --no-pager status shortlist-bot.service --lines=0 2>/dev/null | head -3 || true
systemctl list-timers shortlist-accumulate.timer --no-pager || true
echo
echo "Next:"
echo "  - Watch the bot:  journalctl -u shortlist-bot.service -f"
echo "  - Talk to it:     /screen AAPL MSFT   |   /deep AAPL"
