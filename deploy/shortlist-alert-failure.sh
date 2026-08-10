#!/usr/bin/env bash
# shortlist-alert-failure.sh — Telegram alert for a failed shortlist systemd unit.
#
# Invoked by shortlist-alert-failure@.service (template). $1 is the failing unit
# name WITH the .service suffix, passed through OnFailure=shortlist-alert-failure@%n.service.
#
# Why this exists rather than reusing oracle-alert-failure@.service: that unit runs as
# `oracle`, whose only group is `oracle` — NOT systemd-journal — so its `journalctl -u
# shortlist-scout.service` tail comes back empty and the alert carries no context. It also
# sends through the oracle bot's token. This one runs as the scout's own run-user (in
# systemd-journal) and speaks through the shortlist bot the operator already talks to.
#
# Plain text only (no parse_mode) — sidesteps Telegram MarkdownV2/HTML escaping pitfalls
# when journal output contains backticks, underscores, or angle brackets.
#
# Always exits 0 — a failed alert script must never itself fail in a way that could
# cascade (it is called by an OnFailure= hook, so a nonzero exit risks alert recursion).
set -uo pipefail

UNIT="${1:-unknown.service}"
ENV_FILE="${SHORTLIST_ENV_FILE:-/opt/shortlist/.env}"

# Source, do NOT use systemd's EnvironmentFile=: this repo's .env uses `export KEY=value`
# lines, which systemd parses as a variable literally named "export KEY" and skips — the
# token would silently come back empty and every alert would no-op. bash handles it.
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

# The journal tail can embed provider request URLs (FMP/Finnhub take the key as a query
# param) and the bot token itself. This is the bash-side counterpart of
# env.py:redact_secrets() — CLAUDE.md requires anything that may carry a request URL to be
# redacted before it leaves the box, and Telegram delivery is very much "leaving the box".
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

# Telegram sendMessage caps text at 4096 chars and 400-rejects oversize payloads.
# Byte-clamp leaves ~200B headroom for URL-encoding expansion of non-ASCII chars.
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
