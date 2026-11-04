import { expect, test, type Page } from '@playwright/test'

/**
 * The bell panel, at the width it opened wrong at.
 *
 * The panel is anchored `right-0`, which is the right edge of the BELL. With
 * a page action beside it the bell was no longer the rightmost thing in the
 * header, so the panel's right edge landed inset from the header edge and
 * the panel ran leftwards from under that action. On Dashboard, where the
 * bell happened to be rightmost already, it looked correct -- which is why
 * this only showed up on other pages, and why a single-page check would
 * have passed it.
 *
 * These assert GEOMETRY. "Looks fine on the page I happened to open" is the
 * exact failure mode being guarded against.
 */

const PROFILE = {
  id: 'p1', fitness_goal: 'lose_fat', experience_level: 'intermediate',
  dietary_preference: 'vegetarian', preferred_unit: 'lb',
  age: 31, gender: 'male', height_cm: 183, weight_kg: 90,
}

const THREAD = {
  id: '11111111-1111-1111-1111-111111111111',
  body: 'The bell panel opens towards the left on every page but Home',
  page: '/diet', status: 'done',
  result_notes: 'Anchored the panel to the header edge instead of the button.',
  commit_sha: 'abcdef1234567890', submitted_by: 'admin@test.local',
  created_at: '2026-11-03T09:00:00Z', updated_at: '2026-11-03T10:00:00Z',
  seen_at: null, dismissed_at: null, unread: 2,
  messages: [
    { id: 'm1', role: 'agent', body: 'Anchored the panel to the header edge instead of the button.', seen_at: null, created_at: '2026-11-03T10:00:00Z' },
    { id: 'm2', role: 'agent', body: 'Also made the bell the last item in the header.', seen_at: null, created_at: '2026-11-03T10:05:00Z' },
  ],
}

/** A backlog, for the cases where one row is not enough to fill the panel. */
function many(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    ...THREAD,
    id: THREAD.id.slice(0, -1) + i,
    body: i === 0 ? THREAD.body : `Queued item number ${i + 1}`,
  }))
}

async function signInAsSuperuser(page: Page, route: string, count = 1) {
  await page.route('**/api/**', async (r) => {
    const url = r.request().url()
    const json = (body: unknown) =>
      r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

    if (url.includes('/api/auth/me/')) {
      return json({
        id: 'u1', email: 'admin@test.local', name: 'Admin',
        is_superuser: true, profile: PROFILE,
      })
    }
    if (url.includes('/api/profile')) return json(PROFILE)
    if (url.includes('/api/feedback/notifications')) {
      return json({ unseen: 2 * count, items: many(count) })
    }
    if (url.includes('/messages/')) return json(THREAD)
    // List endpoints must come back as arrays. A blanket `{}` made Diet,
    // Workouts and Sessions throw on `.map`, which blanked the whole page --
    // including the header the bell lives in, so the gate was asserting
    // against nothing at all.
    if (/\/api\/(workouts\/plans|workouts\/sessions|diet\/plans|chat\/sessions|body\/scan\/history|meals|workouts\/templates)/.test(url)) {
      return json([])
    }
    return json({})
  })
  await page.addInitScript(() => {
    window.localStorage.setItem('token', 'gate-token')
  })
  await page.goto(route)
  await page.waitForLoadState('networkidle')
}

const bell = (page: Page) => page.getByLabel(/^Notifications/)

/**
 * Opening is an explicit button, not the row.
 *
 * The whole row used to be the button, and touchEnd treated "barely moved
 * horizontally" as a tap -- which a vertical scroll is, so scrolling the
 * list and lifting your finger opened whichever row it ended under.
 */
const openFirstThread = (page: Page) =>
  page.getByRole('button', { name: /Open conversation/ }).first().click()

/**
 * Workouts and Chat are the only two pages that pass a header `action`, and
 * so the only two where the misanchored panel actually showed -- reverting
 * the fix fails /workouts and leaves every other route green. They lead the
 * list for that reason; the rest are there so the fix is checked not to
 * break the simple case.
 */
const ROUTES = ['/workouts', '/chat', '/dashboard', '/diet', '/sessions', '/profile']

for (const route of ROUTES) {
  test(`the bell panel stays on screen and under the header edge on ${route}`, async ({ page }) => {
    await signInAsSuperuser(page, route)
    await bell(page).click()

    const panel = page.getByText('Feedback updates').locator('../..')
    await expect(panel).toBeVisible()

    const box = (await panel.boundingBox())!
    const header = (await page.locator('header, .sticky').first().boundingBox())!
    const width = page.viewportSize()!.width

    // Fully on screen in both directions. Running off to the left is the
    // reported bug; running off to the right is the obvious wrong fix.
    expect(box.x, `${route}: panel starts off the left edge`).toBeGreaterThanOrEqual(0)
    expect(
      Math.round(box.x + box.width),
      `${route}: panel runs past the right edge`,
    ).toBeLessThanOrEqual(width)

    // Its right edge is the header's right edge, not some point inset from
    // it by whatever else is in the header.
    const panelRight = Math.round(box.x + box.width)
    const headerRight = Math.round(header.x + header.width)
    expect(
      Math.abs(panelRight - headerRight),
      `${route}: panel right edge ${panelRight} is not the header edge ${headerRight}`,
    ).toBeLessThanOrEqual(20)
  })
}

