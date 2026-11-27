# What is left

Written 26 Nov 2026, at `26a2094`. The Nov-23 brief (advice must respect the
plan, plan-edit week scope, effort/tokens, lazy weeks, health dates) is
**complete and in production**, along with the photo cutover and the chat
hardening before it.

This is what remains, why, and what would settle each one.

---

## Open: waiting on a person

### The production outage, 26 Nov 13:30–14:45 PST

**Unexplained.** Three candidate causes were ruled out by evidence:

- **Not a deploy.** The last promotion before the window was 25 Nov 00:19.
  The same build had been running over 24 hours.
- **Not the test suite.** Its only run that day was 14:54–15:03, which
  starts nine minutes *after* the window closed. (Staging is a Neon
  *branch* of the production project, so suite runs do share its compute —
  worth keeping in mind, just not the cause this time.)
- **Not the feedback loop.** `~/getfit-loop.log` has no entry since 24 Nov.

What was found and fixed while looking:

| | |
|---|---|
| `CONN_MAX_AGE` unset | A new Neon connection per request: 0.63s every time. Now 600s with `CONN_HEALTH_CHECKS`. Measured 0.79s → 0.37s. |
| Health checked nothing | Returned "ok" in 0.15s while real requests took 30s. Now runs `SELECT 1`, reports `database`, and keeps the pooled connection warm. |

**Still outstanding, and only the owner can do it:** the service runs
`gunicorn --workers 1 --threads 4`, so **four** slow requests saturate it —
and a chat turn holds a thread for its whole SSE stream. Reproduced: eight
concurrent guide requests took 32.5s and health went from 0.65s to 29.2s.
`backend/render.yaml` is documentation only; the real start command is in the
Render dashboard. Suggested `--workers 2 --threads 8` (or `--workers 1
--threads 16` if memory is tight — the work is I/O-bound).

Also worth doing: move the staging Neon branch to its own project, so test
runs can never touch production's compute.

**If it recurs**, `/api/health/` now reports its commit and its database, so
the next occurrence leaves evidence instead of a guess.

### G2 — effort and token tuning

G1 ships the measurement: per-stage timings, tokens per round, slowest tool,
logged on every exit path. **Deliberately not tuned yet.** Warm numbers are
4.8s for a lookup and 9.8s for an edit. A 24.6s first-turn cost was seen once
and three hypotheses for it were wrong (catalogue load ~23ms, SDK warm-up
~100ms, and "the health call proves the instance was warm" — it did not,
health never touched the database). The fourth candidate, Neon connection
setup, is now removed. **Let the logs accumulate from real traffic before
touching `effort` or `max_tokens`.**

### Photo marks

The review page is live and accepted:
<https://claude.ai/artifact/QR24zutHCw2QwNsFdSQY5o>. The owner reviewed ~200
of 873 and found them all right, *including ones the audit graded wrong* — so
nothing is withheld on a model read any more. Only `MISLABELLED_KB_KEYS` (four
hand-checked entries) blanks or redirects anything.

**If a wrong photo is reported:** add it there, don't re-run the audit.

---

## Open: not started

### Pass C — the third photo read

313 of 394 outstanding. **Recommend dropping it.** The owner's review is
better evidence than another model read, and the two reads already disagree
less than the owner disagrees with both.

### The A-series leftovers

A7's polish items (focus trap in the expanded composer, "New messages ↓"
firing on any upward scroll, hover-only delete ✕, `pendingTurn` TTL sweep) are
the only chat-hardening items not done. Low value individually.

---

## Known weak points, documented rather than fixed

**The eval harness scores generation and edits, not advice.** A1/A7 (deload
behaviour) are unit-tested, never evaluated across models. If the advice
prose is reworked, nothing measures whether it got better.

**`looks_like_a_deload` is a heuristic.** Volume down a quarter *and* sets not
up, against the previous week. It cannot see a deload that keeps volume
identical and only drops intensity, and it needs a previous week to compare
against. The phase map still wins when a plan has one — most generated plans
do; older plans do not.

**Weight increments are a judgement call.** Machines and cables 5 lb,
dumbbells and bars 2.5, unknown equipment 5. From the owner's own words
("for ones we can attach 2.5 lbs, or dumbbells, sure"). One table in
`api/weights.py` if a gym's equipment differs.

**The suite is not evidence on its own.** Five times in this stretch a live
run found what ~1,600 tests could not: a 503 behind "no photo", a screen
silently giving no answer, an agent killed by a tool schema one parameter over
the cap (the cap test passed because it never counted nested properties), a
deploy that never happened, and the connection overhead above. Every batch
should still get a live run.

**Prod verification depends on the probe account**
(`~/.getfit-prod-test-account`), whose plan has **no `program_outline`** — so
nothing phase-dependent can be verified there. That absence is what the
volume-based deload detection exists for.

---

## Operational notes worth keeping

- **Staging auto-deploy stalled silently once** (24 Nov), for 55 minutes,
  while pushes landed on GitHub. Verification against it was re-testing the
  old build. Always check `/api/health/`'s `commit` before trusting a live
  result. Frontend-only commits do not redeploy the backend, so a commit
  mismatch there is expected, not a stall.
- **The feedback queue is readable by a read-only API key** as of `662fa76`.
  Submission stays superuser-only and enforced in the view — that is the
  boundary that matters, since the loop writes code from this table.
- **The loop alarms on its own failures** as of `80316f0`: both push paths,
  plus a `check_unpushed` state check every pass that catches a crash between
  commit and push.
