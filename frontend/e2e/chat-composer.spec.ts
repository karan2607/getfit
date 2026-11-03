import { expect, test } from '@playwright/test'
import { signIn } from './signed-in'

/**
 * Defects proven by a real-device screenshot (iPhone, 2 Nov 2026).
 *
 * This is the first TRUE phone-size evidence in the project -- the earlier
 * audit rendered desktop-width screenshots only, which is why the 390px gate
 * exists. These are reproductions, not hypotheses.
 *
 * The composer and nav live behind auth, so these run against /login, which
 * carries the same nav and the same page-level layout. The composer case gets
 * its own harness below.
 */

const LONG_PROMPT =
  'Update my plan to remove barbell bench press, heavy incline barbell press '
  + 'and barbell romanian deadlift across every week, replacing each with a '
  + 'machine or dumbbell alternative at the same sets and rep scheme and my '
  + 'standing constraints: no deadlifts from the floor, no standing overhead '
  + 'press (machine/seated overhead press is fine). Finally, list any other '
  + 'exercises in the plan that heavily load the upper back, but do not '
  + 'change them — just list them for my review.'

test('the bottom nav fits six labels at 390px with nothing clipped', async ({ page }) => {
  await signIn(page)

  const nav = page.locator('nav').filter({ hasText: 'Workouts' }).first()
  await expect(nav).toBeVisible()

  // "Home" rendered as "ome" and "Profile" as "Prof" on a real iPhone: the
  // bar held six items but did not fit the viewport.
  const clipped = await page.evaluate(() => {
    const labels = Array.from(
      document.querySelectorAll<HTMLElement>('nav a span:last-child'),
    )
    return labels
      .map((el) => {
        const r = el.getBoundingClientRect()
        return {
          text: (el.textContent || '').trim(),
          left: Math.round(r.left),
          right: Math.round(r.right),
          // A label narrower than its own text is truncated by ellipsis or
          // overflow, which is how "Profile" became "Prof".
          truncated: el.scrollWidth > el.clientWidth + 1,
        }
      })
      .filter((l) => l.left < 0 || l.right > window.innerWidth || l.truncated)
  })
  expect(clipped, 'nav labels clipped or truncated at 390px').toEqual([])
})

test('every nav label is fully inside the viewport', async ({ page }) => {
  await signIn(page)

  const boxes = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('nav a')).map((el) => {
      const r = el.getBoundingClientRect()
      return { text: (el.textContent || '').trim(), left: Math.round(r.left), right: Math.round(r.right) }
    }),
  )
  for (const box of boxes) {
    expect(box.left, `${box.text} starts off-screen`).toBeGreaterThanOrEqual(0)
    expect(box.right, `${box.text} ends off-screen`).toBeLessThanOrEqual(391)
  }
})

test('the nav uses drawn icons, not emoji', async ({ page }) => {
  await signIn(page)

  // Emoji render inconsistently across devices and read as unfinished.
  const emoji = await page.evaluate(() => {
    const text = Array.from(document.querySelectorAll('nav a'))
      .map((el) => el.textContent || '')
      .join('')
    return [...text].filter((ch) => ch.codePointAt(0)! > 0x2100)
  })
  expect(emoji, 'emoji found in the nav').toEqual([])
})

test('no field is small enough to trigger an iOS zoom', async ({ page }) => {
  /*
   * THE root cause of the real-device report. Safari auto-zooms any focused
   * input under 16px, and that zoom -- not a layout bug -- is what clipped
   * the nav at both edges and made the composer look like half the screen.
   */
  await signIn(page, '/login')
  const small = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('input, textarea, select'))
      .map((el) => ({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        size: parseFloat(getComputedStyle(el).fontSize),
      }))
      .filter((f) => f.size < 16),
  )
  expect(small, 'fields under 16px auto-zoom on iOS').toEqual([])
})

test('a long pasted prompt does not widen the page', async ({ page }) => {
  // Was signing in and then looking for the login form's email field, which
  // no longer exists once signed in -- it redirects. The composer is the
  // field the report was about anyway.
  await signIn(page, '/chat/s1')
  const field = page.locator('textarea').first()
  await field.fill(LONG_PROMPT)
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth + 1)
})


test('a long pasted prompt stays reviewable', async ({ page }) => {
  /*
   * Fix 1. The old composer capped at a fixed 160px with no `overflow`, so a
   * pasted prompt scrolled under its own top edge: unreachable, unreadable,
   * and still sendable. The cap is now a share of the viewport, the field
   * scrolls past it, and an expand button opens a full-screen editor.
   */
  await signIn(page, '/chat/s1')
  const field = page.locator('textarea').first()
  await field.fill(LONG_PROMPT)

  const box = await field.boundingBox()
  const vh = page.viewportSize()!.height
  expect(box!.height, 'composer should grow past four lines').toBeGreaterThan(120)
  expect(box!.height, 'but not eat the conversation').toBeLessThanOrEqual(vh * 0.45)

  // Capped means scrollable, or the text above the fold is unreachable.
  const overflow = await field.evaluate((el) => getComputedStyle(el).overflowY)
  expect(overflow, 'a capped field must scroll').toBe('auto')

  // And the whole thing is reviewable full-screen before sending.
  await page.getByLabel('Expand to full screen').click()
  const expanded = page.getByRole('dialog', { name: 'Edit message' })
  await expect(expanded).toBeVisible()
  await expect(expanded.locator('textarea')).toHaveValue(LONG_PROMPT)
})

test('the full-screen editor covers the bottom nav', async ({ page }) => {
  // Both were z-50, so the nav painted over the editor it was supposed to
  // replace -- six tabs floating on top of the text you were reviewing.
  await signIn(page, '/chat/s1')
  await page.locator('textarea').first().fill(LONG_PROMPT)
  await page.getByLabel('Expand to full screen').click()
  const dialog = page.getByRole('dialog', { name: 'Edit message' })
  await expect(dialog).toBeVisible()
  const navZ = await page.locator('nav').first().evaluate(
    (el) => parseInt(getComputedStyle(el).zIndex || '0', 10))
  const dialogZ = await dialog.evaluate(
    (el) => parseInt(getComputedStyle(el).zIndex || '0', 10))
  expect(dialogZ).toBeGreaterThan(navZ)
})
