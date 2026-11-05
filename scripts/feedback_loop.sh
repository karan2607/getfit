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
#   The "readonly" key at ~/.getfit-readonly-token is NO LONGER USED by this
#   script. It was pasted into the agent's prompt so it could check a
#   submission's claims about stored data; the agent has no network access
#   now, so it bought nothing. Leave the file; see CLAUDE_ALLOWED_TOOLS below
#   for what the run may do, and the note there for how the data check comes
#   back without handing the agent a credential.
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
# One run at a time.
#
# launchd fires this every 900s, and a pass that actually produces changes
# takes longer than that: the agent run, then the build gate, whose backend
# suite alone is about nine minutes. One item is ~12 minutes and two is ~25,
# so overlapping runs were arithmetically certain the moment this script
# could edit files.
#
# Two runs in one checkout is not a slow queue, it is a corrupt one. Run B
# sees run A's half-finished edits, and either refuses -- marking A's item
# 'skipped' when nothing was wrong with it, which already happened once on
# Nov 2 -- or passes the gate and commits A's work under B's item.
#
# `mkdir` because it is atomic and macOS has no flock(1). The PID inside
# lets a crashed run's lock be taken over rather than wedging the loop
# forever, which is the usual way a lockfile turns one outage into a
# permanent one.
# ---------------------------------------------------------------------------
LOCK_DIR="${GETFIT_LOCK_DIR:-${TMPDIR:-/tmp}/getfit-feedback-loop.lock}"

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s' "$$" > "$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
    return 0
  fi

  local holder
  holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    log "another pass is still running (pid $holder); skipping this one"
    log "  nothing is wrong -- a pass that edits code outlives the 15m timer"
    return 1
  fi

  log "clearing a stale lock (pid ${holder:-unknown} is gone)"
  rm -rf "$LOCK_DIR"
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s' "$$" > "$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
    return 0
  fi
  log "could not take the lock after clearing it; skipping this pass"
  return 1
}

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
# The read-only key is NOT read into this process any more.
#
# It used to be pasted into the agent's prompt so it could check a
# submission's claims about stored data. The agent has no network access now
# (arbitrary curl is not on the allowlist), so the key bought nothing and
# putting a credential in a prompt for nothing is the wrong default. The file
# stays where it is; when the data check is rebuilt, this script will make
# those requests itself and pass the results in.

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
  # set_status <id> <status> [notes] [sha] [attempts]
  #
  # `attempts` is only ever passed as 0, by the paths where the fault was
  # this runner's and not the item's. An attempt is counted when an item is
  # taken, which is right, but it means a runner bug spends the item's
  # budget -- three were burned on an empty prompt the runner built itself.
  local id="$1" st="$2" notes="${3:-}" sha="${4:-}" attempts="${5:-}"
  local body
  body="$(jq -nc --arg s "$st" --arg n "$notes" --arg c "$sha" --arg a "$attempts" \
    '{status:$s} + (if $n == "" then {} else {result_notes:$n} end)
               + (if $c == "" then {} else {commit_sha:$c} end)
               + (if $a == "" then {} else {attempts:($a|tonumber)} end)')"
  api PATCH "/api/feedback/$id/" "$body" >/dev/null
}

# ---------------------------------------------------------------------------
# The build gate.
#
# The last thing between an unattended agent and a push. It now runs what CI
# runs: Django checks, migration drift, the backend suite, the frontend
# build, the frontend unit tests, the palette guard and the 390px layout
# gate. `npm run build` rather than a bare tsc because the project enables
# noUnusedLocals, so the build catches what a plain typecheck does not.
#
# Pushes go to staging, which is promoted to production by hand -- so this
# gate is not the last line of defence, but it is the only automatic one.
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
  # The frontend gates the build alone does not cover.
  #
  # `npm run build` is `tsc -b && vite build`: it type-checks and bundles,
  # and that is all. It ran clean on three frontend commits in one pass
  # while the unit tests, the palette guard and the 390px gate never ran at
  # all -- including on a change to the shared workout page. They passed
  # when checked afterwards, which was luck, not a gate.
  #
  # The palette guard exists because the rebrand's green kept coming back
  # one harmless-looking diff at a time, and the 390px gate exists because
  # this app is used on a phone and a desktop render cannot show a nav
  # covering an input. An unattended agent writing frontend code is exactly
  # who those were built for.
  if ! out="$(cd "$REPO/frontend" && npm run test 2>&1 | tail -30)"; then
    GATE_ERROR="frontend unit tests failed:
