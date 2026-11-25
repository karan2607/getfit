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
# Where a verified fix goes.
#
# PRODUCTION by default, staging only for a change big enough to want a human
# look first. Staging-for-everything was the safer-looking choice and it cost
# more than it saved: production sat twelve days and sixteen commits behind,
# eleven of them feedback fixes that had been reported as "Fixed". The same
# photo bugs were reported three times because the fixes were never promoted.
# An undeployed fix is indistinguishable from no fix, so defaulting to staging
# did not reduce risk, it hid work.
#
# What still goes to staging (see `release_branch`): anything carrying a
# migration, and anything large. Those are the changes where a local gate
# tells you least and a bad one is hardest to undo.
#
# GETFIT_BRANCH pins a branch for every item and skips the decision entirely.
PROD_BRANCH="${GETFIT_PROD_BRANCH:-main}"
HOLD_BRANCH="${GETFIT_HOLD_BRANCH:-staging}"
# Work always HAPPENS on the integration branch and is promoted from there by
# fast-forwarding main onto the same commit. That ordering is what keeps this
# simple: there is no cherry-pick, no reset, and a held change stays exactly
# where it is instead of being rewritten under the next one.
BRANCH="${GETFIT_BRANCH:-$HOLD_BRANCH}"

# A change is "major" past either of these. Deliberately crude: the point is a
# predictable line, not a clever one.
BIG_FILES="${GETFIT_BIG_FILES:-12}"
BIG_LINES="${GETFIT_BIG_LINES:-600}"

#: Why this item is going where it is going, for the log and the report.
RELEASE_REASON=''

# promote_to_prod — whether the commit just made should also go live.
#
# Called AFTER the gate has passed and the commit has landed on the
# integration branch, so it reads the diff the agent actually produced rather
# than guessing from the feedback text. Returns 0 to promote.
promote_to_prod() {
  if [[ -n "${GETFIT_BRANCH:-}" ]]; then
    RELEASE_REASON="GETFIT_BRANCH is pinned to $GETFIT_BRANCH"
    return 1
  fi

  local migrations files lines behind
  # Migrations change the shape of real data and a local gate runs against an
  # empty database, so it proves the migration applies and nothing about what
  # it does to eight weeks of someone's logged sets.
  migrations="$(git -C "$REPO" diff --name-only 'HEAD~1' HEAD -- \
                  'backend/api/migrations/*.py' | wc -l | tr -d ' ')"
  if (( migrations > 0 )); then
    RELEASE_REASON="carries $migrations migration(s), so it wants a look first"
    return 1
  fi

  files="$(git -C "$REPO" diff --name-only 'HEAD~1' HEAD | wc -l | tr -d ' ')"
  lines="$(git -C "$REPO" diff --numstat 'HEAD~1' HEAD \
           | awk '{ a += $1; d += $2 } END { print a + d + 0 }')"
  if (( files >= BIG_FILES || lines >= BIG_LINES )); then
    RELEASE_REASON="large change: $files file(s), $lines changed line(s)"
    return 1
  fi

  # A small fix cannot be promoted past work someone is still holding: main
  # only ever fast-forwards, so pushing this commit would carry every commit
  # under it -- including the migration that was deliberately held back.
  behind="$(git -C "$REPO" rev-list --count \
            "origin/$PROD_BRANCH..HEAD~1" 2>/dev/null || echo 0)"
  if (( behind > 0 )); then
    RELEASE_REASON="held behind $behind change(s) already awaiting review on $HOLD_BRANCH"
    return 1
  fi

  RELEASE_REASON="$files file(s), $lines changed line(s), no migrations"
  return 0
}

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
  # set_status <id> <status> [notes] [sha] [attempts] [branch]
  #
  # `attempts` is only ever passed as 0, by the paths where the fault was
  # this runner's and not the item's. An attempt is counted when an item is
  # taken, which is right, but it means a runner bug spends the item's
  # budget -- three were burned on an empty prompt the runner built itself.
  local id="$1" st="$2" notes="${3:-}" sha="${4:-}" attempts="${5:-}" branch="${6:-}"
  local body
  # `branch` is what lets the app say "On staging" rather than "Fixed" on a
  # fix that is not deployed. Sent only when known, so an item that failed
  # before any push does not claim to have landed somewhere.
  body="$(jq -nc --arg s "$st" --arg n "$notes" --arg c "$sha" --arg a "$attempts" \
            --arg b "$branch" \
    '{status:$s} + (if $n == "" then {} else {result_notes:$n} end)
               + (if $c == "" then {} else {commit_sha:$c} end)
               + (if $a == "" then {} else {attempts:($a|tonumber)} end)
               + (if $b == "" then {} else {branch:$b} end)')"
  api PATCH "/api/feedback/$id/" "$body" >/dev/null
}

