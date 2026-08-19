#!/usr/bin/env bash
# Telegram alert for a failed shortlist systemd unit. Invoked as
# shortlist-alert-failure@%n.service (OnFailure=); $1 is the failing unit name.
#
# Runs as the bot's own run-user (in systemd-journal) rather than reusing
# oracle-alert-failure@.service, which runs as `oracle` (no journal access, so
# its tail comes back empty) and would send through the wrong bot token.
#
# Always exits 0 — this runs from OnFailure=, so failing itself risks alert recursion.
set -uo pipefail

UNIT="${1:-unknown.service}"
ENV_FILE="${SHORTLIST_ENV_FILE:-/opt/shortlist/.env}"

# Sourced, not systemd EnvironmentFile=: .env uses `export KEY=value`, which
# EnvironmentFile= parses as a var literally named "export KEY" and drops silently.
if [[ -r "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE" || true
    set +a
fi

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"

if [[ -z "$TOKEN" || -z "$CHAT_ID" ]]; then
    echo "shortlist-alert-failure: missing telegram env vars; skipping alert for $UNIT" >&2
    exit 0
fi

# Journal output can carry provider request URLs (API key as query param) and
# the bot token itself — redact before it leaves the box (CLAUDE.md).
redact() {
    sed -E \
        -e 's/([?&](apikey|api_key|token|apiKey|key)=)[^&[:space:]"]+/\1REDACTED/g' \
        -e 's#(https://api\.telegram\.org/bot)[0-9]+:[A-Za-z0-9_-]+#\1REDACTED#g' \
        -e 's/([0-9]{8,10}:AA)[A-Za-z0-9_-]{30,}/\1REDACTED/g'
}

tail_journal() {
    journalctl -u "$UNIT" --no-pager -n 30 --no-hostname 2>/dev/null || true
}

RESULT="$(systemctl show -p Result --value "$UNIT" 2>/dev/null || echo unknown)"
EXITSTATUS="$(systemctl show -p ExecMainStatus --value "$UNIT" 2>/dev/null || echo '?')"

# Telegram caps messages at 4096 chars; clamp with headroom for URL-encoding.
{
    printf '⚠️ %s failed (result=%s, exit=%s)\n\n' "$UNIT" "$RESULT" "$EXITSTATUS"
    tail_journal
} | redact | head -c 3900 | curl -fsS --max-time 15 \
    --data-urlencode "chat_id=${CHAT_ID}" \
    --data-urlencode "text@-" \
    "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    >/dev/null 2>&1 \
    || echo "shortlist-alert-failure: telegram send failed for $UNIT" >&2

exit 0
