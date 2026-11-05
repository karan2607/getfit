#!/bin/bash
#
# Feedback loop runner.
#
# Polls the superuser-only feedback queue and hands each new item to a fresh
# Claude Code session, which implements it, runs the build gate, and pushes.
# This script owns the queue mechanics; Claude owns the code changes.
#
# Setup (once):
#   Use an INTEGRATION KEY, not the app's session token. Create one in
#   Profile -> Password -> Integration keys with scope "feedback", then:
#     printf '%s' '<43-char integration key>' > ~/.getfit-loop-token
#   chmod 600 ~/.getfit-loop-token
#
#   OPTIONAL but recommended: a second key with scope "readonly", at
#     ~/.getfit-readonly-token
#   The agent uses it to VERIFY claims a submission makes about stored data.
#   Without it, a report like "bench history has 22-rep sets" has to be taken
#   on trust -- which cost a real item: it was escalated to protect sets the
#   user had already corrected. The key cannot write anything (safe methods
#   only, own rows only), which is what makes it safe to leave on disk for an
#   unattended agent.
#
# The token must belong to a SUPERUSER — the queue endpoints return 403
# otherwise. Override the backend URL with GETFIT_API if it changes.
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
# Pushes to STAGING, not main. Staging auto-deploys and is promoted to
# production by hand, so the loop can no longer put an unreviewed change in
# front of users on its own -- it can only put one in front of you.
#
# Override with GETFIT_BRANCH=main to restore the old behaviour, but note the
# build gate is a local test run: it cannot catch a UI regression or a
# production-only break (the Django 5 outage was green locally on 4.2).
BRANCH="${GETFIT_BRANCH:-staging}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
# Public backend URL. Override with GETFIT_API if it ever changes.
API="${GETFIT_API:-https://getfit-backend-p00m.onrender.com}"
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

# Optional read-only key. Absent is fine and the loop behaves as before, just
# without the ability to check its assumptions.
READONLY_TOKEN_FILE="${GETFIT_READONLY_TOKEN_FILE:-$HOME/.getfit-readonly-token}"
READONLY_TOKEN=""
if [[ -r "$READONLY_TOKEN_FILE" ]]; then
  READONLY_TOKEN="$(tr -d '[:space:]' < "$READONLY_TOKEN_FILE")"
fi

for bin in curl jq claude git; do
  command -v "$bin" >/dev/null 2>&1 || { log "ERROR: $bin is not installed."; exit 1; }
done

api() {
  # api <method> <path> [json-body]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sS -X "$method" "$API$path" \
      -H "Authorization: Bearer $TOKEN" \
      -H 'Content-Type: application/json' \
      -d "$body"
  else
    curl -sS -X "$method" "$API$path" -H "Authorization: Bearer $TOKEN"
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
# The reply shown to the person who filed the item.
#
# `tail -1` used to be enough, when the summary was one line and nothing read
# it but a log. It is now the agent's turn in a conversation, so it is the
# whole final paragraph -- everything after the last blank line, capped so a
# run that forgets to summarise cannot paste its entire transcript into the
# notification panel.
# ---------------------------------------------------------------------------
final_paragraph() {
  printf '%s' "$1" | awk '
    /^[[:space:]]*$/ { buf = ""; next }
    { buf = buf (buf == "" ? "" : "\n") $0 }
    END { print buf }
  ' | head -c 1200
}

# ---------------------------------------------------------------------------
# Process one item
# ---------------------------------------------------------------------------
process_item() {
  local id="$1" body="$2" page="$3" messages="${4:-[]}" attempts="${5:-0}"

  log "---- item $id ----"
  log "  body: $body"
  log "  page: $page"
  if (( attempts > 0 )); then
    log "  retry: attempt $((attempts + 1)) — a previous run claimed this and never reported back"
  fi
  set_status "$id" in_progress

  # Start from a clean, current tree so one item's failure can't contaminate
  # the next, and so we never push a stale merge.
  if ! git -C "$REPO" diff --quiet || ! git -C "$REPO" diff --cached --quiet; then
    log "  SKIP: working tree is dirty — refusing to touch it."
    set_status "$id" skipped "Working tree was dirty; loop refused to run."
    return
  fi
  # Fail loudly on a missing branch rather than silently working on whatever
  # happens to be checked out. Before this the checkout error went to the log
  # and the loop carried on, so a typo in GETFIT_BRANCH meant committing to
  # the wrong branch -- and with the default now `staging`, that wrong branch
  # would have been main.
  if ! git -C "$REPO" checkout "$BRANCH" >>"$LOG" 2>&1; then
    log "  SKIP: branch '$BRANCH' does not exist locally."
    log "    Create it once with:  git checkout -b $BRANCH && git push -u origin $BRANCH"
    set_status "$id" skipped "Branch '$BRANCH' does not exist; nothing was changed."
    return
  fi
  git -C "$REPO" pull --ff-only >>"$LOG" 2>&1 || log "  WARN: pull failed, continuing on local $BRANCH"

  local before
  before="$(git -C "$REPO" rev-parse HEAD)"

  local verify_block=""
  if [[ -n "$READONLY_TOKEN" ]]; then
    verify_block="$(cat <<EOF

VERIFY BEFORE YOU ACT:
A submission describes the problem as the user saw it, possibly days ago, and
possibly after they already worked around it. Do NOT treat its claims about
stored data as facts. You have a READ-ONLY API key -- it cannot write anything
-- so check first:

  curl -sS -H 'Authorization: Bearer $READONLY_TOKEN' '$API/api/workouts/plans/'

Readable: /api/workouts/, /api/sessions/, /api/health/, /api/diet/,
/api/profile/. GET only; anything else returns 403 by design.

This matters. One item was escalated to protect logged sets that the user had
already fixed before submitting, because the text said they were broken and
there was no way to look. If a claim does not hold, say so in your summary and
proceed on what you actually found.
EOF
)"
  fi

  # A reply reopens the item rather than filing a new one, so by the time it
  # comes back round there may be a conversation attached. Reading only
  # `body` would re-implement the original request and lose the follow-up --
  # which is the whole reason the thread exists.
  local thread_block=""
  local turns
  turns="$(printf '%s' "$messages" | jq -r '
    if type == "array" and length > 0 then
      .[] | (if .role == "agent" then "YOU" else "USER" end) + ": " + .body
    else empty end' 2>/dev/null)"
  if [[ -n "$turns" ]]; then
    thread_block="$(cat <<EOF

THIS IS A CONTINUING CONVERSATION. The request above is where it started.
What follows is every turn since, oldest first:

$turns

Address the LAST user message. The earlier turns are context: work you have
already done is already committed, so do not redo it. If the last message
says your previous fix did not work, treat your own earlier summary as a
claim to re-check rather than a fact.
EOF
)"
  fi

  local prompt
  prompt="$(cat <<EOF
