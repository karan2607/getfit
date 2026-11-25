#!/usr/bin/env bash
#
# The stuck-runner alarm.
#
# A dirty checkout stopped this loop for eleven days: 1,928 refusals, twelve
# items queued, and no signal anywhere outside a log nobody was reading. The
# refusal itself is right; staying quiet about it is not. These cases pin the
# two things that make the alarm useful -- it waits before crying wolf, and
# it does not repeat itself into being muted.
#
#   bash scripts/test_blocked_alert.sh

set -uo pipefail

PASS=0
FAIL=0
ALERTS=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REPO="$WORK/repo"
LOG="$WORK/log"
BLOCK_STATE="$WORK/blocked"
BLOCK_ALERT_AFTER=3600
git init -q -b main "$REPO"

log() { printf '%s\n' "$*" >> "$LOG"; }
# Stand in for the desktop notification, so a test run stays silent and the
# alert is countable. `alert` is the loop's own one-line wrapper around
# osascript; stubbing the wrapper rather than the command keeps this test
# honest if notifications ever stop going through osascript.
alert() { ALERTS=$((ALERTS + 1)); }

eval "$(awk '/^blocked_pass\(\) \{/,/^\}/' scripts/feedback_loop.sh)"
eval "$(awk '/^unblocked_pass\(\) \{/,/^\}/' scripts/feedback_loop.sh)"

check() {
  # check <name> <expected alerts>
  local name="$1" want="$2"
  if [[ "$ALERTS" == "$want" ]]; then
    printf '  ok   %s (%s alert(s))\n' "$name" "$ALERTS"
    PASS=$((PASS + 1))
  else
    printf '  FAIL %s: wanted %s alert(s), got %s\n' "$name" "$want" "$ALERTS"
    FAIL=$((FAIL + 1))
  fi
}

# Rewind the recorded start so the next pass looks N seconds old.
age_block_by() {
  local secs="$1" first last
  read -r first last < "$BLOCK_STATE"
  printf '%s %s\n' "$((first - secs))" "$([[ $last == 0 ]] && echo 0 || echo $((last - secs)))" \
    > "$BLOCK_STATE"
}

echo "the stuck-runner alarm:"

ALERTS=0; rm -f "$BLOCK_STATE"
blocked_pass
check "the first blocked pass does not alert" 0

for _ in 1 2 3; do blocked_pass; done
check "a few minutes of blockage still does not alert" 0

age_block_by 3700
blocked_pass
check "an hour blocked raises one alert" 1

blocked_pass; blocked_pass
check "it does not alert again on the next passes" 1

age_block_by 3700
blocked_pass
check "another hour raises exactly one more" 2

# A pass that got through clears the record; the clock restarts from the next
# block rather than from the first one ever seen.
ALERTS=0
unblocked_pass
[[ -e "$BLOCK_STATE" ]] && { printf '  FAIL a clean pass left the block record behind\n'; FAIL=$((FAIL + 1)); } \
  || { printf '  ok   a clean pass clears the block record\n'; PASS=$((PASS + 1)); }
blocked_pass
check "a fresh block starts its hour over" 0

# A truncated or garbled state file must not wedge the alarm.
ALERTS=0
printf 'nonsense\n' > "$BLOCK_STATE"
blocked_pass
check "a corrupt state file is treated as a fresh block" 0

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
