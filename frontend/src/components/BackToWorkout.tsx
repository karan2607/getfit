import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { readActiveWorkout } from '../lib/activeWorkout'

/**
 * One tap back into an unfinished workout from anywhere else in the app.
 *
 * Read from localStorage on every navigation rather than fetched, so it
 * costs nothing on pages that are already waiting on the server. Hidden on
 * the workout's own page, where it would only point at itself.
 *
 * A strip at the top of the page rather than a floating button: a button
 * over the bottom of the screen would sit on top of the chat composer and
 * the diet page's log controls.
 */
export default function BackToWorkout() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  if (!user) return null
  const active = readActiveWorkout(user.id)
  if (!active) return null
  const href = `/workouts/session/${active.sessionId}`
  if (pathname === href) return null

  return (
    <Link
      to={href}
      className="sticky top-0 z-30 flex-shrink-0 flex items-center justify-center gap-2 bg-accent-500 hover:bg-accent-600 text-white text-sm font-semibold px-4 py-2.5 transition-colors"
    >
      <span aria-hidden="true">←</span>
      <span className="truncate">Back to workout{active.dayName ? ` · ${active.dayName}` : ''}</span>
    </Link>
  )
}
