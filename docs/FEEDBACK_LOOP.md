# Feedback loop

An in-app button queues change requests; an agent loop picks them up, implements
them, and ships them.

- **UI:** [`frontend/src/components/FeedbackButton.tsx`](../frontend/src/components/FeedbackButton.tsx)
  — a floating button, plus a Queue tab showing status and results
- **API:** `GET/POST /api/feedback/`, `GET/PATCH/DELETE /api/feedback/<id>/`
- **Model:** `Feedback` in [`backend/api/models.py`](../backend/api/models.py)

## Superuser-only, enforced server-side

Both endpoints reject non-superusers with **403**. The button is hidden for
ordinary users via `is_superuser` on `/api/auth/me/`, but **hiding the button is
not the access control** — the view check is.

This restriction is the security boundary for the whole design. The loop reads
this table and writes code from its contents, so the table must only ever contain
instructions authored by someone trusted. If any signed-in user could POST here,
the endpoint would become a way to dictate code changes to the repository and get
them deployed. Covered by `FeedbackAccessControlTests` in
[`backend/api/tests/test_feedback.py`](../backend/api/tests/test_feedback.py).

The submitter also cannot forge `status`, `result_notes` or `commit_sha` — those
are read-only on the submit serializer and only writable through `PATCH`, which
is how the loop reports back.

## Statuses

`new` → `in_progress` → `done` | `failed` | `skipped`

## Running the loop

[`scripts/feedback_loop.sh`](../scripts/feedback_loop.sh) owns the queue
mechanics and hands each item to a fresh Claude Code session for the actual code
change.

### Prerequisites

The runner shells out to the **Claude Code CLI**, which is separate from the IDE
extension. If `command -v claude` prints nothing, install it first:

```bash
npm install -g @anthropic-ai/claude-code
```

`curl`, `jq` and `git` are also required (`brew install jq` if needed).

### Setup (once)

```bash
export GETFIT_API=https://<your-backend>.onrender.com
printf '%s' '<your-40-char-app-token>' > ~/.getfit-loop-token
chmod 600 ~/.getfit-loop-token
```

Get a token from `POST /api/auth/login/`. It must belong to a **superuser** —
the queue endpoints 403 otherwise.

### Run

```bash
bash scripts/feedback_loop.sh            # one pass over the queue
bash scripts/feedback_loop.sh --watch    # poll every 5 min (GETFIT_POLL_SECONDS)
```

Logs to `~/getfit-loop.log`.

### Durable scheduling

A cron job created inside a Claude session is **session-only** — it stops when
that session ends. For something that survives restarts, use `launchd`. Save as
`~/Library/LaunchAgents/com.getfit.feedbackloop.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.getfit.feedbackloop</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/karan/Documents/projects/getfit/scripts/feedback_loop.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>GETFIT_API</key><string>https://YOUR-BACKEND.onrender.com</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>/tmp/getfit-loop.out</string>
  <key>StandardErrorPath</key><string>/tmp/getfit-loop.err</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.getfit.feedbackloop.plist
launchctl list | grep getfit          # confirm it's registered
launchctl unload ~/Library/LaunchAgents/com.getfit.feedbackloop.plist   # to stop
```

### What the runner guarantees

- **Refuses to run on a dirty working tree** — it will not sweep up your
  in-progress edits into an automated commit.
- Starts each item from a freshly pulled `main`.
- **Reverts everything** if the build gate fails, and records the error in
  `result_notes` so you can read it in the Queue tab.
- Marks `skipped` rather than guessing when the agent escalates (see below).
- Only marks `done` after a push actually succeeds.

### Escalation

The agent is instructed to refuse two classes of change and emit `ESCALATE:`
instead, which the runner records as `skipped`:

1. **Data migrations that mutate existing rows.** The local database is empty, so
   a backfill cannot be meaningfully verified before it runs against production.
2. **Anything touching authentication or permissions**, or that widens access.

Both land in your Queue tab for you to handle by hand.

## The build gate

The loop pushes to the **`staging`** branch, which auto-deploys to
`getfit-backend-staging` against a Neon branch of the production database.
Promotion to `main` is a deliberate act, so the loop cannot put an unreviewed
change in front of users — only in front of you.

This gate still runs before anything is pushed, because catching a failure
here is cheaper than catching it on staging. Both must pass before any commit:

```bash
cd backend  && python manage.py check && python manage.py test api.tests
cd frontend && npm run build        # tsc -b + vite build
```

What the gate CANNOT catch, and what staging is for: a UI regression (the
component tests cover the logic, not the rendering of every surface) and a
production-only break. The Django 5 outage passed a green local run on 4.2 and
broke on production's 5.x.

Run the **full** `npm run build`, not just `npx tsc --noEmit`: the project config
enables `noUnusedLocals`, so the build catches errors the bare typecheck does not.

Same gate runs in CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)),
on Python 3.9 and 3.12, so a push that slips through locally still gets caught —
but after deploy, which is why the gate runs first.

## Why the gate matters more than usual here

The loop pushes to `main`, and Render auto-deploys from `main` with
`migrate && upgrade_data` in its build command. A bad push therefore reaches real
user data without a human in the loop. Two rules follow:

1. **Never let the loop write a data migration unreviewed.** Schema additions are
   fine; a `RunPython` that mutates existing rows should be escalated to a human,
   because the local database is empty and a backfill cannot be meaningfully
   verified before it runs against production.
2. **Prefer additive, reversible changes.** If an item can only be done
   destructively, mark it `skipped` with a note rather than guessing.