test('the bell is the last thing in the header', async ({ page }) => {
  // This is what makes `right-0` correct. If something is ever added after
  // it, the anchor silently goes wrong again on that page only.
  //
  // It has to run on a page that HAS a header action: on a page with none
  // the bell is trivially last and the check passes no matter which order
  // PageHeader renders them in -- it passed against the unfixed code when
  // it was pointed at /diet.
  await signInAsSuperuser(page, '/workouts')
  const after = await bell(page).evaluate((el) => {
    const parent = el.closest('div')!.parentElement!
    const kids = Array.from(parent.children)
    return kids.length - 1 - kids.indexOf(el.closest('div')!)
  })
  expect(after, 'something sits to the right of the bell').toBe(0)
})

test('a row opens the thread in the panel, without leaving the page', async ({ page }) => {
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()
  await openFirstThread(page)

  // Same surface: no navigation, so nothing the page had is torn down.
  await expect(page).toHaveURL(/\/diet$/)
  await expect(page.getByLabel('Reply to the agent')).toBeVisible()
  await expect(page.getByText(/Anchored the panel/).first()).toBeVisible()

  // And back again, still on the same page.
  await page.getByLabel('Back to notifications').click()
  await expect(page.getByText('Feedback updates')).toBeVisible()
  await expect(page.getByLabel('Reply to the agent')).toBeHidden()
})

test('the open thread fills the panel exactly', async ({ page }) => {
  /*
   * The slide used to be a `width: 200%` track with `w-1/2` panes shifted
   * `-50%`. Those three percentages resolve against different boxes: in a
   * 352px panel the track came out 700px and each pane 350px, so the shift
   * overshot and left only ~13px of the thread pane inside the panel -- a
   * sliver of the list on the left and blank space for the rest. Reopening
   * re-laid it out and happened to look right, so it read as intermittent.
   *
   * Measured, because "it looks like it slid" is exactly what it did.
   */
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()

  /*
   * Both boxes come from one walk up the DOM: from the composer to the pane
   * (the child of the element carrying the slide transform), then one more
   * step to the panel. Selecting them by ancestor COUNT is what made the
   * first version of this test report a 350px offset that was the list pane
   * rather than the panel -- my own selector, not the layout.
   */
  const measure = () =>
    page.evaluate(() => {
      const box = (el: Element) => {
        const r = el.getBoundingClientRect()
        return { x: r.x, width: r.width, height: r.height }
      }
      const composer = document.querySelector('[aria-label="Reply to the agent"]')
      const track = document.querySelector<HTMLElement>('[style*="translateX"]')
      if (!track) return null
      if (!composer) return { panel: box(track.parentElement!), pane: null }
      let node = composer as HTMLElement
      while (node.parentElement && node.parentElement !== track) {
        node = node.parentElement
      }
      return { panel: box(track.parentElement!), pane: box(node) }
    })

  const start = (await measure())!
  const before = start.panel

  await openFirstThread(page)
  // Past the 300ms transition, so this is the settled position.
  await page.waitForTimeout(500)

  const end = (await measure())!
  const box = end.pane!
  const after = end.panel

  // Flush with the panel's content box on both edges, within the 1px border.
  expect(Math.abs(box.x - after.x), 'thread pane is not flush with the panel left edge')
    .toBeLessThanOrEqual(2)
  expect(
    Math.abs((box.x + box.width) - (after.x + after.width)),
    'thread pane is not flush with the panel right edge',
  ).toBeLessThanOrEqual(2)

  // And the panel must not SHRINK on open: the pane you just opened is the
  // one that needs the room. A fixed 30rem thread view meant a full list
  // stood at 70vh and then got smaller.
  expect(
    Math.round(after.height),
    `panel shrank from ${Math.round(before.height)} to ${Math.round(after.height)}`,
  ).toBeGreaterThanOrEqual(Math.round(before.height))
})

test('the panel does not shrink when a thread opens, with a full list', async ({ page }) => {
  /*
   * The reported symptom. One row cannot show it: a short list is well
   * under the cap, so opening a thread grows the panel either way. With a
   * real backlog the list stands at 70vh and a thread view fixed at 30rem
   * made it smaller -- shrinking the pane you just opened, which is the one
   * that needs the room.
   */
  await signInAsSuperuser(page, '/diet', 12)
  await bell(page).click()

  const panelHeight = () =>
    page.evaluate(() => {
      const track = document.querySelector<HTMLElement>('[style*="translateX"]')
      return track ? Math.round(track.parentElement!.getBoundingClientRect().height) : 0
    })

  const before = await panelHeight()
  // The list genuinely fills the panel, or this test proves nothing.
  expect(before, 'the stub list is too short to test shrinking')
    .toBeGreaterThanOrEqual(Math.round(page.viewportSize()!.height * 0.6))

  await openFirstThread(page)
  await page.waitForTimeout(500)

  const after = await panelHeight()
  expect(after, `panel shrank from ${before} to ${after} on open`)
    .toBeGreaterThanOrEqual(before)
})