You are running unattended in the GetFit repo as an automated feedback loop.
Implement this single user-submitted request and nothing else.

REQUEST: $body
SUBMITTED FROM PAGE: $page
$thread_block

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
- When done, print a summary as the LAST thing you output. It is shown to the
  person who filed this, verbatim, as your reply in the conversation -- so
  write it to them, not to a log. Two or three short sentences: what was
  actually wrong, and what you changed. No file paths, no test counts, no
  "the user reported that". If you could not fix it, say what stopped you.
$verify_block
EOF
)"

  local claude_out
  # `< /dev/null` is load-bearing.
  #
  # process_item is called from a `while read` loop fed by the queue
  # listing. Without this, `claude` inherits that stdin and consumes the
  # remaining rows, so a pass that found 3 items processed exactly 1 and
  # logged "pass complete (1 processed)" as though that were all of them.
  claude_out="$(cd "$REPO" && claude -p "$prompt" 2>&1 < /dev/null)"
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
  set_status "$id" done "$(final_paragraph "$claude_out")" "$sha"
}

# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------
run_pass() {
  local items
  # `claimable`, not `new`: the server decides what may be worked on, which
  # includes an 'in_progress' item whose claim has gone stale because the run
  # holding it died. Polling for 'new' alone orphaned those -- one sat
  # invisible in 'in_progress' for 14 days, past both this loop and the
  # notification bell.
  items="$(api GET '/api/feedback/?status=claimable' || true)"

  if ! printf '%s' "$items" | jq -e 'type == "array"' >/dev/null 2>&1; then
    # Call out an auth failure explicitly.
    #
    # This loop used to authenticate with the app's SESSION token, which
    # logout() deletes -- and the web app auto-logs-out after 30 minutes idle.
    # So leaving a browser tab open silently 401'd every pass from then on. It
    # cost several days of the loop doing nothing, twice. It now uses a
    # feedback-scoped integration key, which no session action can revoke.
    if printf '%s' "$items" | grep -qi 'invalid token\|not authenticated\|credentials'; then
      log "AUTH FAILED: the key in $TOKEN_FILE was rejected."
      log "  Create a replacement in the app: Profile -> Password ->"
      log "  Integration keys, scope \"feedback\". Then:"
      log "    printf '%s' '<new key>' > $TOKEN_FILE && chmod 600 $TOKEN_FILE"
      log "  Do NOT paste a login token here: those are session credentials"
      log "  that logout() deletes, which is what kept killing this loop."
      return 1
    fi
    log "queue fetch failed: $(printf '%s' "$items" | head -c 200)"
    return 1
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
      "$(printf '%s' "$row" | jq -r '.page // ""')" \
      "$(printf '%s' "$row" | jq -c '.messages // []')" \
      "$(printf '%s' "$row" | jq -r '.attempts // 0')"
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
