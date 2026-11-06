import { expect, test } from '@playwright/test'
import { signIn } from './signed-in'

/**
 * "Set active" while the POST is still in flight.
 *
 * The handler always updated local state and always toasted -- but only once
 * the awaited POST resolved, with the button left enabled and unchanged in
 * the meantime. Against a backend taking tens of seconds that is
 * indistinguishable from a dead button, and it was reported as "no change on
 * the Workouts page, then a delayed toast after navigating away".
 *
 * So the response here is deliberately slow. A fast stub would pass against
 * the unfixed code and prove nothing, which is exactly how this shipped.
 */

const PLAN = {
  id: 'pl-1', title: 'Beginner Full-Body Strength',
  description: 'Three days a week', duration_weeks: 8,
  is_active: false, weeks_ready: 1,
}

/** Long enough to observe, short enough not to slow the gate. */
const POST_DELAY_MS = 1500

test('the button says it is working while the POST is in flight', async ({ page }) => {
  await signIn(page, '/workouts')

  // Re-route after sign-in so the activate POST can be delayed on its own.
  await page.route('**/api/workouts/plans/', async (r) => {
    await r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([PLAN]),
    })
  })
  await page.route('**/api/workouts/plans/*/activate/', async (r) => {
    await new Promise((res) => setTimeout(res, POST_DELAY_MS))
    await r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ...PLAN, is_active: true, activated_at: '2026-11-05T00:00:00Z' }),
    })
  })
  await page.reload()
  await page.waitForLoadState('networkidle')

  const button = page.getByRole('button', { name: 'Set active' })
  await expect(button).toBeVisible()
  await button.click()

  // Immediately, not after the response.
  await expect(page.getByRole('button', { name: 'Activating…' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Activating…' })).toBeDisabled()

  // And then the plan is actually active, with the button gone.
  await expect(page.getByText('Active')).toBeVisible({ timeout: 5_000 })
  await expect(page.getByRole('button', { name: /Set active|Activating/ })).toHaveCount(0)
})

test('a second click cannot fire a second activation', async ({ page }) => {
  let posts = 0
  await signIn(page, '/workouts')
  await page.route('**/api/workouts/plans/', async (r) => {
    await r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([PLAN, { ...PLAN, id: 'pl-2', title: 'Push Pull Legs' }]),
    })
  })
  await page.route('**/api/workouts/plans/*/activate/', async (r) => {
    posts += 1
    await new Promise((res) => setTimeout(res, POST_DELAY_MS))
    await r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ...PLAN, is_active: true }),
    })
  })
  await page.reload()
  await page.waitForLoadState('networkidle')

  const buttons = page.getByRole('button', { name: 'Set active' })
  await expect(buttons).toHaveCount(2)
  await buttons.first().click()
  // The OTHER plan's button must go dead too: two activations racing would
  // leave whichever POST landed last as the winner.
  await expect(buttons.first()).toBeDisabled()
  await buttons.first().click({ force: true }).catch(() => {})
  await page.waitForTimeout(POST_DELAY_MS + 500)
  expect(posts, 'more than one activation was sent').toBe(1)
})