# ---------------------------------------------------------------------------
# The build gate.
#
# ---------------------------------------------------------------------------
# Every stage is timed and every stage has a ceiling.
#
# WHY: on Nov 11 one pass held the lock for 22 hours and 45 minutes. The Mac
# had 16.5GB of its 17.4GB swap in use and a load average of 104, so the
# frontend unit tests -- 9 seconds on an idle machine -- reported
# "Duration 3245.38s (transform 9641s, import 12835s, tests 120s)". Only 120s
# of that was running tests; the rest was page-fault wait. The backend suite
# was still going after two and a half hours. Five submissions queued up
# behind it and nothing said why, because a stage that HANGS logged nothing:
# only a stage that failed did.
#
# So a slow machine must fail an item, not silence the queue. Two changes:
# log the elapsed seconds of every stage whether it passes or not, and kill
# any stage that exceeds its ceiling. The ceilings are 3-6x the measured
# healthy cost (below), which is wide enough that a merely busy machine still
# passes and narrow enough that the worst case is about 70 minutes instead of
# a day.
#
# Measured on an idle machine, 2026-11-09:
#   django check 5s | makemigrations --check 3s | backend tests 510s
#   npm run build 11s | vitest 9s | palette 1s | playwright 40s   (~10 min)
#
# A timeout is NOT a code failure. GATE_ERROR says so explicitly, because the
# alternative is what already happened once: a thrashing machine timed out
# the frontend tests, the gate called it a test failure, and the rollback
# deleted a migration and two component directories for work that was
# probably fine.
# ---------------------------------------------------------------------------

# macOS ships no timeout(1) and the plist's PATH deliberately lists only what
# the runner needs, so this is bash and pgrep rather than coreutils.
#
# Killing the subshell alone is not enough: `npm run test` is npm, which
# forks node, which forks workers. Kill the subshell and the workers keep the
# CPU -- which on a loaded machine is how one hung stage becomes two. Walk
# the tree depth-first so children die before the parent can reap them.
kill_tree() {
  local pid="$1" kid
  for kid in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$kid"
  done
  kill -9 "$pid" 2>/dev/null || true
}

STAGE_OUT=""
STAGE_SECS=0

# run_stage <label> <limit_seconds> <dir> <cmd...>
# Returns the command's status, or 124 if it was killed for running long.
run_stage() {
  local label="$1" limit="$2" dir="$3"
  shift 3
  local tmp pid rc t0=$SECONDS
  tmp="$(mktemp "${TMPDIR:-/tmp}/getfit-gate.XXXXXX")"

  ( cd "$dir" && "$@" ) >"$tmp" 2>&1 &
  pid=$!

  # Elapsed comes from $SECONDS, not from counting polls: a 5s poll made a
  # 3-second stage report 5s, and the whole point of this logging is to be
  # able to trust the numbers afterwards.
  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS - t0 >= limit )); then
      kill_tree "$pid"
      wait "$pid" 2>/dev/null || true
      STAGE_SECS=$(( SECONDS - t0 ))
      STAGE_OUT="$(tail -30 "$tmp" 2>/dev/null)"
      rm -f "$tmp"
      log "  stage $label: KILLED after ${STAGE_SECS}s (ceiling ${limit}s)"
      return 124
    fi
    sleep 2
  done

  wait "$pid"; rc=$?
  STAGE_SECS=$(( SECONDS - t0 ))
  STAGE_OUT="$(tail -30 "$tmp" 2>/dev/null)"
  rm -f "$tmp"
  log "  stage $label: ${STAGE_SECS}s rc=$rc"
  return $rc
}