$out"; return 1
  fi
  if ! out="$(cd "$REPO/frontend" && npm run check:palette 2>&1 | tail -20)"; then
    GATE_ERROR="palette guard failed:
$out"; return 1
  fi
  # Playwright drives a real WebKit at 390x844 against a built preview, so
  # it is the slowest check here by far. It still runs: the alternative is
  # finding out from a screenshot on your phone, which is how both of
  # tonight's layout bugs were found.
  if ! out="$(cd "$REPO/frontend" && npm run test:e2e 2>&1 | tail -30)"; then
    GATE_ERROR="390px layout gate failed:
$out"; return 1
  fi
  GATE_ERROR=""
  return 0
}

# ---------------------------------------------------------------------------
# What an unattended run is allowed to do.
#
# WHY THIS EXISTS AT ALL: `claude -p` is a print-mode run with nobody to
# answer a permission prompt, so anything that would prompt is denied
# automatically. Nothing shipped between Oct 25 and Nov 4 for that reason --
# twelve items, four of which said "write permission was denied" in so many
# words. The agent diagnosed them correctly every time and then could not
# write the file.
#
# WHY AN ALLOWLIST AND NOT --dangerously-skip-permissions: this runner
# commits and pushes to staging on its own. Skipping all checks would let an
# item's text talk the agent into running any command on this machine, and
# the queue is the only thing standing between that and a push. An allowlist
# means a new capability is a deliberate edit to this line.
#
# NOT LISTED, DELIBERATELY:
#   git      — the runner owns git. The prompt already says not to commit or
#              push; this makes it so. Verified refused.
#   curl     — no network. The read-only API check is lost for now, which is
#              a real cost: a submission's claims about stored data have to
#              be taken on trust again. Allowing arbitrary curl with a token
#              on disk is the wrong trade; fetching the data in the runner
#              and passing it in the prompt is the right fix, later.
#   rm, mv, and everything else — the build gate catches a broken tree, not
#              a destroyed one.
#
# The test and build commands are listed so the agent can verify its own
# work. It writes tests either way; without these it never ran them.
CLAUDE_ALLOWED_TOOLS=(
  Read Edit Write Glob Grep
  "Bash(cd backend && venv/bin/python manage.py test:*)"
  "Bash(venv/bin/python manage.py test:*)"
  "Bash(venv/bin/python manage.py check:*)"
  "Bash(venv/bin/python manage.py makemigrations:*)"
  "Bash(cd frontend && npm run:*)"
  "Bash(npm run:*)"
  "Bash(npx tsc:*)"
  "Bash(npx vitest:*)"
)

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
    # Back to 'new', not 'skipped'.
    #
    # A dirty checkout is this runner's environment, not a judgement about
    # the item -- and 'skipped' is a FINISHED state, so the item left the
    # queue, lit the notification bell, and told you it had been considered
    # and declined. Two real items were lost that way. It stays queued.
    log "  REQUEUE: working tree is dirty — refusing to touch it."
    set_status "$id" new "The runner's checkout had uncommitted changes, so nothing ran. Not your item's fault; it stays queued." "" 0
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

  # The read-only data check is OFF, and the prompt says so plainly.
  #
  # It needs network access, and arbitrary curl is not on the allowlist: a
  # submission is text that an agent reads, so a run that can fetch any URL
  # with a token sitting on disk is a worse trade than losing the check.
  #
  # Saying nothing would be worse than saying it is gone. The agent used to
  # be told it could verify claims about stored data, and an instruction to
  # check something it cannot reach produces a confident guess instead of an
  # admission. The right fix is for THIS SCRIPT to fetch the data and pass it
  # in -- it already holds the token and makes no decisions from it -- which
  # keeps the check without giving the agent the network. Not built yet.
  local verify_block
  verify_block="$(cat <<'EOF'

