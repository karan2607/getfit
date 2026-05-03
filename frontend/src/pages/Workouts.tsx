import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type WorkoutPlan, type WorkoutPlanDetail, type WorkoutPlanPreview, type WorkoutSessionDetail, type SetLog, type ExerciseHistoryPoint, type Exercise } from '../lib/api'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'
import { useAuth } from '../hooks/useAuth'
import { SkeletonCard, SkeletonText } from '../components/Skeleton'
import PageHeader from '../components/PageHeader'
import ExerciseDrawer from '../components/ExerciseDrawer'
import WorkoutChatDrawer from '../components/WorkoutChatDrawer'
import ConfirmModal from '../components/ConfirmModal'

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
  const [chatOpen, setChatOpen] = useState(false)
  const [daysPerWeek, setDaysPerWeek] = useState(4)
  const [durationWeeks, setDurationWeeks] = useState(8)
  const [fitnessGoal, setFitnessGoal] = useState<string>(user?.profile?.fitness_goal ?? 'build_muscle')
  const [experienceLevel, setExperienceLevel] = useState<string>(user?.profile?.experience_level ?? 'intermediate')
  const [equipment, setEquipment] = useState('full gym')
  const [notes, setNotes] = useState('')
  const [latestScan, setLatestScan] = useState<{ physique_category: string; body_fat_pct: number | null; muscle_mass_note: string; recommendations: string } | null>(null)
  const [bodyPhoto, setBodyPhoto] = useState<File | null>(null)

  const [useBodyContext, setUseBodyContext] = useState(false)

  useEffect(() => {
    api.body.history().then((results) => {
      if (results.length > 0) {
        setLatestScan(results[0])
        setUseBodyContext(true)
      }
    }).catch(() => {})
  }, [])

  async function handleGenerate() {
    setGenerating(true)
    try {
      const body_context = useBodyContext && latestScan
        ? `Physique: ${latestScan.physique_category}. Body fat: ${latestScan.body_fat_pct}%. ${latestScan.muscle_mass_note} ${latestScan.recommendations}`.trim()
        : undefined
      const data = await api.workouts.generatePlan({
        days_per_week: daysPerWeek,
        duration_weeks: durationWeeks,
        fitness_goal: fitnessGoal,
        experience_level: experienceLevel,
        equipment,
        notes,
        body_context,
        body_photo: bodyPhoto ?? undefined,
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
    <div>
      <PageHeader title="Generate Plan" subtitle="AI will create a plan tailored to your preferences" />
      <div className="p-6 max-w-2xl">
        <button onClick={onBack} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6 transition-colors">
          ← Back to plans
        </button>

        {step === 'form' && (
          <div className="bg-white rounded-2xl border border-gray-100 p-6 space-y-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">Goal</label>
              <div className="flex gap-2">
                {GOAL_OPTIONS.map((g) => (
                  <button key={g.value} onClick={() => setFitnessGoal(g.value)}
                    className={`flex-1 py-2.5 rounded-xl text-sm font-semibold transition-colors flex flex-col items-center gap-1 ${fitnessGoal === g.value ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
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
                    className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${experienceLevel === l.value ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
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
                    className={`w-10 h-10 rounded-xl text-sm font-semibold transition-colors ${daysPerWeek === d ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
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
                    className={`px-3 h-10 rounded-xl text-sm font-semibold transition-colors ${durationWeeks === w ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
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
                    className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${equipment === e.value ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
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
                className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
            </div>
            <div className="border-t border-gray-100 pt-4">
              <label className="block text-sm font-medium text-gray-700 mb-3">Body context <span className="text-gray-400 font-normal">(optional — helps AI personalise weights)</span></label>

              {latestScan ? (
                <div className="flex items-start gap-3">
                  <div className={`flex-1 rounded-xl border p-3 cursor-pointer transition-colors ${useBodyContext ? 'border-brand-400 bg-brand-50' : 'border-gray-200 bg-white'}`} onClick={() => setUseBodyContext(v => !v)}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`w-4 h-4 rounded-full border-2 flex-shrink-0 ${useBodyContext ? 'border-brand-500 bg-brand-500' : 'border-gray-300'}`} />
                      <p className="text-sm font-semibold text-gray-900">Use latest body scan</p>
                    </div>
                    <p className="text-xs text-gray-500 pl-6">{latestScan.physique_category}{latestScan.body_fat_pct ? ` · ${latestScan.body_fat_pct}% body fat` : ''}</p>
                  </div>
                </div>
              ) : null}

              <div className="mt-2">
                <label className="block text-xs text-gray-500 mb-1.5">Or upload a new photo</label>
                <label className="flex items-center gap-2 cursor-pointer text-sm text-brand-500 hover:text-brand-600 font-medium">
                  <input type="file" accept="image/*" className="hidden" onChange={(e) => setBodyPhoto(e.target.files?.[0] ?? null)} />
                  {bodyPhoto ? `📷 ${bodyPhoto.name}` : '+ Upload photo'}
                </label>
                {bodyPhoto && <button onClick={() => setBodyPhoto(null)} className="text-xs text-gray-400 hover:text-red-400 mt-1">Remove</button>}
              </div>
            </div>
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="w-full bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors"
            >
              {generating ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Generating plan...
                </span>
              ) : 'Generate my plan'}
            </button>
          </div>
        )}

        {step === 'preview' && preview && (
          <>
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-xl font-bold text-gray-900">{preview.title}</h2>
                {preview.description && <p className="text-sm text-gray-500 mt-1">{preview.description}</p>}
              </div>
              <button
                onClick={() => setChatOpen(true)}
                className="text-sm text-brand-500 bg-brand-50 hover:bg-brand-100 font-semibold px-3 py-1.5 rounded-lg transition-colors"
              >
                ✨ Regenerate with AI
              </button>
            </div>

            <div className="space-y-3 mb-6">
              {preview.days.map((day) => (
                <div key={day.day_number} className="bg-white rounded-2xl border border-gray-100 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-6 h-6 rounded-full bg-brand-100 text-brand-500 text-xs font-bold flex items-center justify-center">
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
              className="w-full bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors"
            >
              {saving ? 'Saving...' : 'Save this plan'}
            </button>

            <WorkoutChatDrawer
              isOpen={chatOpen}
              onClose={() => setChatOpen(false)}
              planPreview={preview}
              onPreviewUpdate={(updated) => { setPreview(updated); setChatOpen(false) }}
            />
          </>
        )}
      </div>
    </div>
  )
}

// ── Exercise Card (horizontal scroll item) ────────────────────────────────

function ExerciseCard({ exercise, onClick }: { exercise: Exercise; onClick: (e: Exercise) => void }) {
  return (
    <div
      onClick={() => onClick(exercise)}
      className="flex-shrink-0 w-40 h-[120px] bg-white rounded-xl shadow-sm border-l-4 border-brand-500 cursor-pointer transition-all duration-150 hover:scale-105 hover:shadow-md flex flex-col justify-between p-3 overflow-hidden"
    >
      <p className="text-sm font-bold text-gray-900 leading-tight line-clamp-2">{exercise.name}</p>
      <div>
        <span className="text-xs font-semibold bg-brand-100 text-brand-500 px-1.5 py-0.5 rounded-full">
          {exercise.sets} × {exercise.reps}
        </span>
        {exercise.rest_seconds != null && (
          <p className="text-xs text-gray-400 mt-1">⏱ {exercise.rest_seconds}s</p>
        )}
      </div>
    </div>
  )
}

function RestDayCard() {
  return (
    <div className="bg-gray-100 rounded-xl border border-gray-200 py-4 flex items-center justify-center gap-2 text-gray-400">
      <span>😴</span>
      <span className="text-sm font-medium">Rest Day</span>
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
  const [selectedExercise, setSelectedExercise] = useState<Exercise | null>(null)
  const [editDayId, setEditDayId] = useState<string | null>(null)
  const [explicitChatOpen, setExplicitChatOpen] = useState(false)
  const [weeksExpanded, setWeeksExpanded] = useState(false)

  const chatOpen = explicitChatOpen || editDayId !== null

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

  const durationWeeks = plan.duration_weeks ?? 1
  const hasRepeatWeeks = durationWeeks > 1
  const trainingDays = plan.days.filter((d) => !d.is_rest_day).length

  return (
    <>
      <PageHeader
        title={plan.title}
        subtitle={`${durationWeeks} ${durationWeeks === 1 ? 'week' : 'weeks'} · ${trainingDays} training day${trainingDays !== 1 ? 's' : ''}`}
        action={
          !plan.is_active ? (
            <button onClick={handleActivate} className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors">
              Set active
            </button>
          ) : (
            <span className="bg-white/20 text-white text-xs font-semibold px-3 py-1.5 rounded-full">Active</span>
          )
        }
      />

      <div className="p-6">
        <button onClick={() => navigate('/workouts')} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6 transition-colors">
          ← All plans
        </button>

        {plan.description && (
          <p className="text-sm text-gray-500 mb-6">{plan.description}</p>
        )}

        {/* Week 1 */}
        <div className="mb-6">
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-4">
            Week 1{hasRepeatWeeks ? ' (weekly schedule)' : ''}
          </p>
          <div className="space-y-6">
            {plan.days.map((day) => (
              <div key={day.id}>
                {/* Day header */}
                <div className="flex items-center gap-3 mb-3">
                  <span className="w-6 h-6 rounded-full bg-brand-100 text-brand-500 text-xs font-bold flex items-center justify-center flex-shrink-0">
                    {day.day_number}
                  </span>
                  <p className="font-semibold text-gray-900 text-sm">{day.name}</p>
                  {day.focus && (
                    <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">{day.focus}</span>
                  )}
                  {!day.is_rest_day && (
                    <div className="ml-auto flex items-center gap-2">
                      <button
                        onClick={() => setEditDayId(day.id)}
                        className="text-xs text-brand-500 border border-brand-300 px-3 py-1.5 rounded-lg hover:bg-brand-50 transition-colors"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleStartSession(day.id)}
                        disabled={startingDay === day.id}
                        className="text-sm bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
                      >
                        {startingDay === day.id ? '...' : 'Start'}
                      </button>
                    </div>
                  )}
                </div>
                {/* Exercises */}
                {day.is_rest_day ? (
                  <RestDayCard />
                ) : (
                  <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1">
                    {day.exercises.map((ex) => (
                      <ExerciseCard key={ex.id} exercise={ex} onClick={setSelectedExercise} />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Repeat weeks (collapsed) */}
        {hasRepeatWeeks && (
          <div
            onClick={() => setWeeksExpanded((v) => !v)}
            className="mt-2 bg-gray-50 border border-gray-200 rounded-xl p-4 flex items-center justify-between cursor-pointer hover:bg-gray-100 transition-colors select-none"
          >
            <p className="text-sm font-medium text-gray-700">
              Weeks 2–{durationWeeks} — same schedule repeats
            </p>
            <span className="text-gray-400 text-sm">{weeksExpanded ? '▾' : '▸'}</span>
          </div>
        )}

        {weeksExpanded && hasRepeatWeeks && (
          <div className="mt-4 space-y-6 opacity-60">
            {plan.days.map((day) => (
              <div key={day.id}>
                <div className="flex items-center gap-3 mb-3">
                  <span className="w-6 h-6 rounded-full bg-gray-200 text-gray-500 text-xs font-bold flex items-center justify-center flex-shrink-0">
                    {day.day_number}
                  </span>
                  <p className="font-semibold text-gray-700 text-sm">{day.name}</p>
                  {day.focus && (
                    <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">{day.focus}</span>
                  )}
                </div>
                {day.is_rest_day ? (
                  <RestDayCard />
                ) : (
                  <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1">
                    {day.exercises.map((ex) => (
                      <ExerciseCard key={ex.id} exercise={ex} onClick={setSelectedExercise} />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Chat FAB */}
      <button
        onClick={() => setExplicitChatOpen(true)}
        className="fixed bottom-6 right-6 z-30 bg-brand-500 hover:bg-brand-600 text-white rounded-full w-14 h-14 flex items-center justify-center shadow-lg transition-colors text-xl"
        title="Chat with AI Trainer"
      >
        💬
      </button>

      <ExerciseDrawer exercise={selectedExercise} onClose={() => setSelectedExercise(null)} />
      <WorkoutChatDrawer
        isOpen={chatOpen}
        onClose={() => { setExplicitChatOpen(false); setEditDayId(null) }}
        planId={plan?.id}
        dayId={editDayId ?? undefined}
        onPlanUpdated={() => plan && api.workouts.getPlan(plan.id).then(setPlan).catch(() => {})}
      />
    </>
  )
}

// ── Active Session ─────────────────────────────────────────────────────────

function ActiveSession({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const { user } = useAuth()
  const unit = user?.profile?.preferred_unit ?? 'lb'
  const KG_PER_LB = 0.453592
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
    let numVal: number | undefined = value === '' ? undefined : Number(value)
    if (field === 'weight_kg' && numVal !== undefined) {
      numVal = unit === 'lb' ? numVal * KG_PER_LB : numVal
    }
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
      navigate('/dashboard')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setCompleting(false)
    }
  }

  if (loading) return <div className="p-6"><SkeletonText lines={10} /></div>
  if (!session) return null

  const byExercise = session.set_logs.reduce<Record<string, SetLog[]>>((acc, log) => {
    if (!acc[log.exercise_name]) acc[log.exercise_name] = []
    acc[log.exercise_name].push(log)
    return acc
  }, {})

  const completedSets = session.set_logs.filter((l) => l.is_completed).length
  const totalSets = session.set_logs.length
  const progress = totalSets > 0 ? Math.round((completedSets / totalSets) * 100) : 0

  const isCompleted = session.is_completed

  return (
    <>
      <PageHeader
        title={session.exercise_day?.name ?? 'Workout'}
        subtitle={isCompleted
          ? `Completed ${new Date(session.completed_at!).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
          : (session.exercise_day?.focus ?? '')}
        action={!isCompleted ? (
          <button
            onClick={handleComplete}
            disabled={completing}
            className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors disabled:opacity-50"
          >
            {completing ? 'Finishing...' : 'Finish workout'}
          </button>
        ) : (
          <span className="bg-white/20 text-white text-sm font-semibold px-4 py-2 rounded-xl">
            Done ✓
          </span>
        )}
      />
      <div className="p-6 max-w-lg">
        <button onClick={() => navigate(-1)} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
          ← Back
        </button>

        {/* Progress bar */}
        <div className="mb-6">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>{completedSets} / {totalSets} sets</span>
            <span>{progress}%</span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-500 rounded-full transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        <div className="space-y-5 mb-8">
          {Object.entries(byExercise).map(([exerciseName, logs]) => (
            <div key={exerciseName} className="bg-white rounded-2xl border border-gray-100 p-4">
              <h3 className="font-semibold text-gray-900 mb-3">{exerciseName}</h3>
              <div className="space-y-2">
                <div className="grid grid-cols-[1.5rem_1fr_1fr_2rem] gap-2 text-xs text-gray-400 px-1">
                  <span>Set</span>
                  <span>Weight ({unit})</span>
                  <span>Reps</span>
                  <span />
                </div>
                {logs.map((log) => (
                  <div key={log.id} className={`grid grid-cols-[1.5rem_1fr_1fr_2rem] gap-2 items-center rounded-lg px-1 py-1 transition-colors ${log.is_completed ? 'bg-emerald-50' : ''}`}>
                    <span className="text-xs text-gray-500 font-medium">{log.set_number}</span>
                    {isCompleted ? (
                      <span className="text-sm text-center text-gray-700 py-1.5">
                        {log.weight_kg != null ? (unit === 'lb' ? Math.round(log.weight_kg * 2.20462 * 10) / 10 : log.weight_kg) : '—'}
                      </span>
                    ) : (
                      <input
                        type="number" min={0} step={0.5}
                        placeholder={`Weight (${unit})`}
                        defaultValue={log.weight_kg != null ? (unit === 'lb' ? Math.round(log.weight_kg * 2.20462 * 10) / 10 : log.weight_kg) : ''}
                        onBlur={(e) => handleLogSet(log, 'weight_kg', e.target.value)}
                        className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-center focus:outline-none focus:ring-1 focus:ring-brand-400 w-full"
                      />
                    )}
                    {isCompleted ? (
                      <span className="text-sm text-center text-gray-700 py-1.5">
                        {log.reps_completed ?? '—'}
                      </span>
                    ) : (
                      <input
                        type="number" min={0} placeholder="—"
                        defaultValue={log.reps_completed ?? ''}
                        onBlur={(e) => handleLogSet(log, 'reps_completed', e.target.value)}
                        className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-center focus:outline-none focus:ring-1 focus:ring-brand-400 w-full"
                      />
                    )}
                    <div
                      className={`w-7 h-7 rounded-full border-2 flex items-center justify-center ${
                        log.is_completed
                          ? 'bg-emerald-500 border-emerald-500 text-white'
                          : 'border-gray-300 text-transparent'
                      }${!isCompleted ? ' cursor-pointer hover:border-emerald-400' : ''}`}
                      onClick={!isCompleted ? () => handleToggleSet(log) : undefined}
                    >
                      ✓
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
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
      <path d={pathD} fill="none" stroke="#be123c" strokeWidth="2.5" strokeLinejoin="round" />
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(p.weight)} r="4" fill="#be123c" />
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
      <div className="flex gap-3 mb-4">
        <input
          placeholder="Search exercise..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search.trim() && loadExercise(search.trim())}
          className="flex-1 border border-gray-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <button
          onClick={() => search.trim() && loadExercise(search.trim())}
          className="bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium px-4 rounded-xl transition-colors"
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
              className="text-xs bg-gray-100 hover:bg-brand-100 hover:text-brand-500 text-gray-600 px-3 py-1.5 rounded-full transition-colors"
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

// ── Plan List ──────────────────────────────────────────────────────────────

function PlanList({
  plans,
  setPlans,
  loading,
  onGenerate,
}: {
  plans: WorkoutPlan[]
  setPlans: React.Dispatch<React.SetStateAction<WorkoutPlan[]>>
  loading: boolean
  onGenerate: () => void
}) {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  async function handleDelete(id: string) {
    try {
      await api.workouts.deletePlan(id)
      setPlans((p) => p.filter((x) => x.id !== id))
      setConfirmDeleteId(null)
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

  return (
    <div className="p-6 max-w-2xl">
      {loading ? (
        <div className="space-y-3">{[1, 2].map((i) => <SkeletonCard key={i} />)}</div>
      ) : plans.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-4xl mb-3">🏋️</div>
          <p className="text-gray-500 text-sm mb-4">No plans yet. Generate your first one!</p>
          <button onClick={onGenerate} className="bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium px-5 py-2.5 rounded-xl transition-colors">
            Generate plan
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`bg-white rounded-2xl border-2 p-5 cursor-pointer transition-colors ${plan.is_active ? 'border-brand-400' : 'border-gray-100 hover:border-gray-200'}`}
              onClick={() => navigate(`/workouts/${plan.id}`)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {plan.is_active && (
                      <span className="text-xs font-semibold text-brand-500 bg-brand-50 px-2 py-0.5 rounded-full">Active</span>
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
                      className="text-xs text-white bg-emerald-500 hover:bg-emerald-600 font-semibold px-3 py-1.5 rounded-lg transition-colors"
                    >
                      Set active
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(plan.id) }}
                    className="text-xs text-white bg-red-500 hover:bg-red-600 font-semibold px-3 py-1.5 rounded-lg transition-colors"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {confirmDeleteId && (
        <ConfirmModal
          title="Delete plan?"
          message="This will permanently delete the workout plan and all its data. This can't be undone."
          confirmLabel="Delete plan"
          onConfirm={() => handleDelete(confirmDeleteId)}
          onClose={() => setConfirmDeleteId(null)}
        />
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
  const [showGenerate, setShowGenerate] = useState(false)
  const [plans, setPlans] = useState<WorkoutPlan[]>([])
  const [plansLoading, setPlansLoading] = useState(true)

  useEffect(() => {
    api.workouts.listPlans()
      .then(setPlans)
      .catch(() => {})
      .finally(() => setPlansLoading(false))
  }, [])

  if (showGenerate) {
    return (
      <GeneratePlanFlow
        onBack={() => setShowGenerate(false)}
        onSaved={(p) => { setPlans((prev) => [p, ...prev]); setShowGenerate(false) }}
      />
    )
  }

  return (
    <div>
      <PageHeader
        title="Workouts"
        subtitle="Training plans tailored to your goals"
        action={
          <button
            onClick={() => setShowGenerate(true)}
            className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors"
          >
            + Generate
          </button>
        }
      />
      <div className="border-b border-gray-100 px-6 pt-4 pb-0">
        <div className="flex gap-6">
          {(['plans', 'progress'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors capitalize ${
                tab === t ? 'border-brand-500 text-brand-500' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      {tab === 'plans' ? (
        <PlanList plans={plans} setPlans={setPlans} loading={plansLoading} onGenerate={() => setShowGenerate(true)} />
      ) : (
        <ProgressTab />
      )}
    </div>
  )
}
