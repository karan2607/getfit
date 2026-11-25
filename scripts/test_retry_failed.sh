#!/usr/bin/env bash
#
# The sweep that puts exhausted items back.
#
# An item that burns MAX_ATTEMPTS stops being claimable and nothing looks at
# it again. Thirteen were sitting there, oldest from 21 October, and ten had
# failed on the RUNNER -- a session limit, an expired token, a denied shell
# command, a broken build gate -- not on the request. "failed" reads as a
# verdict on the item, which is why nobody went back to them.
#
# Tested with stubs rather than against the live queue: running the real
# thing would re-open items that were closed on purpose.
#
#   bash scripts/test_retry_failed.sh

set -uo pipefail

PASS=0
FAIL=0

check() {
  local name="$1" want="$2" got="$3"
  if [[ "$got" == "$want" ]]; then
    printf '  ok   %s\n' "$name"
    PASS=$((PASS + 1))
  else
    printf '  FAIL %s: wanted %s, got %s\n' "$name" "$want" "$got"
    FAIL=$((FAIL + 1))
  fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

LOG="$WORK/log"
EXHAUSTED_STATE="$WORK/exhausted"
EXHAUSTED_EVERY=86400
QUEUE='[]'
CALLS="$WORK/calls"
: > "$CALLS"

log() { printf '%s\n' "$*" >> "$LOG"; }
api() { printf '%s' "$QUEUE"; }
set_status() { printf '%s %s %s\n' "$1" "$2" "${5:-}" >> "$CALLS"; }
# `alert` is the loop's own wrapper around osascript. Stubbing the wrapper
# rather than the command keeps this honest if notifications ever stop
# going through osascript.
alert() { printf 'ALERT\n' >> "$CALLS"; }

eval "$(awk '/^retry_failed\(\) \{/,/^\}/' scripts/feedback_loop.sh)"
eval "$(awk '/^report_exhausted\(\) \{/,/^\}/' scripts/feedback_loop.sh)"

echo "the failed-item sweep:"

QUEUE='[{"id":"a"},{"id":"b"},{"id":"c"}]'
: > "$CALLS"
retry_failed
check "every exhausted item is put back" "3" "$(grep -c ' new ' "$CALLS")"
check "and its attempt count is reset" "3" "$(grep -c ' new 0$' "$CALLS")"

QUEUE='[]'
: > "$CALLS"
retry_failed
check "an empty failed list sweeps nothing" "0" "$(wc -l < "$CALLS" | tr -d ' ')"

# -- the alarm ------------------------------------------------------------
QUEUE='[{"id":"a"}]'
: > "$CALLS"; rm -f "$EXHAUSTED_STATE"
report_exhausted
check "items off the queue raise one alert" "1" "$(grep -c ALERT "$CALLS")"

report_exhausted
check "and not again on the next pass" "1" "$(grep -c ALERT "$CALLS")"

printf '%s\n' "$(( $(date +%s) - 90000 ))" > "$EXHAUSTED_STATE"
report_exhausted
check "but again the next day" "2" "$(grep -c ALERT "$CALLS")"

QUEUE='[]'
: > "$CALLS"
report_exhausted
check "a clean queue is silent" "0" "$(grep -c ALERT "$CALLS")"
check "and forgets it had complained" "absent" \
  "$([[ -e "$EXHAUSTED_STATE" ]] && echo present || echo absent)"

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
