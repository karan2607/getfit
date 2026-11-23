/*
 * The workout the user has open but not finished, kept outside React.
 *
 * Leaving a workout mid-session for, say, Health meant getting back by
 * Workouts -> the plan -> Continue, waiting on a load at each step. With the
 * session written down here, every other page can offer a one-tap way back
 * without asking the server anything.
 *
 * Written when an unfinished session loads, cleared when it is completed or
 * loads as already completed. Keyed to the user, so a shared device never
 * offers one person another's workout.
 */
const KEY = 'getfit.activeWorkout'

export interface ActiveWorkout {
  userId: string
  sessionId: string
  dayName: string
}

export function rememberActiveWorkout(value: ActiveWorkout) {
  try {
    localStorage.setItem(KEY, JSON.stringify(value))
  } catch {
    // Private mode and blocked site data both throw. Losing the shortcut
    // is the old behaviour, not a new failure.
  }
}

export function forgetActiveWorkout(sessionId?: string) {
  try {
    // With an id, only that session is cleared, so finishing an old one
    // does not hide the shortcut to a different one still open.
    if (sessionId && readActiveWorkout()?.sessionId !== sessionId) return
    localStorage.removeItem(KEY)
  } catch {
    // See above.
  }
}

export function readActiveWorkout(userId?: string): ActiveWorkout | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ActiveWorkout
    if (!parsed || typeof parsed.sessionId !== 'string') return null
    if (userId !== undefined && parsed.userId !== userId) return null
    return parsed
  } catch {
    return null
  }
}
