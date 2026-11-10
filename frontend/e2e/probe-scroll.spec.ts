import { expect, test } from '@playwright/test'
import { signIn } from './signed-in'

/**
 * The bell's list must actually be a scroll container on iPhone.
 *
 * Removing the whole-row tap target was not enough: the list's height was
 * only ever inherited from the panel's `max-height` through two nested flex
 * boxes. WebKit does not treat that as a definite height, so the list grew to
 * full length and the panel's overflow-hidden clipped it. Nothing scrolled,
 * and the only thing a thumb could do was press the row it was on.
 *
 * The existing "scrolling does not open a thread" test passed throughout,
 * because it only checked that nothing opened -- never that anything moved.
 */

const ROW = {
  id: '22222222-2222-2222-2222-222222222220',
  body: 'Queued item', page: '/diet', status: 'done',
  result_notes: 'Done.', commit_sha: 'abcdef1234567890', submitted_by: 'admin@test.local',
  created_at: '2026-11-03T09:00:00Z', updated_at: '2026-11-03T10:00:00Z',
  seen_at: null, dismissed_at: null, unread: 1,
  messages: [{ id: 'm1', role: 'agent', body: 'Done.', seen_at: null, created_at: '2026-11-03T10:00:00Z' }],
}

const feedback = Array.from({ length: 12 }, (_, i) => ({
  ...ROW,
  id: ROW.id.slice(0, -1) + i.toString(16),
  body: `Queued item number ${i + 1}`,
}))

test('the notification list scrolls, and its last row can be reached', async ({ page }) => {
  await signIn(page, '/diet', { superuser: true, feedback })
  await page.getByLabel(/^Notifications/).click()
  await expect(page.getByText('Feedback updates')).toBeVisible()

  const state = await page.evaluate(() => {
    const scroller = document.querySelector('ul')!.parentElement!
    const panel = document.querySelector<HTMLElement>('[style*="translateX"]')!.parentElement!
    const before = { sh: scroller.scrollHeight, ch: scroller.clientHeight }
    scroller.scrollTop = scroller.scrollHeight
    const lastRow = scroller.querySelector('li:last-child')!.getBoundingClientRect()
    const p = panel.getBoundingClientRect()
    const s = scroller.getBoundingClientRect()
    return {
      ...before,
      maxHeight: getComputedStyle(scroller).maxHeight,
      scrollTop: scroller.scrollTop,
      scrollerBottom: Math.round(s.bottom),
      lastRowBottom: Math.round(lastRow.bottom),
      panelBottom: Math.round(p.bottom),
    }
  })

  // Capped on the scroller itself, so no engine has to infer it.
  expect(state.maxHeight, 'the list has no height cap of its own').not.toBe('none')
  // A real backlog overflows, and the scroller -- not the panel -- holds it.
  expect(state.sh, 'the list is not overflowing; the test proves nothing').toBeGreaterThan(state.ch)
  expect(state.scrollTop, 'the list did not scroll').toBeGreaterThan(0)
  // Not clipped by the panel: the scroller ends inside it, and so does the
  // last row once scrolled to.
  expect(state.scrollerBottom, 'the list runs past the panel and is clipped')
    .toBeLessThanOrEqual(state.panelBottom + 1)
  expect(state.lastRowBottom, 'the last row is unreachable')
    .toBeLessThanOrEqual(state.panelBottom + 1)

  // And scrolling still opened nothing.
  await expect(page.getByLabel('Reply to the agent')).toBeHidden()
})