# The last thing between an unattended agent and a push. It runs what CI
# runs: Django checks, migration drift, the backend suite, the frontend
# build, the frontend unit tests, the palette guard and the 390px layout
# gate. `npm run build` rather than a bare tsc because the project enables
# noUnusedLocals, so the build catches what a plain typecheck does not.
#
# The frontend gates the build alone does not cover: `npm run build` is
# `tsc -b && vite build`, which type-checks and bundles and that is all. It
# ran clean on three frontend commits in one pass while the unit tests, the
# palette guard and the 390px gate never ran at all -- including on a change
# to the shared workout page. They passed when checked afterwards, which was
# luck, not a gate. The palette guard exists because the rebrand's green kept
# coming back one harmless-looking diff at a time, and the 390px gate exists
# because this app is used on a phone and a desktop render cannot show a nav
# covering an input.
#
# Pushes go to staging, which is promoted to production by hand -- so this
# gate is not the last line of defence, but it is the only automatic one.
build_gate() {
  local rc gate_start=$SECONDS

  # label                limit  dir              command
  local -a stages=(
    "django check|120|$REPO/backend|venv/bin/python manage.py check"
    "makemigrations --check|120|$REPO/backend|venv/bin/python manage.py makemigrations --check --dry-run"
    "backend tests|1800|$REPO/backend|venv/bin/python manage.py test api.tests"
    "frontend build|600|$REPO/frontend|npm run build"
    "frontend unit tests|600|$REPO/frontend|npm run test"
    "palette guard|120|$REPO/frontend|npm run check:palette"
    "390px layout gate|900|$REPO/frontend|npm run test:e2e"
  )

  local spec label limit dir cmd
  for spec in "${stages[@]}"; do
    IFS='|' read -r label limit dir cmd <<<"$spec"
    # Word-split is intended: these are fixed command strings above, not
    # anything the agent or a submission can reach.
    # shellcheck disable=SC2086
    run_stage "$label" "$limit" "$dir" $cmd
    rc=$?
    if (( rc == 124 )); then
      GATE_ERROR="the build gate timed out, so nothing was pushed.

\"$label\" was killed after ${limit}s. This is a TIMEOUT, not a failing
test -- the change may well be fine. It usually means the machine running
the loop was overloaded. Worth re-queueing once the machine is idle.

Last output:
$STAGE_OUT"
      log "  GATE TIMEOUT after $((SECONDS - gate_start))s total"
      return 1
    fi
    if (( rc != 0 )); then
      GATE_ERROR="$label failed:
$STAGE_OUT"
      log "  GATE FAILED at \"$label\" after $((SECONDS - gate_start))s total"
      return 1
    fi
  done

  log "  gate passed in $((SECONDS - gate_start))s"
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
# ONE verification command, not thirteen prefixes to guess at.
#
# This was a list of literal command prefixes -- "cd backend &&
# venv/bin/python manage.py test:*" and friends -- and nothing in the prompt
# told the agent what they were. So it composed the same commands its own
# way, an absolute path here, a different cd there, and the session denied
# every one. In its own words, from the first pass after the runner was
# unblocked:
#
#   "I couldn't run the new test: this unattended session denies shell
#    commands that need approval."
#
# It wrote tests and shipped them unrun, on every item. The build gate below
# still caught breakage before any push, so this leaked rather than broke --
# but an agent that cannot execute its own work can only guess whether it
# did the job.
#
# scripts/verify.sh is the fix: one entry point, one allowlist line, nothing
# to compose and nothing to match. The prompt names it explicitly.
CLAUDE_ALLOWED_TOOLS=(
  Read Edit Write Glob Grep
  "Bash(bash scripts/verify.sh:*)"
  "Bash(scripts/verify.sh:*)"
  "Bash(./scripts/verify.sh:*)"
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
# Escalation
# A desktop notification. The only way this loop reaches a person.
#
# Three copies of this osascript line had grown, which is two too many for
# something that is the difference between a problem being seen and a
# problem sitting in a log for eleven days.
alert() {
  # alert <title> <message>
  osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
}

# Commits that exist here and nowhere else.
#
# The loop once committed eight fixes and pushed none of them: the items
# read "Fixed", the work sat in a local branch for days, and the only
# record was a line in a log. Every push failure below now alerts, but
# detecting the STATE rather than the event is what makes this durable --
# it catches a crash between commit and push, an interrupted run, and
# whatever the next way to strand a commit turns out to be.
unpushed_commits() {
  local branch="${1:-$BRANCH}"
  git -C "$REPO" rev-list --count "origin/$branch..$branch" 2>/dev/null || printf '0'
}

check_unpushed() {
  local ahead
  ahead="$(unpushed_commits)"
  [[ "$ahead" =~ ^[0-9]+$ ]] || return 0
  (( ahead > 0 )) || { rm -f "$UNPUSHED_STATE"; return 0; }

  local now last=0
  now="$(date +%s)"
  [[ -r "$UNPUSHED_STATE" ]] && read -r last < "$UNPUSHED_STATE" 2>/dev/null
  [[ "$last" =~ ^[0-9]+$ ]] || last=0

  log "  $ahead commit(s) on $BRANCH have never been pushed"
  if (( now - last >= UNPUSHED_EVERY )); then
    alert "getfit feedback loop" \
          "$ahead commit(s) on $BRANCH were never pushed. The fixes are local only."
    printf '%s\n' "$now" > "$UNPUSHED_STATE"
  fi
}


# ---------------------------------------------------------------------------
# A blocked runner that only writes to its own log is a silent runner.
#
# Refusing a dirty checkout is right -- committing someone's half-finished
# work would be far worse. Refusing it forever without telling anyone is not:
# the queue looked alive the whole time, every item kept its "stays queued"
# note, and nothing ever said the loop had not run a single item since the
# 11th. Eleven days, twelve items, no signal.
#
# So after an hour of consecutive blocked passes, say so where the owner
# actually is -- their desktop -- and then at most once an hour after that,
# because an alert that repeats every 15 minutes gets muted and becomes
# another kind of silence.
BLOCK_STATE="${GETFIT_BLOCK_STATE:-$HOME/.getfit-loop-blocked}"
BLOCK_ALERT_AFTER="${GETFIT_BLOCK_ALERT_AFTER:-3600}"

blocked_pass() {
  local now first last
  now="$(date +%s)"
  first="$now"; last=0
  if [[ -r "$BLOCK_STATE" ]]; then
    read -r first last < "$BLOCK_STATE" 2>/dev/null || { first="$now"; last=0; }
    [[ "$first" =~ ^[0-9]+$ ]] || first="$now"
    [[ "$last" =~ ^[0-9]+$ ]] || last=0
  fi
  if (( now - first >= BLOCK_ALERT_AFTER )) \
     && (( last == 0 || now - last >= BLOCK_ALERT_AFTER )); then
    local hours=$(( (now - first) / 3600 ))
    log "  ALERT: blocked for ${hours}h — notifying."
    alert "getfit feedback loop is stuck" \
          "Uncommitted changes in $REPO have blocked it for ${hours}h. $(git -C "$REPO" status --short | wc -l | tr -d ' ') file(s)."
    last="$now"
  fi
  printf '%s %s\n' "$first" "$last" > "$BLOCK_STATE"
}

# A pass that got through clears the record, so the next block is timed from
# when it actually started rather than from the first one ever seen.
unblocked_pass() {
  rm -f "$BLOCK_STATE"
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
    # Name the files. Without this the log said only that something was
    # dirty, so the one fact needed to clear the block was the one fact it
    # withheld -- and a stale working tree stopped this runner for ELEVEN
    # days and 1,928 refusals before anyone looked.
    git -C "$REPO" status --short | sed 's/^/    /' | tee -a "$LOG" >/dev/null
    blocked_pass
    set_status "$id" new "The runner's checkout had uncommitted changes, so nothing ran. Not your item's fault; it stays queued." "" 0
    return
  fi
  unblocked_pass
  # Before touching anything, say whether the last run left work stranded.
  # Checked here rather than only after a push so it also catches a crash
  # between commit and push, and an interrupted run.
  check_unpushed
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
- RUN YOUR TESTS. This is the only shell command available to you:
      bash scripts/verify.sh backend <dotted.test.module>
  Also: \`bash scripts/verify.sh frontend\`, or \`all\`. Nothing else is
  permitted -- no git, no curl, no python, no npm directly -- so do not
  spend turns discovering that. Run the narrow target while iterating and
  the broader one before you finish, and say in your summary if a test
  could not be run.
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
    alert "getfit feedback loop" \
          "Push to $BRANCH failed. $sha is committed locally and nowhere else."
    set_status "$id" failed "Committed $sha locally but push failed. Resolve by hand."
    return
  fi
  log "  pushed $sha to $BRANCH"

  # Promotion. A verified small fix goes live now; anything held says so, in
  # the item itself, because "Fixed" on something that is not deployed is the
  # single most misleading thing this loop can tell you.
  local landed_on="$BRANCH" notes
  notes="$(final_paragraph "$claude_out")"
  if promote_to_prod; then
    if git -C "$REPO" push origin "HEAD:$PROD_BRANCH" >>"$LOG" 2>&1; then
      landed_on="$PROD_BRANCH"
      log "  promoted $sha to $PROD_BRANCH ($RELEASE_REASON)"
      notes="$notes

Live in production ($sha)."
    else
      # The item is still marked done below, because the fix IS built and
      # pushed -- but it is not live, and "Fixed" on something that is not
      # deployed is the most misleading thing this loop can say. The note
      # says so; this makes sure somebody hears it.
      log "  promotion to $PROD_BRANCH FAILED; the fix is on $BRANCH only"
      alert "getfit feedback loop" \
            "Promotion to $PROD_BRANCH failed. $sha is on $BRANCH but not live."
      notes="$notes

On $BRANCH ($sha). The push to $PROD_BRANCH failed, so this is not live yet."
    fi
  else
    log "  holding on $BRANCH: $RELEASE_REASON"
    notes="$notes

On $BRANCH ($sha), NOT live yet: $RELEASE_REASON. It needs promoting to $PROD_BRANCH."
  fi

  set_status "$id" done "$notes" "$sha" "" "$landed_on"
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
  report_exhausted
}

# ---------------------------------------------------------------------------
# Work that fell out of the queue
# ---------------------------------------------------------------------------
# An item that burns MAX_ATTEMPTS stops being claimable and nothing ever
# looks at it again. Thirteen were sitting there, the oldest from 21 October,
# and TEN of them had failed on the runner rather than on the request: a
# session limit, an expired OAuth token, a shell command the allowlist
# denied, or a build gate that was itself broken. None of that is the item's
# fault and all of it is invisible, because "failed" reads as a verdict.
#
# So the same channel that says "nothing is being worked on" also says "work
# left the queue". Once a day at most -- this is a backlog, not an incident.
EXHAUSTED_STATE="${GETFIT_EXHAUSTED_STATE:-$HOME/.getfit-loop-exhausted}"
EXHAUSTED_EVERY="${GETFIT_EXHAUSTED_EVERY:-86400}"
#: Unpushed-commit alarm: where the last warning time is kept, and how
#: often to repeat it. Same shape as the two alarms above.
UNPUSHED_STATE="${UNPUSHED_STATE:-$STATE_DIR/unpushed}"
UNPUSHED_EVERY="${UNPUSHED_EVERY:-21600}"

report_exhausted() {
  local failed now last
  failed="$(api GET '/api/feedback/?status=failed' | jq 'length' 2>/dev/null)"
  [[ "$failed" =~ ^[0-9]+$ ]] || return 0
  (( failed > 0 )) || { rm -f "$EXHAUSTED_STATE"; return 0; }

  now="$(date +%s)"
  last=0
  [[ -r "$EXHAUSTED_STATE" ]] && read -r last < "$EXHAUSTED_STATE" 2>/dev/null
  [[ "$last" =~ ^[0-9]+$ ]] || last=0

  log "  $failed item(s) have left the queue after $(printf '%s' "${MAX_ATTEMPTS:-3}") attempts"
  if (( now - last >= EXHAUSTED_EVERY )); then
    alert "getfit feedback" \
          "$failed item(s) gave up and left the queue. Run: bash scripts/feedback_loop.sh --retry-failed"
    printf '%s\n' "$now" > "$EXHAUSTED_STATE"
  fi
}

# Put every exhausted item back. One command, because the alternative is
# thirteen PATCHes by hand and therefore nobody does it.
retry_failed() {
  local ids id n=0
  ids="$(api GET '/api/feedback/?status=failed' | jq -r '.[].id' 2>/dev/null)"
  if [[ -z "$ids" ]]; then
    log "nothing in the failed state"
    return 0
  fi
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    set_status "$id" new \
      "Re-queued by a sweep: an item that runs out of attempts leaves the queue, and most of those failures were the runner's." \
      "" 0
    n=$((n + 1))
  done <<< "$ids"
  log "re-queued $n item(s)"
  rm -f "$EXHAUSTED_STATE"
}

if [[ "${1:-}" == "--retry-failed" ]]; then
  acquire_lock || exit 0
  retry_failed
elif [[ "${1:-}" == "--watch" ]]; then
  log "watching $API every ${POLL_SECONDS}s (ctrl-c to stop)"
  while true; do
    acquire_lock && run_pass
    sleep "$POLL_SECONDS"
  done
else
  acquire_lock || exit 0
  run_pass
fi