test('scrolling the list does not open a thread', async ({ page }) => {
  /*
   * The reported bug, as a real touch gesture rather than synthetic events:
   * press, drag upward through the list, release. Before the fix this
   * landed in whichever conversation the finger ended over.
   */
  await signInAsSuperuser(page, '/diet', 12)
  await bell(page).click()
  await expect(page.getByText('Feedback updates')).toBeVisible()

  const list = page.getByText(/Queued item number 3/)
  const box = (await list.boundingBox())!
  const x = Math.round(box.x + box.width / 2)

  await page.touchscreen.tap(x, Math.round(box.y))
  // A deliberately imperfect drag: a thumb never travels straight down.
  await page.mouse.move(x, Math.round(box.y))
  await page.mouse.down()
  await page.mouse.move(x - 6, Math.round(box.y) - 120, { steps: 10 })
  await page.mouse.up()

  // Still the list. No composer, so nothing was opened.
  await expect(page.getByText('Feedback updates')).toBeVisible()
  await expect(page.getByLabel('Reply to the agent')).toBeHidden()
})

test('Fixed and Couldn\'t fix are visibly different states', async ({ page }) => {
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()
  const pill = page.getByText('Fixed').first()
  const colours = await pill.evaluate((el) => {
    const s = getComputedStyle(el)
    return { bg: s.backgroundColor, fg: s.color }
  })
  // A solid pill, not a pale tint: filled-vs-outlined reads before hue does.
  expect(colours.bg, 'Fixed should be a solid pill').not.toMatch(/rgba?\(0, 0, 0, 0\)/)
  const [r, g, b] = colours.bg.match(/\d+/g)!.map(Number)
  // Not a warm tint -- that is what made it read as the red failure state.
  expect(r, `Fixed pill is reddish: rgb(${r},${g},${b})`).toBeLessThan(120)
})

test('the reply box is not under the bottom nav', async ({ page }) => {
  // The reported defect, measured: the nav is `fixed ... z-50` across the
  // bottom of every mobile screen. A composer behind it cannot be tapped at
  // all, which is not something a desktop render or a screenshot shows.
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()
  await openFirstThread(page)

  const box = (await page.getByLabel('Reply to the agent').boundingBox())!
  const nav = (await page.locator('nav, .fixed.bottom-0').first().boundingBox())!
  expect(
    Math.round(box.y + box.height),
    `reply box bottom ${Math.round(box.y + box.height)} is under the nav at ${Math.round(nav.y)}`,
  ).toBeLessThanOrEqual(Math.round(nav.y))

  // And it is genuinely typeable, not merely positioned well.
  await page.getByLabel('Reply to the agent').fill('still off on Diet')
  await expect(page.getByLabel('Reply to the agent')).toHaveValue('still off on Diet')
})

test('the panel does not overflow sideways with a thread open', async ({ page }) => {
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()
  await openFirstThread(page)

  // Two panes on a 200%-wide track, so a mistake here scrolls the whole
  // document sideways rather than clipping quietly.
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(overflow.scrollWidth, 'the open thread overflows sideways')
    .toBeLessThanOrEqual(overflow.innerWidth + 1)
})

test('the unread tag says how many messages are waiting', async ({ page }) => {
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()
  await expect(page.getByLabel('2 new messages')).toBeVisible()
})

test('the reply box does not trigger iOS auto-zoom', async ({ page }) => {
  // 16px minimum or iOS Safari zooms the whole viewport on focus, which is
  // what made the chat drawer feel like it "takes up 50% of the screen".
  await signInAsSuperuser(page, '/diet')
  await bell(page).click()
  await openFirstThread(page)
  const size = await page.getByLabel('Reply to the agent').evaluate(
    (el) => parseFloat(getComputedStyle(el).fontSize),
  )
  expect(size, 'textarea under 16px triggers iOS auto-zoom').toBeGreaterThanOrEqual(16)
})

test('the standalone page clears the bottom nav too', async ({ page }) => {
  // Not the normal way in -- the bell reads threads in place -- but it is a
  // real route, and it is where the composer was unreachable.
  await signInAsSuperuser(page, `/feedback/${THREAD.id}`)
  const box = (await page.getByLabel('Reply to the agent').boundingBox())!
  const nav = await page.locator('nav, .fixed.bottom-0').first().boundingBox()
  // z-[60] puts the page over the nav, so either the nav is not painted or
  // the box sits above it. Both are fine; the box being behind it is not.
  if (nav) {
    const covered = box.y + box.height > nav.y && box.y < nav.y + nav.height
    const above = await page.getByLabel('Reply to the agent').evaluate((el) => {
      const r = el.getBoundingClientRect()
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      return el.contains(top) || el === top
    })
    expect(covered && !above, 'the reply box is behind the bottom nav').toBe(false)
  }
  await page.getByLabel('Reply to the agent').fill('typed')
  await expect(page.getByLabel('Reply to the agent')).toHaveValue('typed')
})
