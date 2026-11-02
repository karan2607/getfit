import { describe, expect, it } from 'vitest'
import { describeDate, localToday } from './localDate'

/**
 * The diet page defaulted its date picker to `toISOString().split('T')[0]`,
 * which is the UTC date. Late evening east of Greenwich that is TOMORROW, so
 * a meal logged "today" was filed under a day the user had not reached.
 */
describe('localToday', () => {
  it('uses the device calendar day, not UTC', () => {
    // 1 Nov 2026, 23:30 local. In any timezone ahead of UTC the UTC date is
    // still the 1st; in any behind, it has already rolled to the 2nd.
    const late = new Date(2026, 10, 1, 23, 30)
    expect(localToday(late)).toBe('2026-11-01')
    // The bug, stated as the contrast it is.
    const utcWouldSay = late.toISOString().split('T')[0]
    expect(localToday(late)).not.toBe(
      utcWouldSay === '2026-11-01' ? 'never-equal' : utcWouldSay,
    )
  })

  it('pads single-digit months and days', () => {
    expect(localToday(new Date(2026, 0, 5))).toBe('2026-01-05')
  })
})

describe('describeDate', () => {
  const now = new Date(2026, 10, 1, 12, 0)

  it('names today as today', () => {
    expect(describeDate('2026-11-01', now)).toMatch(/^Today, /)
  })

  it('names yesterday as yesterday', () => {
    expect(describeDate('2026-10-31', now)).toMatch(/^Yesterday, /)
  })

  it('shows other days plainly', () => {
    const label = describeDate('2026-10-20', now)
    expect(label).not.toMatch(/Today|Yesterday/)
    expect(label).toMatch(/20/)
  })

  it('returns the input unchanged if it is not a date', () => {
    expect(describeDate('not-a-date', now)).toBe('not-a-date')
  })
})
