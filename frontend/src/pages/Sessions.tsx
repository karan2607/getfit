import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type WorkoutSessionSummary } from '../lib/api'
import PageHeader from '../components/PageHeader'
import { SkeletonText } from '../components/Skeleton'

function weekLabel(date: Date): string {
  const now = new Date()
  const startOfThisWeek = new Date(now)
  startOfThisWeek.setDate(now.getDate() - now.getDay())
  startOfThisWeek.setHours(0, 0, 0, 0)

  const startOfLastWeek = new Date(startOfThisWeek)
  startOfLastWeek.setDate(startOfThisWeek.getDate() - 7)

  if (date >= startOfThisWeek) return 'This week'
  if (date >= startOfLastWeek) return 'Last week'

  const startOfSessionWeek = new Date(date)
  startOfSessionWeek.setDate(date.getDate() - date.getDay())
  return `Week of ${startOfSessionWeek.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
}

function formatDuration(started: string, completed: string | null): string {
  if (!completed) return 'In progress'
  const mins = Math.round((new Date(completed).getTime() - new Date(started).getTime()) / 60_000)
  if (mins < 60) return `${mins}m`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

export default function Sessions() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<WorkoutSessionSummary[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.workouts.listSessions()
      .then(setSessions)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  // Group sessions by week label
  const grouped: { label: string; sessions: WorkoutSessionSummary[] }[] = []
  for (const s of sessions) {
    const label = weekLabel(new Date(s.started_at))
    const last = grouped[grouped.length - 1]
    if (last && last.label === label) {
      last.sessions.push(s)
    } else {
      grouped.push({ label, sessions: [s] })
    }
  }

  return (
    <div>
      <PageHeader title="Session History" subtitle="All your past workouts" />
      <div className="p-4 md:p-6 max-w-2xl">
        {loading ? (
          <SkeletonText lines={10} />
        ) : sessions.length === 0 ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-3">🏋️</div>
            <p className="font-semibold text-gray-900 mb-1">No sessions yet</p>
            <p className="text-sm text-gray-500">Complete a workout to see your history here.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {grouped.map(({ label, sessions: group }) => {
              const completed = group.filter((s) => s.is_completed).length
              return (
                <div key={label}>
                  <div className="flex items-center justify-between mb-2">
                    <h2 className="text-sm font-bold text-gray-700">{label}</h2>
                    <span className="text-xs text-gray-400">{completed} completed</span>
                  </div>
                  <div className="space-y-2">
                    {group.map((s) => (
                      <div
                        key={s.id}
                        onClick={() => navigate(`/workouts/session/${s.id}`)}
                        className="bg-white rounded-xl border border-gray-100 px-4 py-3 flex items-center justify-between cursor-pointer hover:border-brand-200 hover:shadow-sm transition-all"
                      >
                        <div>
                          <p className="text-sm font-semibold text-gray-900">{s.day_name || 'Workout'}</p>
                          <p className="text-xs text-gray-400 mt-0.5">
                            {new Date(s.started_at).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
                            {' · '}
                            {formatDuration(s.started_at, s.completed_at)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                            s.is_completed ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'
                          }`}>
                            {s.is_completed ? 'Done' : 'In progress'}
                          </span>
                          <span className="text-gray-300">›</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
