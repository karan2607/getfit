# Staging

Everything deploys to staging first and is promoted to production by hand.

## Why

Before this, `main` went straight to production with only a local test run in
front of it. Two things that gate structurally cannot catch:

- **Production-only breaks.** The Django 5 outage was green locally on 4.2 and
  broke on production's 5.x. CI now runs 3.9/3.12/3.13, but the general class
  remains.
- **UI regressions.** The component tests cover logic, not the rendering of
  every surface. A grouped card that renders wrong still needs eyes.

And one thing it could never do: **verify a data migration against real data.**
The local database is empty, so the `Composite` conversion and upgrade steps 6
and 7 were all verified against synthetic fixtures. Staging points at a Neon
*branch* of production, which finally makes that possible.

## Layout

| | branch | database |
|---|---|---|
| `getfit-backend` | `main` | production Neon |
| `getfit-backend-staging` | `staging` | Neon `staging` branch |
| `getfit-staging` (static) | `staging` | — |

Staging runs `DEBUG=False`, same as production. Turning it on would hide the
exact failure mode staging exists to catch — a tracebackless 500 is part of
what production looks like.

## Workflow

```bash
git checkout staging && git merge --ff-only main   # or commit directly
git push origin staging                            # auto-deploys
# ... look at it ...
git checkout main && git merge --ff-only staging
git push origin main                                # auto-deploys to prod
```

The feedback loop pushes to `staging`, not `main`, so it can no longer put an
unreviewed change in front of users — only in front of you.

## Verifying a data migration before it reaches production

```bash
# counts on production, for comparison
DATABASE_URL="$PROD_URL" python manage.py upgrade_data --dry-run

# then on staging, which has the same shaped data
DATABASE_URL="$STAGING_URL" PRODUCTION_DB_HOST="<prod host>" \
  python manage.py reset_staging --counts
DATABASE_URL="$STAGING_URL" python manage.py migrate
```

`reset_staging` **refuses to run** unless it can prove it is not pointed at
production, and an unset `PRODUCTION_DB_HOST` counts as "cannot prove" rather
than "probably fine" — an unset variable is the likeliest way to end up on the
wrong database.

Re-branching staging from production is done in the Neon dashboard; this repo
has no Neon API token.

## One-time setup

1. **Neon** — create a branch named `staging` from `main`. Copy its pooled
   connection string.
2. **Render** — apply the blueprint (`backend/render.yaml`), which creates
   `getfit-backend-staging`. Then set the `sync: false` variables on it:
   `DATABASE_URL` (the staging branch), `ALLOWED_HOSTS`,
   `CORS_ALLOWED_ORIGINS`, `GEMINI_API_KEY`, `RESEND_API_KEY`.
3. **Render** — create a static site `getfit-staging` from the `staging`
   branch, root `frontend`, build `npm ci && npm run build`, publish `dist`,
   with `VITE_API_URL` set to the staging backend URL.
4. **Git** — `git checkout -b staging && git push -u origin staging`.

## Known costs

- Staging holds a **raw copy of production data**, including real health data.
  That was a deliberate choice: fidelity for migration testing, on the basis
  that every deploy passes through staging so it gets the same care. If that
  stops being true, add a scrub step on branch creation.
- Staging shares production's `GEMINI_API_KEY`, so generating plans there burns
  the same credits.
- The free Render tier spins down when idle; the first request after a quiet
  spell takes ~50s.
