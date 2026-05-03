import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { api, type WorkoutPlanDetail, type DietPlanDetail, type BodyScanResult, type WorkoutSessionSummary } from '../lib/api'
import PageHeader from '../components/PageHeader'

function StatCard({ label, value, sub, color = 'emerald' }: {
  label: string; value: string | number; sub?: string; color?: string
}) {
  const colorMap: Record<string, string> = {
    emerald: 'bg-emerald-50 text-emerald-600',
    blue: 'bg-blue-50 text-blue-600',
    amber: 'bg-amber-50 text-amber-600',
    purple: 'bg-purple-50 text-purple-600',
  }
  return (
    <div className={`rounded-2xl p-4 ${colorMap[color] ?? colorMap.emerald}`}>
      <p className="text-xs font-semibold opacity-70 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-2xl font-bold">{value}</p>
      {sub && <p className="text-xs opacity-70 mt-0.5">{sub}</p>}
    </div>
  )
}

function SectionHeader({ title, to, linkLabel }: { title: string; to?: string; linkLabel?: string }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-base font-bold text-gray-800">{title}</h2>
      {to && <Link to={to} className="text-xs text-emerald-600 hover:underline">{linkLabel ?? 'View all'}</Link>}
    </div>
  )
}

export default function Dashboard() {
  const { user } = useAuth()
  const [activePlan, setActivePlan] = useState<WorkoutPlanDetail | null>(null)
  const [activeDiet, setActiveDiet] = useState<DietPlanDetail | null>(null)
  const [recentSessions, setRecentSessions] = useState<WorkoutSessionSummary[]>([])
  const [latestBodyScan, setLatestBodyScan] = useState<BodyScanResult | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.allSettled([
      api.workouts.listPlans(),
      api.workouts.listSessions(),
      api.diet.listPlans(),
      api.body.history(),
    ]).then(async ([plansRes, sessionsRes, dietRes, bodyRes]) => {
      const secondary: Promise<unknown>[] = []

      if (plansRes.status === 'fulfilled') {
        const active = plansRes.value.find((p) => p.is_active)
        if (active) secondary.push(api.workouts.getPlan(active.id).then(setActivePlan).catch(() => {}))
      }
      if (sessionsRes.status === 'fulfilled') {
        setRecentSessions(sessionsRes.value.slice(0, 5))
      }
      if (dietRes.status === 'fulfilled') {
        const active = dietRes.value.find((p) => p.is_active)
        if (active) secondary.push(api.diet.getPlan(active.id).then(setActiveDiet).catch(() => {}))
      }
      if (bodyRes.status === 'fulfilled' && bodyRes.value.length > 0) {
        setLatestBodyScan(bodyRes.value[0])
      }

      await Promise.all(secondary)
      setLoading(false)
    })
  }, [])

  const firstName = user?.name?.split(' ')[0] ?? 'there'
  const completedSessions = recentSessions.filter((s) => s.is_completed).length
  const todayDay = new Date().toLocaleDateString('en-US', { weekday: 'long' })

  const todayWorkout = activePlan?.days.find((d) => {
    if (!activePlan.activated_at) return d.order === 0
    const msPerDay = 86_400_000
    const daysSince = Math.floor((Date.now() - new Date(activePlan.activated_at).getTime()) / msPerDay)
    return d.order === daysSince % activePlan.days.length
  })

  if (loading) {
    return (
      <div className="p-4 md:p-6 max-w-2xl">
        <div className="h-8 w-48 bg-gray-100 rounded-lg animate-pulse mb-6" />
        <div className="grid grid-cols-2 gap-3 mb-6">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-20 bg-gray-100 rounded-2xl animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title={`Hey, ${firstName} 👋`} subtitle={`${todayDay} — let's keep the streak going`} />
      <div className="p-4 md:p-6 max-w-2xl">

      {/* Quick stats */}
      <div className="grid grid-cols-2 gap-3 mb-6">
        <StatCard
          label="Workouts (30d)"
          value={completedSessions}
          sub="sessions completed"
          color="emerald"
        />
        {activeDiet ? (
          <StatCard
            label="Daily Target"
            value={activeDiet.target_calories}
            sub="kcal target"
            color="blue"
          />
        ) : (
          <StatCard label="Diet Plan" value="—" sub="No active plan" color="blue" />
        )}
        {latestBodyScan ? (
          <StatCard
            label="Body Fat"
            value={latestBodyScan.body_fat_pct != null ? `${latestBodyScan.body_fat_pct}%` : '—'}
            sub={latestBodyScan.physique_category}
            color="amber"
          />
        ) : (
          <StatCard label="Body Scan" value="—" sub="No scans yet" color="amber" />
        )}
        <StatCard
          label="Active Plan"
          value={activePlan ? '✓' : '—'}
          sub={activePlan?.title ?? 'No workout plan'}
          color="purple"
        />
      </div>

      {/* Today's workout */}
      <div className="mb-6">
        <SectionHeader title="Today's Workout" to="/workouts" linkLabel="All workouts" />
        {activePlan && todayWorkout ? (
          <div className="bg-white rounded-2xl border border-gray-100 p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <p className="font-semibold text-gray-900">{todayWorkout.name}</p>
                {todayWorkout.focus && <p className="text-xs text-gray-400">{todayWorkout.focus}</p>}
              </div>
              {todayWorkout.is_rest_day ? (
                <span className="text-xs text-gray-400 bg-gray-100 px-3 py-1 rounded-full">Rest day</span>
              ) : (
                <Link
                  to="/workouts"
                  className="text-xs bg-emerald-600 hover:bg-emerald-700 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
                >
                  Start
                </Link>
              )}
            </div>
            {!todayWorkout.is_rest_day && todayWorkout.exercises.length > 0 && (
              <div className="space-y-1">
                {todayWorkout.exercises.slice(0, 3).map((ex) => (
                  <div key={ex.id} className="flex items-center justify-between text-xs text-gray-500">
                    <span>{ex.name}</span>
                    <span className="text-gray-400">{ex.sets} × {ex.reps}</span>
                  </div>
                ))}
                {todayWorkout.exercises.length > 3 && (
                  <p className="text-xs text-gray-400">+{todayWorkout.exercises.length - 3} more</p>
                )}
              </div>
            )}
          </div>
        ) : activePlan ? (
          <div className="bg-white rounded-2xl border border-gray-100 p-4 text-sm text-gray-500">
            No session scheduled for today in your active plan.
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-gray-100 p-4 flex items-center justify-between">
            <p className="text-sm text-gray-500">No active workout plan</p>
            <Link to="/workouts" className="text-xs text-emerald-600 hover:underline font-medium">Generate one →</Link>
          </div>
        )}
      </div>

      {/* Active diet */}
      {activeDiet && (
        <div className="mb-6">
          <SectionHeader title="Today's Nutrition" to="/diet" linkLabel="View plan" />
          <div className="bg-white rounded-2xl border border-gray-100 p-4">
            <div className="flex items-center gap-4 mb-3">
              <div className="text-center">
                <p className="text-xl font-bold text-gray-900">{activeDiet.target_calories}</p>
                <p className="text-xs text-gray-400">kcal</p>
              </div>
              <div className="flex-1 grid grid-cols-3 gap-2 text-center text-xs">
                <div>
                  <p className="font-semibold text-emerald-600">{activeDiet.protein_g}g</p>
                  <p className="text-gray-400">Protein</p>
                </div>
                <div>
                  <p className="font-semibold text-blue-500">{activeDiet.carbs_g}g</p>
                  <p className="text-gray-400">Carbs</p>
                </div>
                <div>
                  <p className="font-semibold text-amber-500">{activeDiet.fat_g}g</p>
                  <p className="text-gray-400">Fat</p>
                </div>
              </div>
            </div>
            <div className="space-y-1">
              {activeDiet.meals.slice(0, 3).map((meal) => (
                <div key={meal.id} className="flex items-center justify-between text-xs text-gray-500">
                  <span className="capitalize">{meal.meal_type} — {meal.name}</span>
                  <span className="text-gray-400">{meal.calories} kcal</span>
                </div>
              ))}
              {activeDiet.meals.length > 3 && (
                <p className="text-xs text-gray-400">+{activeDiet.meals.length - 3} more meals</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Recent sessions */}
      {recentSessions.length > 0 && (
        <div className="mb-6">
          <SectionHeader title="Recent Sessions" to="/workouts" />
          <div className="space-y-2">
            {recentSessions.map((s) => (
              <div key={s.id} className="bg-white rounded-xl border border-gray-100 px-4 py-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-800">{s.day_name || 'Workout'}</p>
                  <p className="text-xs text-gray-400">
                    {new Date(s.started_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  </p>
                </div>
                <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                  s.is_completed ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'
                }`}>
                  {s.is_completed ? 'Done' : 'In progress'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state CTA if everything is blank */}
      {!activePlan && !activeDiet && recentSessions.length === 0 && (
        <div className="bg-gradient-to-br from-emerald-50 to-emerald-100 rounded-2xl p-6 text-center">
          <div className="text-3xl mb-2">🚀</div>
          <p className="font-bold text-gray-900 mb-1">Let's get started</p>
          <p className="text-sm text-gray-600 mb-4">Set up your workout plan and diet to see your progress here.</p>
          <div className="flex gap-3 justify-center flex-wrap">
            <Link to="/workouts" className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-4 py-2 rounded-xl transition-colors">
              Create workout plan
            </Link>
            <Link to="/diet" className="bg-white hover:bg-gray-50 text-gray-700 text-sm font-medium px-4 py-2 rounded-xl border border-gray-200 transition-colors">
              Generate diet plan
            </Link>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
