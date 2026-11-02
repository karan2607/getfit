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
