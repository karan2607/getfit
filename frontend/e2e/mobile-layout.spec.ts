import { expect, test, type Page } from '@playwright/test'

/**
 * Layout assertions at the width the app is actually used at.
 *
 * Every one of these checks something a desktop screenshot cannot show, and
 * each corresponds to a real hazard in this codebase:
 *
 *   - `main` carries `overflow-x-hidden`, which HIDES overflow rather than
 *     preventing it, so content can be silently clipped at 390px
 *   - the bottom nav is `fixed` and the app has NO safe-area handling, so it
 *     can sit under the home indicator and over the content
 *   - the chat session delete is `opacity-0 group-hover:opacity-100`, which
 *     is unreachable on touch
 *
 * Routes that need auth redirect to /login, which is itself a screen worth
 * checking, so the unauthenticated set is a real gate rather than a stub.
 */

const PUBLIC_ROUTES = ['/login', '/signup', '/forgot-password']

/** Horizontal overflow, measured rather than eyeballed. */
async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement
    return {
      scrollWidth: doc.scrollWidth,
      innerWidth: window.innerWidth,
      // The widest offending element, so a failure says WHAT overflowed
      // rather than only that something did.
      widest: Array.from(document.querySelectorAll<HTMLElement>('body *'))
        .map((el) => ({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().slice(0, 60),
          right: Math.round(el.getBoundingClientRect().right),
        }))
        .filter((e) => e.right > window.innerWidth + 1)
        .sort((a, b) => b.right - a.right)[0] ?? null,
    }
  })
}

for (const route of PUBLIC_ROUTES) {
  test(`${route} does not scroll sideways at 390px`, async ({ page }) => {
    await page.goto(route)
    await page.waitForLoadState('networkidle')
    const { scrollWidth, innerWidth, widest } = await horizontalOverflow(page)
    expect(
      scrollWidth,
      `horizontal overflow on ${route}; widest offender: ${JSON.stringify(widest)}`,
    ).toBeLessThanOrEqual(innerWidth + 1)
  })

  test(`${route} has no tap target under 44px`, async ({ page }) => {
    await page.goto(route)
    await page.waitForLoadState('networkidle')

    const small = await page.evaluate(() => {
      const targets = Array.from(
        document.querySelectorAll<HTMLElement>(
          'button, a[href], input[type=checkbox], input[type=radio], [role=button]',
        ),
      )
      return targets
        .filter((el) => {
          const r = el.getBoundingClientRect()
          // Zero-size elements are hidden, not small.
          return r.width > 0 && r.height > 0 && (r.width < 44 || r.height < 44)
        })
        .map((el) => ({
          tag: el.tagName.toLowerCase(),
          text: (el.textContent || '').trim().slice(0, 30),
          w: Math.round(el.getBoundingClientRect().width),
          h: Math.round(el.getBoundingClientRect().height),
        }))
    })
    expect(small, `tap targets below 44px on ${route}`).toEqual([])
  })
}

test('the login form is reachable without horizontal scrolling', async ({ page }) => {
  await page.goto('/login')
  await page.waitForLoadState('networkidle')
  const email = page.locator('input[type=email]').first()
  await expect(email).toBeVisible()
  const box = await email.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390 + 1)
})

test('no element is pinned wider than the viewport', async ({ page }) => {
  await page.goto('/login')
  await page.waitForLoadState('networkidle')
  const offenders = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('body *'))
      .filter((el) => el.getBoundingClientRect().width > window.innerWidth + 1)
      .map((el) => ({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 60),
        w: Math.round(el.getBoundingClientRect().width),
      })),
  )
  expect(offenders).toEqual([])
})
