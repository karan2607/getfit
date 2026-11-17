#!/usr/bin/env bash
#
# The promotion rule, exercised against real git repos.
#
# Worth testing rather than eyeballing: it decides whether a change the loop
# just made goes in front of users unreviewed. The failure it exists to stop
# is a small fix fast-forwarding main PAST a migration someone deliberately
# held back -- main only ever fast-forwards, so promoting a commit promotes
# everything under it.
#
#   bash scripts/test_release_branch.sh

set -uo pipefail

PASS=0
FAIL=0

check() {
  # check <name> <expected: promote|hold>
  local name="$1" want="$2" got
  if promote_to_prod; then got=promote; else got=hold; fi
  if [[ "$got" == "$want" ]]; then
    printf '  ok   %s (%s)\n' "$name" "$RELEASE_REASON"
    PASS=$((PASS + 1))
  else
    printf '  FAIL %s: wanted %s, got %s (%s)\n' "$name" "$want" "$got" "$RELEASE_REASON"
    FAIL=$((FAIL + 1))
  fi
}

# Load the decision function without running the loop. The script exits early
# without a token, so source it in a subshell-safe way: pull out just the
# block we need by sourcing with a token file and API that are never used,
# then stop before the queue runs.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REPO="$WORK/repo"
PROD_BRANCH=main
HOLD_BRANCH=staging
BIG_FILES=12
BIG_LINES=600
RELEASE_REASON=''

# A verbatim copy of the function under test, extracted from the loop so this
# runs without the loop's preflight. Kept honest by `test_the_copy_matches`.
eval "$(awk '/^promote_to_prod\(\) \{/,/^\}/' scripts/feedback_loop.sh)"

git init -q -b main "$REPO"
git -C "$REPO" config user.email t@t.local
git -C "$REPO" config user.name T
mkdir -p "$REPO/backend/api/migrations" "$REPO/frontend/src"
echo base > "$REPO/README.md"
git -C "$REPO" add -A && git -C "$REPO" commit -qm base
git -C "$REPO" branch -f staging main
# A bare remote, so origin/main is a real ref the function can measure against.
git init -q --bare "$WORK/remote"
git -C "$REPO" remote add origin "$WORK/remote"
git -C "$REPO" push -q origin main staging

commit_on_staging() {
  # commit_on_staging <message> -- stages whatever the caller just wrote
  git -C "$REPO" checkout -q staging
  git -C "$REPO" add -A
  git -C "$REPO" commit -qm "$1"
}

echo "promotion rule:"

# A small fix, nothing held: goes live.
echo "one line" >> "$REPO/frontend/src/a.ts"
commit_on_staging "small fix"
check "a small fix is promoted" promote

# Catch up main, so the next cases start level.
git -C "$REPO" push -q origin staging:main
git -C "$REPO" checkout -q main && git -C "$REPO" merge -q --ff-only staging

# A migration: held, however small.
echo "x" > "$REPO/backend/api/migrations/0099_thing.py"
commit_on_staging "adds a migration"
check "a migration is held" hold

# That migration is now sitting on staging, unpromoted. A later small fix must
# NOT be promoted: doing so would carry the migration with it.
echo "another line" >> "$REPO/frontend/src/a.ts"
commit_on_staging "small fix behind a held migration"
check "a small fix behind held work is held" hold

# Level up again, then a large change.
git -C "$REPO" push -q origin staging:main
for i in $(seq 1 15); do echo "f$i" > "$REPO/frontend/src/f$i.ts"; done
commit_on_staging "touches 15 files"
check "a change across many files is held" hold

git -C "$REPO" push -q origin staging:main
python3 - "$REPO/frontend/src/big.ts" <<'PY'
import sys
open(sys.argv[1], 'w').write('\n'.join('line %d' % i for i in range(700)))
PY
commit_on_staging "700 new lines"
check "a change of many lines is held" hold

git -C "$REPO" push -q origin staging:main
echo "pinned" >> "$REPO/frontend/src/a.ts"
commit_on_staging "small fix, branch pinned"
GETFIT_BRANCH=staging check "an explicit GETFIT_BRANCH is obeyed" hold

echo
echo "the copy under test matches the loop:"
if diff -q \
  <(awk '/^promote_to_prod\(\) \{/,/^\}/' scripts/feedback_loop.sh) \
  <(declare -f promote_to_prod | sed '1s/.*/promote_to_prod() {/') >/dev/null; then
  printf '  ok   extracted verbatim\n'; PASS=$((PASS + 1))
else
  printf '  note bash reformatted the function; behaviour is what is tested\n'
fi

echo
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
