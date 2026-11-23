#!/usr/bin/env bash
#
# One command the feedback agent can actually run.
#
# WHY THIS EXISTS
# ---------------
# The agent runs unattended behind an allowlist of thirteen literal command
# prefixes ("cd backend && venv/bin/python manage.py test:*" and friends).
# Nothing told it what those prefixes were, so it composed the same commands
# differently -- an absolute path, a different cd, a bare `python` -- and the
# session denied every one of them. It said so itself:
#
#   "I couldn't run the new test: this unattended session denies shell
#    commands that need approval."
#
# So it wrote tests and shipped them unrun, for every item. The runner's own
# build gate still caught breakage before any push, which is why this was a
# slow leak rather than an outage -- but an agent that cannot execute its own
# work is reduced to guessing whether it did the job.
#
# Guessing prefixes is the wrong fix. One entry point means one allowlist
# line, nothing to compose, and nothing to match.
#
#   bash scripts/verify.sh backend        # django check + the api tests
#   bash scripts/verify.sh frontend       # vitest + tsc/build + palette
#   bash scripts/verify.sh all            # both
#   bash scripts/verify.sh backend api.tests.test_chat_stream   # one module
#
# Deliberately NOT here: git, curl, migrate, anything that writes outside the
# working tree. The runner owns git and the agent has no network; see the
# reasoning above CLAUDE_ALLOWED_TOOLS in feedback_loop.sh.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHAT="${1:-all}"
TARGET="${2:-}"

FAILED=0

run() {
  # run <label> <dir> <command...>
  local label="$1" dir="$2"; shift 2
  printf '\n=== %s ===\n' "$label"
  if (cd "$dir" && "$@"); then
    printf '%s\n' "--- $label: OK"
  else
    printf '%s\n' "--- $label: FAILED"
    FAILED=1
  fi
}

backend() {
  local venv="$ROOT/backend/venv/bin/python"
  if [[ ! -x "$venv" ]]; then
    echo "no virtualenv at backend/venv -- cannot run the backend tests"
    FAILED=1
    return
  fi
  run 'django check' "$ROOT/backend" "$venv" manage.py check
  run 'migrations are written' "$ROOT/backend" \
    "$venv" manage.py makemigrations --check --dry-run
  run 'backend tests' "$ROOT/backend" \
    "$venv" manage.py test "${TARGET:-api.tests}"
}

frontend() {
  run 'frontend tests' "$ROOT/frontend" npm run test
  run 'types and build' "$ROOT/frontend" npm run build
  run 'palette guard' "$ROOT/frontend" npm run check:palette
}

case "$WHAT" in
  backend)  backend ;;
  frontend) frontend ;;
  all)      backend; frontend ;;
  *)
    echo "usage: bash scripts/verify.sh [backend|frontend|all] [test target]"
    exit 2
    ;;
esac

printf '\n'
if (( FAILED )); then
  echo 'VERIFY: FAILED'
  exit 1
fi
echo 'VERIFY: OK'
