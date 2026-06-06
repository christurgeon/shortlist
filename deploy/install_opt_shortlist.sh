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

echo "==> 1/6  Create $DEST (owned by $RUN_USER)"
mkdir -p "$DEST"
chown "$RUN_USER:$RUN_GROUP" "$DEST"

echo "==> 2/6  Sync repo $SRC -> $DEST (excluding venv/caches/runtime artifacts)"
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

echo "==> 3/6  Build the venv in place (uv sync --extra scout --extra edgar) as $RUN_USER"
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && uv sync --extra scout --extra edgar"

echo "==> 4/6  Smoke-test the deployed entrypoint (offline --demo, no API/Telegram)"
sudo -u "$RUN_USER" -H bash -lc "cd '$DEST' && './.venv/bin/shortlist-scout' --demo >/dev/null && echo '    demo OK'"

echo "==> 5/6  Install systemd units"
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

echo "==> 6/6  Reload systemd, enable + start the timer"
systemctl daemon-reload
systemctl enable --now shortlist-scout.timer

echo
echo "===== DONE. Timer status: ====="
systemctl list-timers shortlist-scout.timer --no-pager || true
echo
echo "Next:"
echo "  - One real validation run now:   sudo systemctl start shortlist-scout.service"
echo "  - Watch it:                      journalctl -u shortlist-scout.service -f"
