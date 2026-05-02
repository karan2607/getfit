import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type WorkoutPlan, type WorkoutPlanDetail, type WorkoutPlanPreview, type WorkoutSessionDetail, type SetLog, type ExerciseHistoryPoint } from '../lib/api'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'
import { useAuth } from '../hooks/useAuth'
import { SkeletonCard, SkeletonText } from '../components/Skeleton'

// ── Plan List ──────────────────────────────────────────────────────────────

function PlanList() {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [plans, setPlans] = useState<WorkoutPlan[]>([])
  const [loading, setLoading] = useState(true)
  const [showGenerate, setShowGenerate] = useState(false)

  useEffect(() => {
    api.workouts.listPlans()
      .then(setPlans)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function handleDelete(id: string) {
    try {
      await api.workouts.deletePlan(id)
      setPlans((p) => p.filter((x) => x.id !== id))
      showToast('Plan deleted')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleActivate(id: string) {
    try {
      const updated = await api.workouts.activatePlan(id)
      setPlans((prev) => prev.map((p) => ({ ...p, is_active: p.id === updated.id })))
      showToast('Plan activated')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  if (showGenerate) {
    return <GeneratePlanFlow onBack={() => setShowGenerate(false)} onSaved={(p) => { setPlans((prev) => [p, ...prev]); setShowGenerate(false) }} />
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Workout Plans</h1>
          <p className="text-sm text-gray-500 mt-0.5">AI-generated plans tailored to your goals</p>
        </div>
        <button
          onClick={() => setShowGenerate(true)}
          className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-4 py-2 rounded-xl transition-colors"
        >
          + Generate plan
        </button>
      </div>

      {loading ? (
        <div className="space-y-3">{[1, 2].map((i) => <SkeletonCard key={i} />)}</div>
      ) : plans.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-4xl mb-3">🏋️</div>
          <p className="text-gray-500 text-sm mb-4">No plans yet. Generate your first one!</p>
          <button onClick={() => setShowGenerate(true)} className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-5 py-2.5 rounded-xl transition-colors">
            Generate plan
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`bg-white rounded-2xl border-2 p-5 cursor-pointer transition-colors ${plan.is_active ? 'border-emerald-400' : 'border-gray-100 hover:border-gray-200'}`}
              onClick={() => navigate(`/workouts/${plan.id}`)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {plan.is_active && (
                      <span className="text-xs font-semibold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">Active</span>
                    )}
                    <h3 className="font-semibold text-gray-900 truncate">{plan.title}</h3>
                  </div>
                  {plan.description && <p className="text-sm text-gray-500 line-clamp-2">{plan.description}</p>}
                  {plan.duration_weeks && (
                    <p className="text-xs text-gray-400 mt-1">{plan.duration_weeks} weeks</p>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {!plan.is_active && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleActivate(plan.id) }}
                      className="text-xs text-emerald-600 hover:text-emerald-700 font-medium px-2 py-1 rounded-lg hover:bg-emerald-50 transition-colors"
                    >
                      Set active
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(plan.id) }}
                    className="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded-lg hover:bg-red-50 transition-colors"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Generate Plan Flow ─────────────────────────────────────────────────────

const GOAL_OPTIONS = [
  { value: 'lose_fat', label: 'Lose Fat', icon: '🔥' },
  { value: 'build_muscle', label: 'Build Muscle', icon: '💪' },
  { value: 'maintain', label: 'Maintain', icon: '⚖️' },
]
const LEVEL_OPTIONS = [
  { value: 'beginner', label: 'Beginner' },
  { value: 'intermediate', label: 'Intermediate' },
  { value: 'advanced', label: 'Advanced' },
]
const EQUIPMENT_OPTIONS = [
  { value: 'bodyweight', label: 'Bodyweight only' },
  { value: 'home gym', label: 'Home gym' },
  { value: 'full gym', label: 'Full gym' },
]

function GeneratePlanFlow({ onBack, onSaved }: { onBack: () => void; onSaved: (p: WorkoutPlan) => void }) {
  const { user } = useAuth()
  const { showToast } = useToast()
  const navigate = useNavigate()
  const [step, setStep] = useState<'form' | 'preview'>('form')
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<WorkoutPlanPreview | null>(null)
  const [daysPerWeek, setDaysPerWeek] = useState(4)
  const [durationWeeks, setDurationWeeks] = useState(8)
  const [fitnessGoal, setFitnessGoal] = useState<string>(user?.profile?.fitness_goal ?? 'build_muscle')
  const [experienceLevel, setExperienceLevel] = useState<string>(user?.profile?.experience_level ?? 'intermediate')
  const [equipment, setEquipment] = useState('full gym')
  const [notes, setNotes] = useState('')

  async function handleGenerate() {
    setGenerating(true)
    try {
      const data = await api.workouts.generatePlan({
        days_per_week: daysPerWeek,
        duration_weeks: durationWeeks,
        fitness_goal: fitnessGoal,
        experience_level: experienceLevel,
        equipment,
        notes,
      })
      setPreview(data)
      setStep('preview')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setGenerating(false)
    }
  }

  async function handleSave() {
    if (!preview) return
    setSaving(true)
    try {
      const saved = await api.workouts.savePlan(preview)
      showToast('Plan saved!')
      onSaved(saved)
      navigate(`/workouts/${saved.id}`)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <button onClick={onBack} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6 transition-colors">
        ← Back to plans
      </button>

      {step === 'form' && (
        <>
          <h1 className="text-2xl font-bold text-gray-900 mb-1">Generate workout plan</h1>
          <p className="text-sm text-gray-500 mb-6">AI will create a plan based on your profile and preferences below.</p>

          <div className="bg-white rounded-2xl border border-gray-100 p-6 space-y-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">Goal</label>
              <div className="flex gap-2">
                {GOAL_OPTIONS.map((g) => (
                  <button key={g.value} onClick={() => setFitnessGoal(g.value)}
                    className={`flex-1 py-2.5 rounded-xl text-sm font-semibold transition-colors flex flex-col items-center gap-1 ${fitnessGoal === g.value ? 'bg-emerald-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    <span>{g.icon}</span><span>{g.label}</span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">Experience level</label>
              <div className="flex gap-2">
                {LEVEL_OPTIONS.map((l) => (
                  <button key={l.value} onClick={() => setExperienceLevel(l.value)}
                    className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${experienceLevel === l.value ? 'bg-emerald-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    {l.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">Days per week</label>
              <div className="flex gap-2">
                {[2, 3, 4, 5, 6].map((d) => (
                  <button key={d} onClick={() => setDaysPerWeek(d)}
                    className={`w-10 h-10 rounded-xl text-sm font-semibold transition-colors ${daysPerWeek === d ? 'bg-emerald-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    {d}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">Duration</label>
              <div className="flex gap-2">
                {[4, 6, 8, 12].map((w) => (
                  <button key={w} onClick={() => setDurationWeeks(w)}
                    className={`px-3 h-10 rounded-xl text-sm font-semibold transition-colors ${durationWeeks === w ? 'bg-emerald-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    {w}w
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">Equipment</label>
              <div className="flex gap-2">
                {EQUIPMENT_OPTIONS.map((e) => (
                  <button key={e.value} onClick={() => setEquipment(e.value)}
                    className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${equipment === e.value ? 'bg-emerald-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    {e.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Notes <span className="text-gray-400 font-normal">(injuries, preferences, specific goals)</span>
              </label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="e.g. bad lower back, want to focus on upper body, no squats..."
                className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </div>
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors"
            >
              {generating ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Generating plan...
                </span>
              ) : 'Generate my plan'}
            </button>
          </div>
        </>
      )}

      {step === 'preview' && preview && (
        <>
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{preview.title}</h1>
              {preview.description && <p className="text-sm text-gray-500 mt-1">{preview.description}</p>}
            </div>
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="text-sm text-emerald-600 hover:text-emerald-700 font-medium px-3 py-1.5 rounded-lg hover:bg-emerald-50 transition-colors disabled:opacity-50"
            >
              {generating ? 'Regenerating...' : '↺ Regenerate'}
            </button>
          </div>

          <div className="space-y-3 mb-6">
            {preview.days.map((day) => (
              <div key={day.day_number} className="bg-white rounded-2xl border border-gray-100 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-6 h-6 rounded-full bg-emerald-100 text-emerald-700 text-xs font-bold flex items-center justify-center">
                    {day.day_number}
                  </span>
                  <div>
                    <p className="font-semibold text-gray-900 text-sm">{day.name}</p>
                    {day.focus && <p className="text-xs text-gray-500">{day.focus}</p>}
                  </div>
                  {day.is_rest_day && <span className="ml-auto text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">Rest</span>}
                </div>
                {!day.is_rest_day && (
                  <div className="pl-8 space-y-1">
                    {day.exercises.map((ex, i) => (
                      <div key={i} className="flex items-center justify-between text-xs text-gray-600">
                        <span>{ex.name}</span>
                        <span className="text-gray-400">{ex.sets} × {ex.reps}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors"
          >
            {saving ? 'Saving...' : 'Save this plan'}
          </button>
        </>
      )}
    </div>
  )
}

// ── Plan Detail ────────────────────────────────────────────────────────────

function PlanDetail({ planId }: { planId: string }) {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [plan, setPlan] = useState<WorkoutPlanDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [startingDay, setStartingDay] = useState<string | null>(null)

  useEffect(() => {
    api.workouts.getPlan(planId)
      .then(setPlan)
      .catch(() => showToast('Failed to load plan', 'error'))
      .finally(() => setLoading(false))
  }, [planId])

  async function handleActivate() {
    if (!plan) return
    try {
      const updated = await api.workouts.activatePlan(plan.id)
      setPlan((p) => p ? { ...p, is_active: updated.is_active } : p)
      showToast('Plan activated')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleStartSession(dayId: string) {
    setStartingDay(dayId)
    try {
      const session = await api.workouts.startSession(dayId)
      navigate(`/workouts/session/${session.id}`)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setStartingDay(null)
    }
  }

  if (loading) return <div className="p-6"><SkeletonText lines={8} /></div>
  if (!plan) return null

  return (
    <div className="p-6 max-w-2xl">
      <button onClick={() => navigate('/workouts')} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
        ← All plans
      </button>

      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            {plan.is_active && <span className="text-xs font-semibold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">Active</span>}
            <h1 className="text-2xl font-bold text-gray-900">{plan.title}</h1>
          </div>
          {plan.description && <p className="text-sm text-gray-500">{plan.description}</p>}
          {plan.duration_weeks && <p className="text-xs text-gray-400 mt-1">{plan.duration_weeks} weeks · {plan.days.length} days</p>}
        </div>
        {!plan.is_active && (
          <button onClick={handleActivate} className="flex-shrink-0 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-3 py-2 rounded-xl transition-colors">
            Set active
          </button>
        )}
      </div>

      <div className="space-y-3">
        {plan.days.map((day) => (
          <div key={day.id} className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
            <div className="flex items-center justify-between p-4">
              <div className="flex items-center gap-3">
                <span className="w-7 h-7 rounded-full bg-emerald-100 text-emerald-700 text-xs font-bold flex items-center justify-center flex-shrink-0">
                  {day.day_number}
                </span>
                <div>
                  <p className="font-semibold text-gray-900 text-sm">{day.name}</p>
                  {day.focus && <p className="text-xs text-gray-500">{day.focus}</p>}
                </div>
              </div>
              {day.is_rest_day ? (
                <span className="text-xs text-gray-400 bg-gray-100 px-2.5 py-1 rounded-full">Rest day</span>
              ) : (
                <button
                  onClick={() => handleStartSession(day.id)}
                  disabled={startingDay === day.id}
                  className="text-sm bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
                >
                  {startingDay === day.id ? '...' : 'Start'}
                </button>
              )}
            </div>
            {!day.is_rest_day && day.exercises.length > 0 && (
              <div className="px-4 pb-4 pt-0 border-t border-gray-50">
                <div className="space-y-1.5 pt-3">
                  {day.exercises.map((ex) => (
                    <div key={ex.id} className="flex items-center justify-between text-sm">
                      <span className="text-gray-700">{ex.name}</span>
                      <span className="text-gray-400 text-xs">{ex.sets} sets × {ex.reps}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Active Session ─────────────────────────────────────────────────────────

function ActiveSession({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [session, setSession] = useState<WorkoutSessionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [completing, setCompleting] = useState(false)

  useEffect(() => {
    api.workouts.getSession(sessionId)
      .then(setSession)
      .catch(() => showToast('Failed to load session', 'error'))
      .finally(() => setLoading(false))
  }, [sessionId])

  async function handleLogSet(log: SetLog, field: 'weight_kg' | 'reps_completed', value: string) {
    if (!session) return
    const numVal = value === '' ? undefined : Number(value)
    try {
      const updated = await api.workouts.logSet(session.id, {
        exercise_id: log.exercise_id ?? '',
        set_number: log.set_number,
        [field]: numVal,
      })
      setSession((s) => s ? {
        ...s,
        set_logs: s.set_logs.map((l) => l.id === updated.id ? updated : l),
      } : s)
    } catch {
      // silently fail — user can retry
    }
  }

  async function handleToggleSet(log: SetLog) {
    if (!session) return
    try {
      const updated = await api.workouts.logSet(session.id, {
        exercise_id: log.exercise_id ?? '',
        set_number: log.set_number,
        is_completed: !log.is_completed,
      })
      setSession((s) => s ? {
        ...s,
        set_logs: s.set_logs.map((l) => l.id === updated.id ? updated : l),
      } : s)
    } catch {
      // silently fail
    }
  }

  async function handleComplete() {
    if (!session) return
    setCompleting(true)
    try {
      await api.workouts.completeSession(session.id)
      showToast('Workout complete! Great work 💪')
      navigate('/workouts')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setCompleting(false)
    }
  }

  if (loading) return <div className="p-6"><SkeletonText lines={10} /></div>
  if (!session) return null

  // Group set_logs by exercise name
  const byExercise = session.set_logs.reduce<Record<string, SetLog[]>>((acc, log) => {
    if (!acc[log.exercise_name]) acc[log.exercise_name] = []
    acc[log.exercise_name].push(log)
    return acc
  }, {})

  const completedSets = session.set_logs.filter((l) => l.is_completed).length
  const totalSets = session.set_logs.length
  const progress = totalSets > 0 ? Math.round((completedSets / totalSets) * 100) : 0

  return (
    <div className="p-6 max-w-lg">
      <button onClick={() => navigate(-1)} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
        ← Back
      </button>

      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 mb-1">
          {session.exercise_day?.name ?? 'Workout'}
        </h1>
        {session.exercise_day?.focus && (
          <p className="text-sm text-gray-500">{session.exercise_day.focus}</p>
        )}

        {/* Progress bar */}
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>{completedSets} / {totalSets} sets</span>
            <span>{progress}%</span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      </div>

      <div className="space-y-5 mb-8">
        {Object.entries(byExercise).map(([exerciseName, logs]) => (
          <div key={exerciseName} className="bg-white rounded-2xl border border-gray-100 p-4">
            <h3 className="font-semibold text-gray-900 mb-3">{exerciseName}</h3>
            <div className="space-y-2">
              {/* Header */}
              <div className="grid grid-cols-[1.5rem_1fr_1fr_2rem] gap-2 text-xs text-gray-400 px-1">
                <span>Set</span>
                <span>Weight (kg)</span>
                <span>Reps</span>
                <span />
              </div>
              {logs.map((log) => (
                <div key={log.id} className={`grid grid-cols-[1.5rem_1fr_1fr_2rem] gap-2 items-center rounded-lg px-1 py-1 transition-colors ${log.is_completed ? 'bg-emerald-50' : ''}`}>
                  <span className="text-xs text-gray-500 font-medium">{log.set_number}</span>
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    placeholder="—"
                    defaultValue={log.weight_kg ?? ''}
                    onBlur={(e) => handleLogSet(log, 'weight_kg', e.target.value)}
                    className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-center focus:outline-none focus:ring-1 focus:ring-emerald-400 w-full"
                  />
                  <input
                    type="number"
                    min={0}
                    placeholder="—"
                    defaultValue={log.reps_completed ?? ''}
                    onBlur={(e) => handleLogSet(log, 'reps_completed', e.target.value)}
                    className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-center focus:outline-none focus:ring-1 focus:ring-emerald-400 w-full"
                  />
                  <button
                    onClick={() => handleToggleSet(log)}
                    className={`w-7 h-7 rounded-full border-2 flex items-center justify-center transition-colors ${
                      log.is_completed
                        ? 'bg-emerald-500 border-emerald-500 text-white'
                        : 'border-gray-300 text-transparent hover:border-emerald-400'
                    }`}
                  >
                    ✓
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={handleComplete}
        disabled={completing}
        className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold rounded-xl py-3 transition-colors"
      >
        {completing ? 'Finishing...' : 'Finish workout'}
      </button>
    </div>
  )
}

// ── Progress Chart ─────────────────────────────────────────────────────────

function LineChart({ points }: { points: { date: string; weight: number }[] }) {
  if (points.length < 2) return (
    <p className="text-sm text-gray-400 text-center py-8">Log at least 2 sessions to see a trend.</p>
  )

  const W = 480, H = 180, PAD = { top: 16, right: 16, bottom: 32, left: 40 }
  const weights = points.map((p) => p.weight)
  const minW = Math.min(...weights)
  const maxW = Math.max(...weights)
  const range = maxW - minW || 1

  const x = (i: number) => PAD.left + (i / (points.length - 1)) * (W - PAD.left - PAD.right)
  const y = (w: number) => PAD.top + (1 - (w - minW) / range) * (H - PAD.top - PAD.bottom)

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(p.weight)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* Grid lines */}
      {[0, 0.5, 1].map((t) => {
        const yPos = PAD.top + t * (H - PAD.top - PAD.bottom)
        const val = maxW - t * range
        return (
          <g key={t}>
            <line x1={PAD.left} y1={yPos} x2={W - PAD.right} y2={yPos} stroke="#f3f4f6" strokeWidth="1" />
            <text x={PAD.left - 4} y={yPos + 4} textAnchor="end" fontSize="10" fill="#9ca3af">{val.toFixed(1)}</text>
          </g>
        )
      })}
      {/* Line */}
      <path d={pathD} fill="none" stroke="#10b981" strokeWidth="2.5" strokeLinejoin="round" />
      {/* Dots + labels */}
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(p.weight)} r="4" fill="#10b981" />
          <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="9" fill="#9ca3af">
            {new Date(p.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
          </text>
        </g>
      ))}
    </svg>
  )
}

function ProgressTab() {
  const [exerciseName, setExerciseName] = useState('')
  const [search, setSearch] = useState('')
  const [history, setHistory] = useState<ExerciseHistoryPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [recentExercises, setRecentExercises] = useState<string[]>([])

  useEffect(() => {
    api.workouts.listSessions()
      .then(async (sessions) => {
        const completed = sessions.filter((s) => s.is_completed).slice(0, 5)
        const names = new Set<string>()
        for (const s of completed) {
          const detail = await api.workouts.getSession(s.id).catch(() => null)
          detail?.set_logs.forEach((l) => l.exercise_name && names.add(l.exercise_name))
        }
        setRecentExercises(Array.from(names).slice(0, 10))
      })
      .catch(() => {})
  }, [])

  async function loadExercise(name: string) {
    setExerciseName(name)
    setLoading(true)
    try {
      const data = await api.workouts.getExerciseHistory(name)
      setHistory(data)
    } catch {
      setHistory([])
    } finally {
      setLoading(false)
    }
  }

  // Group history: max weight per session date
  const chartPoints = (() => {
    const byDate = new Map<string, number>()
    for (const p of history) {
      const date = p.workout_session__started_at.split('T')[0]
      const existing = byDate.get(date) ?? 0
      if (p.weight_kg > existing) byDate.set(date, p.weight_kg)
    }
    return Array.from(byDate.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, weight]) => ({ date, weight }))
  })()

  const filtered = recentExercises.filter((n) => n.toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Progress</h1>
      <p className="text-sm text-gray-500 mb-6">Track strength gains per exercise over time</p>

      <div className="flex gap-3 mb-4">
        <input
          placeholder="Search exercise..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search.trim() && loadExercise(search.trim())}
          className="flex-1 border border-gray-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
        />
        <button
          onClick={() => search.trim() && loadExercise(search.trim())}
          className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-4 rounded-xl transition-colors"
        >
          View
        </button>
      </div>

      {filtered.length > 0 && !exerciseName && (
        <div className="flex flex-wrap gap-2 mb-5">
          {filtered.map((name) => (
            <button
              key={name}
              onClick={() => loadExercise(name)}
              className="text-xs bg-gray-100 hover:bg-emerald-100 hover:text-emerald-700 text-gray-600 px-3 py-1.5 rounded-full transition-colors"
            >
              {name}
            </button>
          ))}
        </div>
      )}

      {exerciseName && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <h2 className="text-base font-semibold text-gray-900">{exerciseName}</h2>
            <button onClick={() => { setExerciseName(''); setHistory([]) }} className="text-xs text-gray-400 hover:text-gray-600">✕ clear</button>
          </div>

          {loading ? (
            <div className="text-sm text-gray-400">Loading...</div>
          ) : history.length === 0 ? (
            <p className="text-sm text-gray-400">No weight data logged for this exercise yet.</p>
          ) : (
            <>
              <div className="bg-white rounded-2xl border border-gray-100 p-4 mb-4">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Max weight per session (kg)</p>
                <LineChart points={chartPoints} />
              </div>
              <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400">Date</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-gray-400">Set</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-gray-400">Weight</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-gray-400">Reps</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.slice(0, 20).map((p, i) => (
                      <tr key={i} className="border-b border-gray-50 last:border-0">
                        <td className="px-4 py-2.5 text-gray-600">
                          {new Date(p.workout_session__started_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                        </td>
                        <td className="px-4 py-2.5 text-right text-gray-500">{p.set_number}</td>
                        <td className="px-4 py-2.5 text-right font-semibold text-gray-900">{p.weight_kg}kg</td>
                        <td className="px-4 py-2.5 text-right text-gray-500">{p.reps_completed ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Router ─────────────────────────────────────────────────────────────────

export default function Workouts() {
  const { planId, sessionId } = useParams<{ planId?: string; sessionId?: string }>()

  if (sessionId) return <ActiveSession sessionId={sessionId} />
  if (planId) return <PlanDetail planId={planId} />
  return <WorkoutsHome />
}

function WorkoutsHome() {
  const [tab, setTab] = useState<'plans' | 'progress'>('plans')

  return (
    <div>
      <div className="border-b border-gray-100 px-6 pt-6 pb-0">
        <div className="flex gap-6">
          {(['plans', 'progress'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors capitalize ${
                tab === t ? 'border-emerald-600 text-emerald-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      {tab === 'plans' ? <PlanList /> : <ProgressTab />}
    </div>
  )
}
