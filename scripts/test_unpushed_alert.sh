#!/usr/bin/env bash
#
# The unpushed-commit alarm.
#
# The loop once committed eight fixes and pushed none of them. The items
# read "Fixed", the work sat on a local branch for days, and the only
# record was a line in a log nobody reads. The push failures themselves now
# alert, but this checks the STATE rather than the event -- so it also
# catches a crash between commit and push, an interrupted run, and whatever
# the next way to strand a commit turns out to be.
#
#   bash scripts/test_unpushed_alert.sh

set -uo pipefail

PASS=0
FAIL=0
ALERTS=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REPO="$WORK/repo"
REMOTE="$WORK/remote.git"
LOG="$WORK/log"
UNPUSHED_STATE="$WORK/unpushed"
UNPUSHED_EVERY=21600
BRANCH=staging

git init -q --bare "$REMOTE"
git init -q -b "$BRANCH" "$REPO"
git -C "$REPO" config user.email t@t.local
git -C "$REPO" config user.name T
git -C "$REPO" remote add origin "$REMOTE"
printf 'one\n' > "$REPO/f"
git -C "$REPO" add f
git -C "$REPO" commit -qm one
git -C "$REPO" push -q -u origin "$BRANCH"

log() { printf '%s\n' "$*" >> "$LOG"; }
# Stand in for the desktop notification, so a test run stays silent and the
# alert is countable.
alert() { ALERTS=$((ALERTS + 1)); }

eval "$(awk '/^unpushed_commits\(\) \{/,/^\}/' scripts/feedback_loop.sh)"
eval "$(awk '/^check_unpushed\(\) \{/,/^\}/' scripts/feedback_loop.sh)"

check() {
  local name="$1" want="$2"
  if [[ "$ALERTS" == "$want" ]]; then
    printf '  ok   %s (%s alert(s))\n' "$name" "$ALERTS"
    PASS=$((PASS + 1))
  else
    printf '  FAIL %s: wanted %s alert(s), got %s\n' "$name" "$want" "$ALERTS"
    FAIL=$((FAIL + 1))
  fi
}

commit_locally() {
  printf '%s\n' "$1" >> "$REPO/f"
  git -C "$REPO" add f
  git -C "$REPO" commit -qm "$1"
}

age_alarm_by() {
  local secs="$1" last
  read -r last < "$UNPUSHED_STATE"
  printf '%s\n' "$((last - secs))" > "$UNPUSHED_STATE"
}

echo "the unpushed-commit alarm:"

ALERTS=0; rm -f "$UNPUSHED_STATE"
check_unpushed
check "a branch in step with origin does not alert" 0

commit_locally two
check_unpushed
check "a commit that was never pushed alerts at once" 1

check_unpushed; check_unpushed
check "it does not repeat itself into being muted" 1

age_alarm_by 25000
check_unpushed
check "it says so again after the interval" 2

git -C "$REPO" push -q origin "$BRANCH"
ALERTS=0
check_unpushed
check "pushing the work clears it" 0

[[ -e "$UNPUSHED_STATE" ]] \
  && { printf '  FAIL the state file outlives the problem\n'; FAIL=$((FAIL + 1)); } \
  || { printf '  ok   the state file is cleared, so the next lapse alerts at once\n'; PASS=$((PASS + 1)); }

echo
echo "counting what is really there:"
commit_locally three
commit_locally four
got="$(unpushed_commits)"
if [[ "$got" == 2" " || "$got" == "2" ]]; then
  printf '  ok   two local commits count as 2\n'; PASS=$((PASS + 1))
else
  printf '  FAIL wanted 2, got %s\n' "$got"; FAIL=$((FAIL + 1))
fi

echo
printf '%s passed, %s failed\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
