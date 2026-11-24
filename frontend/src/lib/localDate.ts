/**
 * The user's own calendar day, not UTC's.
 *
 * `new Date().toISOString().split('T')[0]` is the UTC date. For anyone east
 * of Greenwich after their evening, and anyone west of it late at night, that
 * is TOMORROW or YESTERDAY. The diet page defaulted its picker that way, so a
 * meal logged "today" could be filed under a day the user had not reached --
 * the same class of bug the backend already fixed with `user_timezone`.
 */

/** Today in the device's own timezone, as YYYY-MM-DD. */
export function localToday(now: Date = new Date()): string {
  // Built from the local parts rather than converted, so no timezone maths
  // can shift the day.
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * A date label a person can check at a glance.
 *
 * "Today, Sat 1 Nov" tells you both which day is selected and that the app
 * agrees with your phone. A bare ISO string tells you neither.
 */
export function describeDate(iso: string, now: Date = new Date()): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  const date = new Date(y, m - 1, d)
  const pretty = date.toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short',
  })
  if (iso === localToday(now)) return `Today, ${pretty}`

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (iso === localToday(yesterday)) return `Yesterday, ${pretty}`

  return pretty
}

/** A date-only string: 2026-11-23, with no time and no zone. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/**
 * Parse a server date, reading a date-only string as the LOCAL day.
 *
 * `new Date('2026-11-23')` is midnight UTC, which anywhere west of Greenwich
 * is the previous afternoon. The health page rendered every row through it,
 * so a day's steps were labelled a day early and "last synced" read
 * yesterday. The diet page had the same bug in November.
 *
 * A full timestamp is an instant and is left exactly as it is: the health
 * views pass both shapes through one formatter, and appending midnight to an
 * instant would move it.
 */
export function parseLocal(iso: string): Date {
  if (DATE_ONLY.test(iso)) {
    const [y, m, d] = iso.split('-').map(Number)
    return new Date(y, m - 1, d)
  }
  return new Date(iso)
}

/** "Nov 23" for either a date-only string or an instant. */
export function formatDayLabel(iso: string): string {
  return parseLocal(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric',
  })
}

/**
 * Whole days from `iso` to now, counted on the local calendar.
 *
 * Subtracting raw milliseconds and dividing is what made "days since the
 * latest data" read 1 on a day the watch had already synced: midnight UTC is
 * eight hours into yesterday in Pacific, so a same-day record looked a day
 * old. Comparing local midnights removes the offset entirely.
 */
export function daysSince(iso: string, now: Date = new Date()): number {
  const then = parseLocal(iso)
  const thenMidnight = new Date(then.getFullYear(), then.getMonth(), then.getDate())
  const nowMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((nowMidnight.getTime() - thenMidnight.getTime()) / 86_400_000)
}
