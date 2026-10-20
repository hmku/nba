#!/usr/bin/env bash
# SessionStart hook: make sure the `fantasy` CLI is installed, then print a SHORT orientation.
# Idempotent and fast when already installed. Must never fail the session and never print secrets
# (FANTASY_DB_URL may contain a password, so only its scheme is shown).
set -u
cd "${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}" || exit 0

# Local laptop: use the repo venv and the git-ignored .env (FANTASY_DB_URL) when present. Never echoes values.
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && . .venv/bin/activate
if [ -z "${FANTASY_DB_URL:-}" ] && [ -f .env ]; then set -a; . ./.env >/dev/null 2>&1; set +a; fi
# macOS has no coreutils `timeout`; fall back to gtimeout or no timeout.
run_timeout() { local t=$1; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$t" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$t" "$@"
  else "$@"; fi; }

if ! command -v fantasy >/dev/null 2>&1; then
  echo "[session-start] fantasy CLI not found; installing (pip install -e \".[dev]\" -q)..."
  PY=$(command -v python3 || command -v python)
  if [ -z "$PY" ] || ! run_timeout 240 "$PY" -m pip install -e ".[dev]" -q >/tmp/fantasy_pip_install.log 2>&1; then
    echo "[session-start] pip install failed (see /tmp/fantasy_pip_install.log); install manually."
  fi
fi

echo "=== Fantasy basketball agent: orientation ==="
db="${FANTASY_DB_URL:-}"
if [ -n "$db" ]; then
  echo "DB: ${db%%://*}://<redacted>  (FANTASY_DB_URL set)"
else
  echo "DB: local SQLite var/fantasy.db (FANTASY_DB_URL unset; the dashboard will NOT see writes)"
fi
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  echo "Session: CLOUD (no Yahoo / no Claude in Chrome; Yahoo work needs a local 'claude --chrome')"
fi

if command -v fantasy >/dev/null 2>&1; then
  echo "--- fantasy status (first 25 lines) ---"
  if out=$(run_timeout 30 fantasy status 2>/dev/null); then
    # Redact anything that looks like a DB URL, just in case.
    printf '%s\n' "$out" | head -n 25 | sed -E 's#(postgres(ql)?(\+[a-z0-9]+)?://)[^ ]*#\1<redacted>#g'
  else
    echo "(fantasy status failed - DB unreachable or not initialised? Run 'fantasy status' manually;"
    echo " for a fresh local SQLite DB: 'fantasy db init && fantasy task seed')"
  fi
fi

echo "--- latest handoffs (docs/handoffs/) ---"
ls -1 docs/handoffs/*.md 2>/dev/null | sort -r | head -n 3
echo "Read docs/STATUS.md and the newest handoff before starting. Skills: .claude/skills/ (handoff, agent-run, draft-prep, live-draft, ...)."
exit 0
