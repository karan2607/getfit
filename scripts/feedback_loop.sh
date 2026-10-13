#!/bin/bash
#
# Feedback loop runner.
#
# Polls the superuser-only feedback queue and hands each new item to a fresh
# Claude Code session, which implements it, runs the build gate, and pushes.
# This script owns the queue mechanics; Claude owns the code changes.
#
# Setup (once):
#   export GETFIT_API=https://<your-backend>.onrender.com
#   printf '%s' '<your-40-char-app-token>' > ~/.getfit-loop-token
#   chmod 600 ~/.getfit-loop-token
#
# Run:
#   bash scripts/feedback_loop.sh          # one pass over the queue
#   bash scripts/feedback_loop.sh --watch  # keep polling
#
# Install under launchd for durability — see docs/FEEDBACK_LOOP.md. A Claude
# in-session cron job dies with the session; this survives restarts.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="${GETFIT_TOKEN_FILE:-$HOME/.getfit-loop-token}"
POLL_SECONDS="${GETFIT_POLL_SECONDS:-300}"
LOG="${GETFIT_LOOP_LOG:-$HOME/getfit-loop.log}"
BRANCH="${GETFIT_BRANCH:-main}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
API="${GETFIT_API:-}"
if [[ -z "$API" ]]; then
  log "ERROR: set GETFIT_API to your backend base URL (no trailing slash)."
  exit 1
fi
API="${API%/}"

if [[ ! -r "$TOKEN_FILE" ]]; then
  log "ERROR: no token at $TOKEN_FILE. See the header of this script."
  exit 1
fi
TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
if [[ -z "$TOKEN" ]]; then
  log "ERROR: $TOKEN_FILE is empty."
  exit 1
fi

for bin in curl jq claude git; do
  command -v "$bin" >/dev/null 2>&1 || { log "ERROR: $bin is not installed."; exit 1; }
done

api() {
  # api <method> <path> [json-body]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sS -X "$method" "$API$path" \
      -H "Authorization: Token $TOKEN" \
      -H 'Content-Type: application/json' \
      -d "$body"
  else
    curl -sS -X "$method" "$API$path" -H "Authorization: Token $TOKEN"
  fi
}

set_status() {
  # set_status <id> <status> [notes] [sha]
  local id="$1" st="$2" notes="${3:-}" sha="${4:-}"
  local body
  body="$(jq -nc --arg s "$st" --arg n "$notes" --arg c "$sha" \
    '{status:$s} + (if $n == "" then {} else {result_notes:$n} end)
               + (if $c == "" then {} else {commit_sha:$c} end)')"
  api PATCH "/api/feedback/$id/" "$body" >/dev/null
}

# ---------------------------------------------------------------------------
# The build gate.
#
# There is no staging environment, so this is the only thing between the loop
# and a broken production deploy. Must pass before anything is pushed.
# `npm run build` rather than a bare tsc: the project enables noUnusedLocals,
# so the build catches errors the plain typecheck does not.
# ---------------------------------------------------------------------------
build_gate() {
  local out
  if ! out="$(cd "$REPO/backend" && venv/bin/python manage.py check 2>&1)"; then
    GATE_ERROR="django check failed:
$out"; return 1
  fi
  if ! out="$(cd "$REPO/backend" && venv/bin/python manage.py makemigrations --check --dry-run 2>&1)"; then
    GATE_ERROR="models drifted from migrations (makemigrations --check failed):
$out"; return 1
  fi
  if ! out="$(cd "$REPO/backend" && venv/bin/python manage.py test api.tests 2>&1 | tail -30)"; then
    GATE_ERROR="backend tests failed:
$out"; return 1
  fi
  if ! out="$(cd "$REPO/frontend" && npm run build 2>&1 | tail -30)"; then
    GATE_ERROR="frontend build failed:
$out"; return 1
  fi
  GATE_ERROR=""
  return 0
}

