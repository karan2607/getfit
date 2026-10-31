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
2. **Render** — create the staging web service BY HAND, matching
   `backend/render.yaml`. Do not point Render at the blueprint: the production
   service was created in the dashboard, and applying a blueprint now can try
   to take it over. `render.yaml` is the written spec, not the mechanism.

   ```
   DEBUG                = False
   SECRET_KEY           = <new, never reuse production's>
   DATABASE_URL         = <the Neon staging branch, pooled>
   ALLOWED_HOSTS        = getfit-backend-staging.onrender.com
   CORS_ALLOWED_ORIGINS = https://getfit-staging.onrender.com
   GEMINI_API_KEY       = <same as production>
   PRODUCTION_DB_HOST   = <production Neon hostname, no user or password>
   ```

   No `RESEND_API_KEY` or `FROM_EMAIL`: outbound email has never been
   configured on this project. No `SENTRY_DSN`: staging noise would devalue
   real alerts.
3. **Vercel** — the frontend is on Vercel, not Render. There is no staging
   service to create: Vercel builds a preview deployment for every branch, so
   pushing to `staging` already produces one.

   Two things to set, both in Vercel → Settings → Environment Variables:

   ```
   VITE_API_URL  (Preview only)     = https://getfit-backend-staging.onrender.com
   VITE_API_URL  (Production only)  = <the production backend URL>
   ```

   Scope matters. A single unscoped `VITE_API_URL` makes previews talk to the
   **production** backend, which is precisely the thing staging exists to
   prevent — the UI would look like staging while writing to real data.

   Then copy the **branch alias** (`…-git-staging-<scope>.vercel.app`, stable
   across deploys) into the backend's `CORS_ALLOWED_ORIGINS`. Do not use the
   per-commit preview URL; it changes every push and CORS would break.

   The SPA rewrite needs no dashboard setting — `frontend/vercel.json` has
   carried it since the first commit and applies to previews as well.

   If the preview URL returns a Vercel login page, Deployment Protection is on
   for previews (Settings → Deployment Protection). Turn it off for this
   project or anyone you share staging with will be blocked.

   If Render appends a suffix because the name is taken, correct
   `CORS_ALLOWED_ORIGINS` on the backend to the real URL. The symptom of a
   mismatch is that the site loads but every API call fails with a CORS error
   in the browser console and nothing appears in the backend logs.
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