YOU CANNOT CHECK THE STORED DATA IN THIS RUN:
A submission describes the problem as the user saw it, possibly days ago, and
possibly after they already worked around it. Its claims about what is in the
database are NOT facts, and this run has no way to look -- there is no network
access.

So do not write as though you had checked. If the change depends on a claim
about stored data, say in your summary which claim you had to take on trust.
If it depends on that claim being true and you cannot proceed safely without
it, make no change and say exactly:
ESCALATE: needs a data check I could not perform

This matters. One item was escalated to protect logged sets that the user had
already fixed before submitting, because the text said they were broken and
there was no way to look.
EOF
)"

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
  # --permission-prompts none makes the print-mode behaviour explicit rather
  # than incidental: nobody is here to answer, so anything outside the
  # allowlist is denied outright instead of hanging on a prompt.
  # Log the shape of the call, not its content.
  #
  # When this invocation broke, every item failed in 4 seconds with "Input
  # must be provided ... when using --print" and the log showed only the
  # error -- so there was no way to tell an empty prompt from a mis-parsed
  # flag without reconstructing the call by hand. One line prevents that.
  log "  invoking claude: prompt=${#prompt} chars, ${#CLAUDE_ALLOWED_TOOLS[@]} allowed tool(s)"

  # Never call claude with an empty prompt.
  #
  # `set -u` makes one unset variable inside that heredoc collapse the WHOLE
  # prompt to nothing -- and that is exactly what happened: an edit deleted
  # the block that builds $thread_block while leaving the reference to it, so
  # every item failed with "Input must be provided ... when using --print".
  # The queue read it as the agent declining to change anything and marked
  # four items failed. An empty prompt is a bug in this script, so say so
  # instead of blaming the item.
  if [[ -z "$prompt" ]]; then
    log "  ABORT: built an empty prompt — this is a bug in the runner, not the item"
    set_status "$id" new "The runner built an empty prompt and did not call the agent. Not your item's fault; it stays queued." "" 0
    return
  fi
  claude_out="$(cd "$REPO" && claude -p "$prompt" \
    --allowedTools "${CLAUDE_ALLOWED_TOOLS[@]}" \
    --permission-prompts none \
    2>&1 < /dev/null)"
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

  # Cross-check an empty result against the filter every server understands.
  #
  # This file and the backend deploy separately: the script runs from the
  # local checkout the moment it is saved, while the server is whatever is
  # live. Asking a server that predates 'claimable' for it returned 200 with
  # [] -- indistinguishable from an empty queue -- and the loop logged
  # "queue empty" with real work waiting. It went blind and said nothing.
  #
  # Checking 400 is not enough: that is what the UPDATED server answers for
  # an unknown filter, and the window that matters is the one before it
  # deploys, where the answer is []. So an empty 'claimable' is treated as
  # inconclusive and verified against 'new'. One extra request when there is
  # nothing to do, and it cannot go blind in either direction.
  if printf '%s' "$items" | jq -e 'type == "array" and length == 0' >/dev/null 2>&1; then
    local fallback
    fallback="$(api GET '/api/feedback/?status=new' || true)"
    if printf '%s' "$fallback" | jq -e 'type == "array" and length > 0' >/dev/null 2>&1; then
      log "'claimable' came back empty but status=new has work:"
      log "  the backend predates the claimable filter. Using status=new."
      log "  NOTE: abandoned 'in_progress' items stay stuck until it deploys."
      items="$fallback"
    fi
  elif ! printf '%s' "$items" | jq -e 'type == "array"' >/dev/null 2>&1 \
       && printf '%s' "$items" | grep -q 'unknown status'; then
    log "backend rejected 'claimable'; falling back to status=new"
    items="$(api GET '/api/feedback/?status=new' || true)"
  fi

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
    acquire_lock && run_pass
    sleep "$POLL_SECONDS"
  done
else
  acquire_lock || exit 0
  run_pass
fi
