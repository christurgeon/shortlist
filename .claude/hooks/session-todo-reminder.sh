#!/usr/bin/env bash
# Stop hook: nudge Claude to record unfinished follow-ups in TODO.md before ending.
#
# Loop-safe — fires at most once per turn (bails when stop_hook_active is set, i.e.
# we are already in the continuation this hook triggered). Silent on read-only /
# Q&A turns — only nudges when the git working tree has uncommitted changes, the
# proxy for "this turn actually did work worth capturing." See CLAUDE.md
# "Session wrap-up — capture follow-ups".
set -u

input=$(cat)

# Already nudged once this turn -> let the stop proceed.
case "$input" in
  *'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" 2>/dev/null || exit 0

# No git, or a clean tree -> nothing changed this session, nothing to capture.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
  exit 0
fi

cat <<'JSON'
{"decision":"block","reason":"This session changed files. Before ending, record any unfinished follow-up work, deferred decisions, or known gaps in TODO.md (newest at top, a dated '## <title> (YYYY-MM-DD)' heading, a short body, and a closing Status: line) per CLAUDE.md 'Session wrap-up'. If everything is already captured or there is nothing to note, stop now without further action."}
JSON
