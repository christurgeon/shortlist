#!/usr/bin/env bash
# Stop hook: nudge Claude to record unfinished follow-ups in TODO.md before ending.
# Loop-safe (bails on stop_hook_active); silent on read-only turns and on TODO-only
# edits. See CLAUDE.md "Session wrap-up — capture follow-ups".
set -u

# Past this, the nudge leads with pruning instead of capture.
SOFT_MAX_LINES=600

input=$(cat)

case "$input" in
  *'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

changed=$({ git diff --name-only; git diff --cached --name-only; } 2>/dev/null | sort -u)
[ -n "$changed" ] || exit 0

# Curating the follow-up list is not follow-up work: nudging here only ever produces
# an entry about the entry. This fired for two months and is why TODO.md hit 2,133 lines.
[ "$changed" = "TODO.md" ] && exit 0

reason="This session changed files. Before ending, update TODO.md per CLAUDE.md 'Session wrap-up'."
reason="$reason Two obligations, not one: (a) record unfinished follow-up work, deferred decisions and known gaps;"
reason="$reason (b) DELETE or fold down any entry whose work has now shipped or been resolved — the durable record is CLAUDE.md, docs/audits/ and git, so a resolved entry left in TODO.md is rot, and removing one is a completed obligation rather than a liberty."
reason="$reason Prefer updating an existing entry in place over adding a new dated one for the same thread of work."
reason="$reason Never record work done ON TODO.md itself, nor a narrative of this session — that is meta-churn, not follow-up work."

lines=$(wc -l < TODO.md 2>/dev/null | tr -d ' ') || lines=0
case "$lines" in
  ''|*[!0-9]*) lines=0 ;;
esac
if [ "$lines" -gt "$SOFT_MAX_LINES" ]; then
  reason="$reason TODO.md is currently $lines lines, past the ${SOFT_MAX_LINES}-line point where a session stops reading it: prune resolved entries FIRST, and do not add without retiring."
fi

reason="$reason If there is nothing to add and nothing to retire, stop now without further action."

# Message is quote- and backslash-free by construction, so no escaping pass is needed.
printf '{"decision":"block","reason":"%s"}\n' "$reason"
