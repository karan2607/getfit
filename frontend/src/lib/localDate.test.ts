import { describe, expect, it } from 'vitest'
import { daysSince, describeDate, formatDayLabel, localToday, parseLocal } from './localDate'

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

describe('parseLocal', () => {
  it('reads a date-only string as local midnight, not UTC midnight', () => {
    // The live bug: `new Date('2026-11-23')` is midnight UTC, which in
    // Pacific is 23 Nov 16:00 the previous day -- so every health row was
    // labelled a day early.
    const d = parseLocal('2026-11-23')
    expect(d.getFullYear()).toBe(2026)
    expect(d.getMonth()).toBe(10)
    expect(d.getDate()).toBe(23)
    expect(d.getHours()).toBe(0)
  })

  it('leaves a full timestamp alone', () => {
    // fmtDate is called with both shapes -- `s.date` is date-only, but
    // `workout.start_time` and `lastSyncAt` are instants. Appending
    // midnight to an instant would move it.
    const iso = '2026-11-23T10:30:00Z'
    expect(parseLocal(iso).getTime()).toBe(new Date(iso).getTime())
  })
})

describe('formatDayLabel', () => {
  it('labels a date-only string on its own day', () => {
    expect(formatDayLabel('2026-11-23')).toBe('Nov 23')
  })

  it('still formats an instant', () => {
    expect(formatDayLabel('2026-11-23T20:00:00Z')).toBe('Nov 23')
  })
})

describe('daysSince', () => {
  it('reads zero on the day of the latest data', () => {
    // The staleness line said "1 day" on a day the watch had just synced,
    // because midnight UTC is yesterday afternoon in Pacific.
    const now = new Date(2026, 10, 23, 9, 0, 0)
    expect(daysSince('2026-11-23', now)).toBe(0)
  })

  it('counts whole local days back', () => {
    const now = new Date(2026, 10, 23, 9, 0, 0)
    expect(daysSince('2026-11-20', now)).toBe(3)
  })
})
