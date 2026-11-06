import { expect, test, type Page } from '@playwright/test'
import { signIn } from './signed-in'

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
 * The SIGNED-IN routes were missing entirely until now, and that is the gap
 * that mattered: every page the owner actually uses lives behind auth, and
 * the only phone-width coverage was three unauthenticated forms plus the
 * chat composer. Two layout defects reached a real phone through it.
 */

const PUBLIC_ROUTES = ['/login', '/signup', '/forgot-password']

/**
 * Behind auth, and therefore where the app is really used. The sidebar is
 * desktop-only (`if (isMobile) return null`), so at 390px these render the
 * six-item bottom nav instead.
 */
const SIGNED_IN_ROUTES = [
  '/dashboard', '/workouts', '/diet', '/sessions', '/profile', '/chat',
]

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

/**
 * Tap targets already under 44px when this coverage was added.
 *
 * The signed-in pages had never been measured at phone width, and the first
 * run found 23 across five of them: tab bars at 34px, primary CTAs at 36-40,
 * Profile's segmented toggles at 40, and one 16px text link. All real defects
 * on a phone-first app.
 *
 * They are enumerated rather than fixed here because tap targets were not in
 * the defect list this batch exists to clear, and bumping 23 controls across
 * five pages would mix into exactly the files the later batches touch — which
 * is what severity batching is for. Enumerating them means a NEW one fails
 * immediately, and the list cannot quietly become a blanket exemption: the
 * test also fails if an entry here has since been fixed.
 *
 * This is debt, recorded. It is not a statement that 34px is acceptable.
 */
const KNOWN_SMALL: Record<string, string[]> = {
  // Labels are matched verbatim, leading glyphs included.
  '/dashboard': ['All workouts', 'Create workout plan', 'Generate diet plan'],
  '/workouts': ['+ Generate', 'plans', 'progress', 'Generate plan'],
  '/diet': ['My Plans', 'Daily Log', 'Food Scanner', 'Generate my first plan'],
  '/chat': ['+ New chat'],
  '/profile': [
    'Info', 'Notes', 'Password',          // tab bar, 34px
    'lb', 'kg',                           // unit toggle, 40px
    'Sedentary', 'Lightly Active',        // activity toggle, 40px
    'Beginner', 'Intermediate', 'Advanced', // experience toggle, 40px
    'Sign out',                           // 42px — 2px short
  ],
}

/** The same two measurements, on the pages that live behind auth. */
async function smallTapTargets(page: Page) {
  return page.evaluate(() => {
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
}

for (const route of SIGNED_IN_ROUTES) {
  test(`${route} does not scroll sideways at 390px`, async ({ page }) => {
    await signIn(page, route)

    // The page must actually have rendered, or this measures an empty body
    // and passes for the wrong reason. That is not hypothetical: the old
    // stub returned `{}` for collections, these pages threw on `.map`, and
    // the body came back blank.
    const body = await page.locator('body').innerText()
    expect(body.trim().length, `${route} rendered nothing — check the stub`)
      .toBeGreaterThan(0)

    const { scrollWidth, innerWidth, widest } = await horizontalOverflow(page)
    expect(
      scrollWidth,
      `horizontal overflow on ${route}; widest offender: ${JSON.stringify(widest)}`,
    ).toBeLessThanOrEqual(innerWidth + 1)
  })

  test(`${route} grows no new tap target under 44px`, async ({ page }) => {
    await signIn(page, route)
    const small = await smallTapTargets(page)
    const unknown = small.filter((t) => !KNOWN_SMALL[route]?.includes(t.text))

    expect(
      unknown,
      `NEW tap target below 44px on ${route}. Either make it 44px, or add it `
        + 'to KNOWN_SMALL with a reason — do not add it silently.',
    ).toEqual([])

    // The debt must shrink, never grow. If a label is fixed, drop it from
    // KNOWN_SMALL; this catches a stale entry so the list cannot rot into a
    // blanket exemption.
    const stale = (KNOWN_SMALL[route] ?? []).filter(
      (label) => !small.some((t) => t.text === label),
    )
    expect(
      stale,
      `${route}: these are now 44px+ — remove them from KNOWN_SMALL`,
    ).toEqual([])
  })

  test(`${route} keeps the bottom nav clear of the content`, async ({ page }) => {
    await signIn(page, route)
    const nav = page.locator('nav').filter({ hasText: 'Workouts' }).first()
    await expect(nav).toBeVisible()

    const box = (await nav.boundingBox())!
    const view = page.viewportSize()!

    // Pinned to the bottom edge and fully on screen.
    expect(Math.round(box.y + box.height), `${route}: nav is not at the bottom`)
      .toBeLessThanOrEqual(view.height + 1)
    expect(Math.round(box.x), `${route}: nav starts off the left edge`)
      .toBeGreaterThanOrEqual(0)
    expect(Math.round(box.x + box.width), `${route}: nav runs past the right edge`)
      .toBeLessThanOrEqual(view.width + 1)

    // And the scroll container reserves room for it, rather than letting the
    // nav paint over the last of the content.
    const covered = await page.evaluate((navTop) => {
      const main = document.querySelector('main')
      if (!main) return null
      const style = getComputedStyle(main)
      return {
        paddingBottom: parseFloat(style.paddingBottom),
        mainBottom: Math.round(main.getBoundingClientRect().bottom),
        navTop,
      }
    }, Math.round(box.y))
    expect(covered, `${route}: no <main> to measure`).not.toBeNull()
    expect(
      covered!.paddingBottom,
      `${route}: <main> reserves ${covered!.paddingBottom}px for a ${Math.round(box.height)}px nav`,
    ).toBeGreaterThanOrEqual(Math.round(box.height))
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
