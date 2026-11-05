/**
 * When the plan page should show the "next week" banner.
 *
 * The banner used to appear with one training day still to go, so the next
 * week could be generated early. But generation reads how this week went, and
 * building it before the last workout is logged left that workout out -- the
 * user saw "Week 6 is ready" while week 5 was unfinished. So the prompt waits
 * until every training day is done. Logging that last day normally starts
 * preparation server-side anyway; the banner then reports it as ready, and
 * the manual Prepare button is a fallback for when that did not happen.
 */
export interface PromptDay {
  is_rest_day: boolean
  last_completed_at?: string | null
}

export function shouldPromptNextWeek(opts: {
  days: PromptDay[]
  currentWeek: number
  maxWeek: number
  nextWeekReady: boolean
  isPreparing: boolean
}): boolean {
  const { days, currentWeek, maxWeek, nextWeekReady, isPreparing } = opts
  if (currentWeek >= maxWeek || isPreparing) return false
  if (nextWeekReady) return true
  const training = days.filter((d) => !d.is_rest_day)
  return training.length > 0 && training.every((d) => d.last_completed_at)
}
