#!/usr/bin/env bash
# Deploys shortlist to /opt/shortlist: syncs the repo, builds the venv, installs
# systemd units, and restarts the bot. Idempotent — safe to re-run.
#
# Runs as a normal login user (SUDO_USER by default), not the oracle service
# account, so the claude-CLI research layer behind /deep keeps its auth in
# that user's ~/.claude. Override via env, e.g.:
#   sudo SHORTLIST_USER=deploy bash deploy/install_opt_shortlist.sh
set -euo pipefail

# --- settings (all overridable via env) ---------------------------------------
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

# SRC derives from this script's own path, so running it from $DEST (the
# documented `cd /opt/shortlist && git pull && install...` recipe) makes
# SRC == DEST — valid, and handled below by skipping rsync rather than
# copying the tree onto itself.
SRC_REAL="$(readlink -f "$SRC")"
DEST_REAL="$(readlink -f "$DEST" 2>/dev/null || echo "$DEST")"
INPLACE=0
if [[ "$SRC_REAL" == "$DEST_REAL" ]]; then
  INPLACE=1
fi

RUN_GROUP="$(id -gn "$RUN_USER")"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z $RUN_HOME ]]; then
  echo "ERROR: could not resolve home directory for '$RUN_USER'" >&2
  exit 1
fi

echo "==> deploying $SRC -> $DEST, service runs as $RUN_USER ($RUN_HOME)"

# --- failure reporting --------------------------------------------------------
# Stopping mid-deploy (set -e) is correct — the bot keeps serving old code until
# restarted — but $DEST may already be modified, and Restart=on-failure means a
# later crash/reboot could pick up the untested tree. Say so loudly on exit.
STAGE="startup"
DEST_MUTATED=0

_on_exit() {
  local rc=$?
  trap - EXIT
  [[ $rc -eq 0 ]] && exit 0
  {
    echo
    echo "===== DEPLOY FAILED (rc=$rc) during: $STAGE ====="
    if [[ $DEST_MUTATED -eq 1 ]]; then
      echo "  $DEST WAS ALREADY MODIFIED (tree and/or venv). The systemd units were NOT"
      echo "  reinstalled and the bot was NOT restarted, so a running bot still serves the"
      echo "  OLD code -- but shortlist-bot.service has Restart=on-failure, so any later"
      echo "  restart or reboot will start it on the tree now on disk."
      echo "  Fix the cause and re-run this script, or roll the checkout back:"
      echo "    cd $DEST && sudo git status && sudo git log --oneline -1"
      echo "    cd $DEST && sudo git checkout -- .      # discards local edits under $DEST"
      echo "    sudo bash $DEST/deploy/install_opt_shortlist.sh"
    else
      echo "  $DEST was not modified."
    fi
    echo "================================================"
  } >&2
  exit "$rc"
}
trap _on_exit EXIT

STAGE="1/6 create $DEST"
echo "==> 1/6  Create $DEST (owned by $RUN_USER)"
DEST_MUTATED=1
mkdir -p "$DEST"
chown "$RUN_USER:$RUN_GROUP" "$DEST"

STAGE="2/6 sync $SRC -> $DEST"
echo "==> 2/6  Sync repo $SRC -> $DEST (excluding venv/caches/runtime artifacts)"
if [[ $INPLACE -eq 1 ]]; then
  echo "    IN-PLACE RUN: source IS the destination ($DEST_REAL) -- skipping rsync." >&2
  echo "    the tree must already be at the intended commit (run 'git pull' first)." >&2
else
  # Excludes are anchored with a leading '/' to match repo-root dirs only —
  # unanchored they'd also match src/shortlist/research and ship a broken wheel.
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
fi
chown -R "$RUN_USER:$RUN_GROUP" "$DEST"
if [[ -f "$DEST/.env" ]]; then chmod 600 "$DEST/.env"; fi

STAGE="3/6 build the venv (uv sync)"
echo "==> 3/6  Build the venv in place (uv sync --extra bot --extra edgar) as $RUN_USER"
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && uv sync --extra bot --extra edgar"

STAGE="4/6 smoke test (shortlist --demo)"
echo "==> 4/6  Smoke-test the deployed entrypoint (offline --demo, no API/Telegram)"
# --demo writes nothing — keep it that way, or every deploy pollutes state/.
if ! _smoke_out="$(sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && './.venv/bin/shortlist' --demo" 2>&1)"; then
  echo "    SMOKE TEST FAILED — the deployed tree does not run:" >&2
  printf '%s\n' "$_smoke_out" | tail -n 30 >&2
  exit 1
fi
echo "    demo OK"

STAGE="5/6 install systemd units"
echo "==> 5/6  Install systemd units"
# Generated inline (not read from deploy/*.service — see CLAUDE.md), installed
# unconditionally so the opt-in accumulate timer never references a missing template.
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
ExecStart=$DEST/deploy/shortlist-alert-failure.sh %i
StandardOutput=journal
StandardError=journal
SyslogIdentifier=shortlist-alert-failure
UNIT
chmod +x "$DEST/deploy/shortlist-alert-failure.sh"

systemctl daemon-reload


# --- OPTIONAL: daily snapshot accumulation (OFF by default) -------------------
# Enable with SHORTLIST_ACCUMULATE=1. See README.md for the breadth/free-tier caveat.
if [[ "${SHORTLIST_ACCUMULATE:-0}" == "1" ]]; then
  ACCUM_ROOT="${SHORTLIST_ACCUMULATE_ROOT:-$DEST/state/snapshots}"
  STAGE="5b/6 install the accumulate timer"
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
ExecStart=$DEST/.venv/bin/shortlist-accumulate run --root $ACCUM_ROOT --max-tickers 42 --sources fmp,finnhub,edgar
OnFailure=shortlist-alert-failure@%n.service
# 1800s is ample: cold DERA index build measured 26.9s, warm read 0.5s.
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

STAGE="6/6 restart the bot"
echo "==> 6/6  Restart the interactive bot so it picks up the synced code"
# try-restart no-ops if the bot isn't running yet; never force-starts an unconfigured one.
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