# ---------------------------------------------------------------------------
# Process one item
# ---------------------------------------------------------------------------
process_item() {
  local id="$1" body="$2" page="$3"

  log "---- item $id ----"
  log "  body: $body"
  log "  page: $page"
  set_status "$id" in_progress

  # Start from a clean, current tree so one item's failure can't contaminate
  # the next, and so we never push a stale merge.
  if ! git -C "$REPO" diff --quiet || ! git -C "$REPO" diff --cached --quiet; then
    log "  SKIP: working tree is dirty — refusing to touch it."
    set_status "$id" skipped "Working tree was dirty; loop refused to run."
    return
  fi
  git -C "$REPO" checkout "$BRANCH" >>"$LOG" 2>&1
  git -C "$REPO" pull --ff-only >>"$LOG" 2>&1 || log "  WARN: pull failed, continuing on local $BRANCH"

  local before
  before="$(git -C "$REPO" rev-parse HEAD)"

  local prompt
  prompt="$(cat <<EOF
You are running unattended in the GetFit repo as an automated feedback loop.
Implement this single user-submitted request and nothing else.

REQUEST: $body
SUBMITTED FROM PAGE: $page

Rules:
- Make the smallest correct change that addresses the request.
- Add or update a test that would have caught the reported problem.
- Match the surrounding code style. Read before you edit.
- Do NOT write a data migration that mutates existing rows. Schema additions
  are fine. If the request can only be done with a destructive data migration,
  make no change and say exactly: ESCALATE: needs a reviewed data migration
- Do NOT change authentication, permissions, or anything that widens access.
  If the request requires that, make no change and say exactly:
  ESCALATE: security-sensitive, needs review
- Do NOT commit or push. The runner handles git.
- When done, print a one-line summary of what you changed.
EOF
)"

  local claude_out
  claude_out="$(cd "$REPO" && claude -p "$prompt" 2>&1)"
  log "  claude: $(printf '%s' "$claude_out" | tail -5)"

  if printf '%s' "$claude_out" | grep -q '^ESCALATE:'; then
    local reason
    reason="$(printf '%s' "$claude_out" | grep -m1 '^ESCALATE:')"
    log "  ESCALATED: $reason"
    git -C "$REPO" checkout -- . >>"$LOG" 2>&1
    set_status "$id" skipped "$reason"
    return
  fi

  if git -C "$REPO" diff --quiet && git -C "$REPO" diff --cached --quiet; then
    log "  no changes produced"
    set_status "$id" failed "Agent produced no changes. Output: $(printf '%s' "$claude_out" | tail -3)"
    return
  fi

  log "  running build gate"
  if ! build_gate; then
    log "  GATE FAILED: $GATE_ERROR"
    git -C "$REPO" checkout -- . >>"$LOG" 2>&1
    git -C "$REPO" clean -fd >>"$LOG" 2>&1
    set_status "$id" failed "Build gate failed, nothing pushed.
$GATE_ERROR"
    return
  fi

  git -C "$REPO" add -A >>"$LOG" 2>&1
  git -C "$REPO" commit -q -m "Feedback: $body

Submitted from $page
Implemented automatically by the feedback loop; build gate passed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" >>"$LOG" 2>&1

  local sha
  sha="$(git -C "$REPO" rev-parse HEAD)"
  if [[ "$sha" == "$before" ]]; then
    set_status "$id" failed "Commit did not land."
    return
  fi

  if ! git -C "$REPO" push origin "$BRANCH" >>"$LOG" 2>&1; then
    log "  PUSH FAILED — leaving the commit local"
    set_status "$id" failed "Committed $sha locally but push failed. Resolve by hand."
    return
  fi

  log "  pushed $sha"
  set_status "$id" done "$(printf '%s' "$claude_out" | tail -1)" "$sha"
}

# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------
run_pass() {
  local items
  items="$(api GET '/api/feedback/?status=new' || true)"

  if ! printf '%s' "$items" | jq -e 'type == "array"' >/dev/null 2>&1; then
    log "queue fetch failed: $(printf '%s' "$items" | head -c 200)"
    return
  fi

  local count
  count="$(printf '%s' "$items" | jq 'length')"
  if [[ "$count" == "0" ]]; then
    log "queue empty"
    return
  fi

  log "$count new item(s)"
  # Oldest first, so the queue drains in submission order.
  local n=0
  while IFS= read -r row; do
    n=$((n + 1))
    process_item \
      "$(printf '%s' "$row" | jq -r '.id')" \
      "$(printf '%s' "$row" | jq -r '.body')" \
      "$(printf '%s' "$row" | jq -r '.page // ""')"
  done < <(printf '%s' "$items" | jq -c 'reverse | .[]')
  log "pass complete ($n processed)"
}

if [[ "${1:-}" == "--watch" ]]; then
  log "watching $API every ${POLL_SECONDS}s (ctrl-c to stop)"
  while true; do
    run_pass
    sleep "$POLL_SECONDS"
  done
else
  run_pass
fi
