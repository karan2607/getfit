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

```
/loop Poll GET /api/feedback/?status=new. For each item:
      1. PATCH status=in_progress
      2. Implement the change
      3. Run the build gate (below). If it fails, PATCH status=failed with the
         error in result_notes and DO NOT push.
      4. Commit, push, PATCH status=done with result_notes and commit_sha.
```

## The build gate

There is no staging environment, so this gate is the only thing between the loop
and a broken production deploy. Both must pass before any commit:

```bash
cd backend  && python manage.py check && python manage.py test api.tests
cd frontend && npm run build        # tsc -b + vite build
```

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
