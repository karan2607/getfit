import type { Page } from '@playwright/test'

/**
 * A signed-in shell, without a backend.
 *
 * The nav and the composer only exist behind auth, so the gate could not
 * reach the very screen the real-device screenshot came from. Stubbing the
 * API keeps the gate offline and deterministic: it is asserting LAYOUT, and
 * layout should not depend on a server being up.
 *
 * ONE stub for every signed-in spec. There were two, and they disagreed: the
 * catch-all here returned `{}` for everything unmatched, which made Diet,
 * Workouts and Sessions throw on `.map` and render an empty body — so a gate
 * pointed at those pages asserted against nothing at all and passed. The
 * other stub had already learned to return `[]` for collections. That
 * knowledge belongs in one place.
 */

const PROFILE = {
  id: 'p1', fitness_goal: 'lose_fat', experience_level: 'intermediate',
  dietary_preference: 'vegetarian', preferred_unit: 'lb',
  age: 31, gender: 'male', height_cm: 183, weight_kg: 90,
}

const SESSION = {
  id: 's1',
  title: 'Gate session',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

/**
 * Endpoints whose responses are ARRAYS.
 *
 * A page that does `rows.map(...)` on `{}` throws and takes the whole render
 * down with it, including the header and nav the gate is there to measure.
 * Being wrong in this list is silent, which is why it is a list and not a
 * guess per spec.
 */
const COLLECTIONS =
  /\/api\/(workouts\/plans|workouts\/sessions|workouts\/templates|diet\/plans|chat\/sessions|body\/scan\/history|meals)/

export interface SignInOptions {
  /** Render the superuser-only chrome (the notification bell). */
  superuser?: boolean
  /** Rows for `/api/feedback/notifications/`. */
  feedback?: unknown[]
  /** Extra routes, matched before the defaults. */
  extra?: (url: string) => unknown | undefined
}

export async function signIn(
  page: Page,
  route = '/chat',
  opts: SignInOptions = {},
) {
  const { superuser = false, feedback = [], extra } = opts

  await page.route('**/api/**', async (r) => {
    const url = r.request().url()
    const json = (body: unknown) =>
      r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

    const override = extra?.(url)
    if (override !== undefined) return json(override)

    if (url.includes('/api/auth/me/')) {
      return json({
        id: 'u1', email: 'gate@test.local', name: 'Gate',
        is_superuser: superuser,
        profile: PROFILE,
      })
    }
    // `/api/profile/` is fetched separately and used to refresh the profile.
    // Letting it fall through to the catch-all `{}` blanked the three fields
    // `isProfileComplete` checks, so every signed-in route redirected to
    // /onboarding and the gate never saw the screens it exists to assert on.
    if (url.includes('/api/profile')) return json(PROFILE)

    if (url.includes('/api/feedback/notifications')) {
      return json({
        unseen: feedback.reduce(
          (n: number, row) => n + ((row as { unread?: number }).unread ?? 0), 0),
        items: feedback,
      })
    }
    if (url.includes('/api/feedback/') && url.includes('/messages/')) {
      return json(feedback[0] ?? {})
    }

    // The composer only renders inside a session (/chat/:id), so the gate
    // needs BOTH the list and the detail. Matching the list pattern against
    // the detail URL too returned an array where the page expected an object,
    // and the whole thread rendered blank. Checked BEFORE COLLECTIONS, which
    // would otherwise claim this URL.
    if (/\/api\/chat\/sessions\/[^/?]+/.test(url)) {
      return json({ ...SESSION, messages: [] })
    }
    if (COLLECTIONS.test(url)) return json([])
    return json({})
  })

  await page.addInitScript(() => {
    window.localStorage.setItem('token', 'gate-token')
  })
  await page.goto(route)
  await page.waitForLoadState('networkidle')
}
