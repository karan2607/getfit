import type { Page } from '@playwright/test'

/**
 * A signed-in shell, without a backend.
 *
 * The nav and the composer only exist behind auth, so the gate could not
 * reach the very screen the real-device screenshot came from. Stubbing the
 * API keeps the gate offline and deterministic: it is asserting LAYOUT, and
 * layout should not depend on a server being up.
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

export async function signIn(page: Page, route = '/chat') {
  await page.route('**/api/**', async (r) => {
    const url = r.request().url()
    const json = (body: unknown) =>
      r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

    if (url.includes('/api/auth/me/')) {
      return json({
        id: 'u1', email: 'gate@test.local', name: 'Gate',
        is_superuser: false,
        profile: PROFILE,
      })
    }
    // `/api/profile/` is fetched separately and used to refresh the profile.
    // Letting it fall through to the catch-all `{}` blanked the three fields
    // `isProfileComplete` checks, so every signed-in route redirected to
    // /onboarding and the gate never saw the screens it exists to assert on.
    if (url.includes('/api/profile')) return json(PROFILE)

    // The composer only renders inside a session (/chat/:id), so the gate
    // needs BOTH the list and the detail. Matching the list pattern against
    // the detail URL too returned an array where the page expected an object,
    // and the whole thread rendered blank.
    if (/\/api\/chat\/sessions\/[^/?]+/.test(url)) {
      return json({ ...SESSION, messages: [] })
    }
    if (url.includes('/api/chat/sessions')) return json([SESSION])
    return json({})
  })

  await page.addInitScript(() => {
    window.localStorage.setItem('token', 'gate-token')
  })
  await page.goto(route)
  await page.waitForLoadState('networkidle')
}
