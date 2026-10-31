import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type WorkoutPlan, type WorkoutPlanDetail, type WorkoutPlanPreview, type WorkoutSessionDetail, type WorkoutSessionSummary, type SetLog, type ExerciseHistory, type Exercise, type MeasurementType } from '../lib/api'
import {
  type SetField, type WeightUnit, type TimeUnit, type DistanceUnit,
  fieldsFor, fieldLabel, prescriptionMismatch, toDisplayField, toStorageField,
  chartSpecFor, chartLabel, formatChartValue, formatDuration, timeUnitFor,
  distanceUnitFor,
} from '../lib/units'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'
import { useAuth } from '../hooks/useAuth'
import { SkeletonCard, SkeletonText } from '../components/Skeleton'
import PageHeader from '../components/PageHeader'
import ExerciseDrawer from '../components/ExerciseDrawer'
import WorkoutChatDrawer from '../components/WorkoutChatDrawer'
import UpcomingWeeks from '../components/workout/UpcomingWeeks'
import SupersetCard, { type SupersetMember } from '../components/workout/SupersetCard'
import {
  groupIntoUnits, compositeLabel, compositeRounds, compositeRest,
} from '../lib/composites'
import SupersetPicker from '../components/workout/SupersetPicker'
import ConfirmModal from '../components/ConfirmModal'
import { compressImage } from '../lib/imageUtils'

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
const SPECIFIC_GOALS = [
  { id: 'lose_weight',      label: 'Lose weight',         icon: '⚖️', targetType: 'weight' as const },
  { id: 'reduce_belly_fat', label: 'Reduce belly fat',    icon: '🎯' },
  { id: 'build_muscle',     label: 'Build muscle',        icon: '💪' },
  { id: 'run_distance',     label: 'Run a distance',      icon: '🏃', targetType: 'distance' as const },
  { id: 'get_stronger',     label: 'Get stronger',        icon: '🏋️' },
  { id: 'improve_stamina',  label: 'Improve stamina',     icon: '⚡' },
  { id: 'better_posture',   label: 'Better posture',      icon: '🧘' },
]
const DISTANCE_OPTIONS = ['5K', '10K', 'Half marathon', 'Full marathon']

function GeneratePlanFlow({ onBack, onSaved }: { onBack: () => void; onSaved: (p: WorkoutPlan) => void }) {
  const { user } = useAuth()
  const { showToast } = useToast()
  const navigate = useNavigate()

  // Step state
  const [step, setStep] = useState<'form' | 'goals' | 'recommendation' | 'preview'>('form')
  const [expandedPreviewWeeks, setExpandedPreviewWeeks] = useState<Set<number>>(new Set([1]))

  // Step 1 — basics
  const [daysPerWeek, setDaysPerWeek] = useState(4)
  const [durationWeeks, setDurationWeeks] = useState(8)
  const [fitnessGoal, setFitnessGoal] = useState<string>(user?.profile?.fitness_goal ?? 'build_muscle')
  const [experienceLevel, setExperienceLevel] = useState<string>(user?.profile?.experience_level ?? 'intermediate')
  const [equipment, setEquipment] = useState('full gym')
  const [latestScan, setLatestScan] = useState<{ physique_category: string; body_fat_pct: number | null; muscle_mass_note: string; recommendations: string } | null>(null)
  const [bodyPhoto, setBodyPhoto] = useState<File | null>(null)
  const [useBodyContext, setUseBodyContext] = useState(false)

  // Step 2 — specific goals
  const [specificGoals, setSpecificGoals] = useState<string[]>([])
  // The unit is SEEDED from the profile and always written explicitly. It used
  // to be set only by the <select>'s onChange, so it stayed `undefined` unless
  // you actively touched the dropdown -- the backend then had to guess, and
  // "lose 2 kg" came back as 2 lb.
  const goalUnit: WeightUnit = (user?.profile?.preferred_unit ?? 'lb') as WeightUnit
  const [goalParams, setGoalParams] = useState<Record<string, { amount?: number; unit?: string; distance?: string }>>(
    { lose_weight: { unit: goalUnit } }
  )
  const [notes, setNotes] = useState('')

  // Step 3 — AI recommendation
  const [loadingRec, setLoadingRec] = useState(false)
  const [targetRec, setTargetRec] = useState<{ message: string; program_target: WorkoutPlan['program_target'] } | null>(null)
  const [editedTarget, setEditedTarget] = useState<number | null>(null)
  const [recQuestion, setRecQuestion] = useState('')
  const [refiningRec, setRefiningRec] = useState(false)

  // Step 4 — preview
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<WorkoutPlanPreview | null>(null)
  const [chatOpen, setChatOpen] = useState(false)

  useEffect(() => {
    api.body.history().then((results) => {
      if (results.length > 0) { setLatestScan(results[0]); setUseBodyContext(true) }
    }).catch(() => {})
  }, [])

  function getBodyContext() {
    return useBodyContext && latestScan
      ? `Physique: ${latestScan.physique_category}. Body fat: ${latestScan.body_fat_pct}%. ${latestScan.muscle_mass_note} ${latestScan.recommendations}`.trim()
      : undefined
  }

  function toggleGoal(id: string) {
    setSpecificGoals(prev => prev.includes(id) ? prev.filter(g => g !== id) : [...prev, id])
  }

  async function handleGetRecommendation() {
    setLoadingRec(true)
    try {
      const result = await api.workouts.suggestTarget({
        fitness_goal: fitnessGoal,
        experience_level: experienceLevel,
        days_per_week: daysPerWeek,
        duration_weeks: durationWeeks,
        equipment,
        specific_goals: specificGoals,
        goal_params: goalParams,
        notes,
        body_context: getBodyContext(),
      })
      setTargetRec(result)
      setEditedTarget(result.program_target?.recommended_value ?? null)
      setStep('recommendation')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setLoadingRec(false)
    }
  }

  async function handleRefineRec() {
    if (!recQuestion.trim() || !targetRec) return
    setRefiningRec(true)
    try {
      const result = await api.workouts.suggestTarget({
        fitness_goal: fitnessGoal,
        experience_level: experienceLevel,
        days_per_week: daysPerWeek,
        duration_weeks: durationWeeks,
        equipment,
        specific_goals: specificGoals,
        goal_params: goalParams,
        notes,
        body_context: getBodyContext(),
        user_question: recQuestion,
        previous_recommendation: targetRec.message,
      })
      setTargetRec(result)
      setEditedTarget(result.program_target?.recommended_value ?? null)
      setRecQuestion('')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setRefiningRec(false)
    }
  }

  async function handleGenerate() {
    setGenerating(true)
    // Carry the unit explicitly. Without it the unit survived only inside the
    // free-text `label`, so the AI re-interpreted "lose 2 kg" however it liked.
    const confirmedTarget = targetRec?.program_target
      ? {
          ...targetRec.program_target,
          recommended_value: editedTarget ?? targetRec.program_target.recommended_value,
          unit: targetRec.program_target.unit
            ?? goalParams['lose_weight']?.unit
            ?? goalUnit,
        }
      : undefined
    try {
      const data = await api.workouts.generatePlan({
        days_per_week: daysPerWeek,
        duration_weeks: durationWeeks,
        fitness_goal: fitnessGoal,
        experience_level: experienceLevel,
        equipment,
        notes,
        body_context: getBodyContext(),
        body_photo: bodyPhoto ?? undefined,
        specific_goals: specificGoals,
        goal_params: goalParams,
        confirmed_target: confirmedTarget,
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

const btnBase = 'w-full bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors'
  const chipBase = (active: boolean) => `flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium border transition-colors cursor-pointer ${active ? 'bg-brand-500 text-white border-brand-500' : 'bg-white text-gray-700 border-gray-200 hover:border-brand-300'}`

  // Step indicator
  const STEPS = ['Basics', 'Your Goals', 'AI Recommendation', 'Preview']
  const stepIdx = { form: 0, goals: 1, recommendation: 2, preview: 3 }[step]

  return (
    <div>
      <PageHeader title="Create Plan" subtitle="AI-powered, personalised for you" />
      <div className="p-4 md:p-6 max-w-2xl">
        <button onClick={step === 'form' ? onBack : () => setStep(step === 'goals' ? 'form' : step === 'recommendation' ? 'goals' : 'recommendation')}
          className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-4 transition-colors">
          ← {step === 'form' ? 'Back to plans' : 'Back'}
        </button>

        {/* Step indicator */}
        <div className="flex items-center gap-1 mb-6 overflow-x-auto pb-1">
          {STEPS.map((s, i) => (
            <div key={s} className="flex items-center gap-1 flex-shrink-0">
              <div className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full transition-colors ${i === stepIdx ? 'bg-brand-500 text-white' : i < stepIdx ? 'bg-brand-100 text-brand-600' : 'bg-gray-100 text-gray-400'}`}>
                <span>{i < stepIdx ? '✓' : i + 1}</span>
                <span>{s}</span>
              </div>
              {i < STEPS.length - 1 && <div className={`w-4 h-px flex-shrink-0 ${i < stepIdx ? 'bg-brand-300' : 'bg-gray-200'}`} />}
            </div>
          ))}
        </div>

        {/* ── STEP 1: Basics ── */}
        {step === 'form' && (
          <div className="bg-white rounded-2xl border border-gray-100 p-5 space-y-5">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-3">Primary goal</label>
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
              <label className="block text-sm font-semibold text-gray-700 mb-3">Experience level</label>
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
              <label className="block text-sm font-semibold text-gray-700 mb-3">Days per week</label>
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
              <label className="block text-sm font-semibold text-gray-700 mb-3">Duration</label>
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
              <label className="block text-sm font-semibold text-gray-700 mb-3">Equipment</label>
              <div className="flex gap-2">
                {EQUIPMENT_OPTIONS.map((e) => (
                  <button key={e.value} onClick={() => setEquipment(e.value)}
                    className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${equipment === e.value ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    {e.label}
                  </button>
                ))}
              </div>
            </div>
            <button onClick={() => setStep('goals')} className={btnBase}>
              Next: Your Goals →
            </button>
          </div>
        )}

        {/* ── STEP 2: Specific Goals ── */}
        {step === 'goals' && (
          <div className="bg-white rounded-2xl border border-gray-100 p-5 space-y-5">
            <div>
              <p className="text-base font-semibold text-gray-900 mb-1">What are you working towards?</p>
              <p className="text-sm text-gray-500 mb-4">Select all that apply — mix and match freely.</p>
              <div className="grid grid-cols-2 gap-2">
                {SPECIFIC_GOALS.map((g) => {
                  const active = specificGoals.includes(g.id)
                  return (
                    <div key={g.id}>
                      <button onClick={() => toggleGoal(g.id)} className={`w-full text-left ${chipBase(active)}`}>
                        <span>{g.icon}</span>
                        <span>{g.label}</span>
                      </button>
                      {active && g.targetType === 'weight' && (
                        <div className="mt-1.5 flex items-center gap-2 px-1">
                          <span className="text-xs text-gray-500">Lose</span>
                          <input
                            type="number" min={1} max={50}
                            value={goalParams['lose_weight']?.amount ?? ''}
                            onChange={(e) => setGoalParams(p => ({ ...p, lose_weight: { unit: goalUnit, ...p.lose_weight, amount: Number(e.target.value) } }))}
                            placeholder="0"
                            className="w-16 border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand-400"
                          />
                          <select
                            value={goalParams['lose_weight']?.unit ?? goalUnit}
                            onChange={(e) => setGoalParams(p => ({ ...p, lose_weight: { ...p.lose_weight, unit: e.target.value } }))}
                            className="border border-gray-200 rounded-lg px-1 py-1 text-xs focus:outline-none"
                          >
                            <option value="kg">kg</option><option value="lb">lb</option>
                          </select>
                        </div>
                      )}
                      {active && g.targetType === 'distance' && (
                        <div className="mt-1.5 flex flex-wrap gap-1 px-1">
                          {DISTANCE_OPTIONS.map((d) => (
                            <button key={d} onClick={() => setGoalParams(p => ({ ...p, run_distance: { distance: d } }))}
                              className={`text-xs px-2 py-1 rounded-lg border transition-colors ${goalParams['run_distance']?.distance === d ? 'bg-brand-500 text-white border-brand-500' : 'border-gray-200 text-gray-600 hover:border-brand-300'}`}>
                              {d}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Injuries, limitations or preferences <span className="text-gray-400 font-normal">(optional)</span>
              </label>
              <textarea
                value={notes} onChange={(e) => setNotes(e.target.value)}
                rows={2}
                placeholder="e.g. bad lower back, no squats, prefer not to run, love deadlifts..."
                className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
            </div>

            <div className="border-t border-gray-100 pt-4">
              <label className="block text-sm font-semibold text-gray-700 mb-3">Body context <span className="text-gray-400 font-normal">(helps AI personalise weights)</span></label>
              {latestScan && (
                <div className={`rounded-xl border p-3 cursor-pointer transition-colors mb-2 ${useBodyContext ? 'border-brand-400 bg-brand-50' : 'border-gray-200'}`} onClick={() => setUseBodyContext(v => !v)}>
                  <div className="flex items-center gap-2">
                    <span className={`w-4 h-4 rounded-full border-2 flex-shrink-0 ${useBodyContext ? 'border-brand-500 bg-brand-500' : 'border-gray-300'}`} />
                    <p className="text-sm font-medium text-gray-900">Use latest body scan</p>
                  </div>
                  <p className="text-xs text-gray-500 pl-6 mt-0.5">{latestScan.physique_category}{latestScan.body_fat_pct ? ` · ${latestScan.body_fat_pct}% body fat` : ''}</p>
                </div>
              )}
              <label className="flex items-center gap-2 cursor-pointer text-sm text-brand-500 hover:text-brand-600 font-medium">
                <input type="file" accept="image/*" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setBodyPhoto(await compressImage(f)) }} />
                {bodyPhoto ? `📷 ${bodyPhoto.name}` : '+ Upload body photo'}
              </label>
              {bodyPhoto && <button onClick={() => setBodyPhoto(null)} className="text-xs text-gray-400 hover:text-red-400 mt-1">Remove</button>}
            </div>

            <button onClick={handleGetRecommendation} disabled={loadingRec} className={btnBase}>
              {loadingRec ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Analyzing your goals...
                </span>
              ) : 'Get AI Recommendation →'}
            </button>
          </div>
        )}

        {/* ── STEP 3: AI Recommendation ── */}
        {step === 'recommendation' && targetRec && (
          <div className="space-y-4">
            <div className="bg-white rounded-2xl border border-gray-100 p-5">
              <div className="flex items-start gap-3 mb-4">
                <div className="w-9 h-9 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0 text-lg">🤖</div>
                <p className="text-sm text-gray-700 leading-relaxed">{targetRec.message}</p>
              </div>

              {targetRec.program_target && (
                <div className="bg-brand-50 border border-brand-100 rounded-xl p-4">
                  <p className="text-xs font-semibold text-brand-600 mb-2 uppercase tracking-wide">Recommended target</p>
                  <p className="text-sm font-semibold text-gray-900 mb-3">{targetRec.program_target.label}</p>
                  <div className="flex items-center gap-3">
                    {targetRec.program_target.current_value != null && (
                      <div className="text-center">
                        <p className="text-lg font-bold text-gray-400">{targetRec.program_target.current_value}</p>
                        <p className="text-xs text-gray-400">Current</p>
                      </div>
                    )}
                    {targetRec.program_target.current_value != null && <div className="text-gray-300 text-lg">→</div>}
                    <div className="text-center">
                      <input
                        type="number"
                        value={editedTarget ?? ''}
                        onChange={(e) => setEditedTarget(Number(e.target.value))}
                        className="w-20 text-center text-2xl font-extrabold text-brand-600 bg-white border-2 border-brand-300 rounded-xl py-1 focus:outline-none focus:border-brand-500"
                      />
                      <p className="text-xs text-brand-500 mt-0.5">Target (tap to edit)</p>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Ask AI to refine */}
            <div className="bg-white rounded-2xl border border-gray-100 p-4">
              <p className="text-xs font-semibold text-gray-500 mb-2">Not what you expected? Ask the AI to adjust</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={recQuestion}
                  onChange={(e) => setRecQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && !refiningRec && handleRefineRec()}
                  placeholder="e.g. I want to focus more on fat loss, not muscle gain"
                  className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400"
                  disabled={refiningRec}
                />
                <button
                  onClick={handleRefineRec}
                  disabled={refiningRec || !recQuestion.trim()}
                  className="bg-brand-500 hover:bg-brand-600 disabled:opacity-40 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors"
                >
                  {refiningRec ? (
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin inline-block" />
                  ) : '→'}
                </button>
              </div>
            </div>

            <button onClick={handleGenerate} disabled={generating} className={btnBase}>
              {generating ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Building your {durationWeeks}-week plan...
                </span>
              ) : `Generate my ${durationWeeks}-week plan →`}
            </button>
          </div>
        )}

        {/* ── STEP 4: Preview ── */}
        {step === 'preview' && preview && (
          <>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-bold text-gray-900">{preview.title}</h2>
                {preview.description && <p className="text-sm text-gray-500 mt-0.5">{preview.description}</p>}
              </div>
              <button onClick={() => setChatOpen(true)}
                className="text-sm text-brand-500 bg-brand-50 hover:bg-brand-100 font-semibold px-3 py-1.5 rounded-lg transition-colors flex-shrink-0 ml-2">
                ✨ Refine
              </button>
            </div>

            <div className="space-y-2 mb-5">
              {/* Three shapes: `week` (per-week generation — what new plans
                  return), `weeks` (legacy whole-plan), `days` (older single
                  week). Reading only the first two rendered an empty preview
                  for every new plan. */}
              {(preview.weeks
                ?? (preview.week ? [preview.week] : null)
                ?? (preview.days ? [preview.days] : [])).map((weekDays, idx) => {
                const weekNum = idx + 1
                const isOpen = expandedPreviewWeeks.has(weekNum)
                const trainingCount = weekDays.filter((d) => !d.is_rest_day).length
                return (
                  <div key={weekNum} className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
                    <button
                      className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors"
                      onClick={() => setExpandedPreviewWeeks((prev) => {
                        const next = new Set(prev)
                        next.has(weekNum) ? next.delete(weekNum) : next.add(weekNum)
                        return next
                      })}
                    >
                      <div className="flex items-center gap-3">
                        <span className="w-7 h-7 rounded-full bg-brand-100 text-brand-600 text-xs font-bold flex items-center justify-center flex-shrink-0">
                          {weekNum}
                        </span>
                        <div className="text-left">
                          <p className="text-sm font-semibold text-gray-900">Week {weekNum}</p>
                          <p className="text-xs text-gray-400">{trainingCount} training day{trainingCount !== 1 ? 's' : ''}</p>
                        </div>
                      </div>
                      <span className="text-gray-400 text-sm">{isOpen ? '▾' : '▸'}</span>
                    </button>

                    {isOpen && (
                      <div className="border-t border-gray-50 px-4 pb-3 pt-2 space-y-3">
                        {weekDays.map((day) => (
                          <div key={day.day_number}>
                            <div className="flex items-center gap-2 mb-1">
                              <span className="w-5 h-5 rounded-full bg-gray-100 text-gray-500 text-[10px] font-bold flex items-center justify-center flex-shrink-0">{day.day_number}</span>
                              <p className="font-semibold text-gray-800 text-sm">{day.name}</p>
                              {day.focus && <span className="text-xs text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">{day.focus}</span>}
                              {day.is_rest_day && <span className="ml-auto text-xs text-gray-400">Rest</span>}
                            </div>
                            {!day.is_rest_day && (
                              <div className="pl-7 space-y-0.5">
                                {groupIntoUnits(day.exercises).map((unit, i) => (
                                  unit.kind === 'composite' ? (
                                    /* A bracket down the left edge: the compact
                                       list has no room for a card, but the
                                       grouping still has to be visible here —
                                       this is the first place the user reads
                                       the plan. */
                                    <div key={i} className="border-l-2 border-brand-200 pl-2 my-1">
                                      <p className="text-[10px] font-bold text-brand-500 uppercase tracking-wide">
                                        {compositeLabel(unit.items.length)}
                                      </p>
                                      {unit.items.map((ex, j) => (
                                        <div key={j} className="flex items-center justify-between text-xs text-gray-600 py-0.5">
                                          <span>{ex.name}</span>
                                          <span className="text-gray-400 ml-2 whitespace-nowrap">{ex.sets} × {ex.reps}</span>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <div key={i} className="flex items-center justify-between text-xs text-gray-600 py-0.5">
                                      <span>{unit.item.name}</span>
                                      <span className="text-gray-400 ml-2 whitespace-nowrap">{unit.item.sets} × {unit.item.reps}</span>
                                    </div>
                                  )
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Only week 1 is generated up front, so without this an 8-week
                plan would preview as a single week and look broken. The
                outline is what makes the rest of the program legible. */}
            {preview.program_outline && preview.program_outline.length > 0 && (
              <div className="mb-5 bg-white rounded-2xl border border-gray-100 p-4">
                <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3">
                  Then, as you go
                </p>
                <div className="space-y-2">
                  {preview.program_outline.map((phase) => (
                    <div key={`${phase.start_week}-${phase.end_week}`} className="flex gap-3">
                      <span className="text-xs font-bold text-brand-500 whitespace-nowrap pt-0.5 w-16 flex-shrink-0">
                        {phase.start_week === phase.end_week
                          ? `Week ${phase.start_week}`
                          : `Wk ${phase.start_week}-${phase.end_week}`}
                      </span>
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-gray-900">{phase.name}</p>
                        {phase.focus && (
                          <p className="text-xs text-gray-400">{phase.focus}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-gray-400 mt-3 pt-3 border-t border-gray-100">
                  Each week is written when you reach it, using what you
                  actually lifted in the weeks before — not guessed now.
                </p>
              </div>
            )}

            <button onClick={handleSave} disabled={saving} className={btnBase}>
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
    <button
      onClick={() => onClick(exercise)}
      className="flex-shrink-0 w-40 bg-white rounded-xl shadow-sm border border-gray-100 flex flex-col p-3 overflow-hidden gap-2 text-left hover:border-brand-200 hover:shadow-md active:bg-gray-50 transition-all"
    >
      <p className="text-sm font-bold text-gray-900 leading-tight line-clamp-2">{exercise.name}</p>
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-semibold bg-brand-100 text-brand-500 px-1.5 py-0.5 rounded-full">
          {exercise.sets} × {exercise.reps}
        </span>
        {exercise.rest_seconds != null && (
          <span className="text-xs text-gray-400">⏱ {exercise.rest_seconds}s</span>
        )}
      </div>
    </button>
  )
}

/**
 * A composite (superset / triset) in the PLAN views.
 *
 * Supersets used to be invisible until you started the workout: the plan
 * listed every member as its own independent card, so the only place a
 * grouping showed up was the live session. That is too late — the plan view is
 * where you check the plan is right.
 *
 * Each member keeps its own `sets × reps` line, because they genuinely differ
 * (bench 4×12 weighted, push-ups 4×10 bodyweight) and a single shared figure
 * would be a lie about one of them. Rest is shown ONCE, on the header, because
 * it belongs to the round rather than to any member.
 */
function CompositeCard({ exercises, onClick }: {
  exercises: Exercise[]
  onClick: (e: Exercise) => void
}) {
  const rounds = compositeRounds(exercises.map((e) => e.sets))
  const rest = compositeRest(exercises)

  return (
    <div className="flex-shrink-0 w-56 bg-white rounded-xl shadow-sm border-2 border-brand-200 flex flex-col overflow-hidden">
      <div className="px-3 py-2 bg-brand-50/70 border-b border-brand-100">
        <p className="text-[10px] font-bold text-brand-500 uppercase tracking-widest">
          ⟲ {compositeLabel(exercises.length)} · {rounds} round{rounds === 1 ? '' : 's'}
        </p>
        {rest != null && (
          <p className="text-[10px] text-gray-400">
            {rest}s rest after each round
          </p>
        )}
      </div>
      <div className="divide-y divide-gray-50">
        {exercises.map((exercise) => (
          <button
            key={exercise.id}
            onClick={() => onClick(exercise)}
            className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-gray-50 active:bg-gray-100 transition-colors"
          >
            <span className="text-xs font-semibold text-gray-900 leading-tight line-clamp-2 min-w-0">
              {exercise.name}
            </span>
            <span className="text-[11px] font-semibold text-gray-500 whitespace-nowrap flex-shrink-0">
              {exercise.sets} × {exercise.reps}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

/** Plan-view exercise list: composites become one card, the rest stay as they were. */
function DayExerciseRow({ exercises, onClick }: {
  exercises: Exercise[]
  onClick: (e: Exercise) => void
}) {
  return (
    <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1">
      {groupIntoUnits(exercises).map((unit) => (
        unit.kind === 'composite' ? (
          <CompositeCard key={`ss:${unit.group}:${unit.items[0].id}`}
                         exercises={unit.items} onClick={onClick} />
        ) : (
          <ExerciseCard key={unit.item.id} exercise={unit.item}
                        onClick={onClick} />
        )
      ))}
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
  const [sessions, setSessions] = useState<WorkoutSessionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [endingWeek, setEndingWeek] = useState(false)
  const [preparingGuides, setPreparingGuides] = useState(false)
  const [startingDay, setStartingDay] = useState<string | null>(null)
  const [conflictSession, setConflictSession] = useState<{ id: string; dayName: string; targetDayId: string } | null>(null)
  const [selectedExercise, setSelectedExercise] = useState<Exercise | null>(null)
  const [editDayId, setEditDayId] = useState<string | null>(null)
  const [editDayNumber, setEditDayNumber] = useState<number | null>(null)
  const [explicitChatOpen, setExplicitChatOpen] = useState(false)
  const [weeksExpanded, setWeeksExpanded] = useState(false)

  const chatOpen = explicitChatOpen || editDayId !== null

  useEffect(() => {
    Promise.all([
      api.workouts.getPlan(planId),
      api.workouts.listSessions(),
    ])
      .then(([p, s]) => { setPlan(p); setSessions(s) })
      .catch(() => showToast('Failed to load plan', 'error'))
      .finally(() => setLoading(false))
  }, [planId])

  /**
   * Poll while a week is generating in the background.
   *
   * Generation runs in a server-side thread (no task queue on a single
   * worker), so there is nothing to subscribe to — the client asks. Only
   * polls while `is_preparing` is true, which the server reports as false
   * once the lock goes stale, so a dyno restart mid-generation stops this
   * rather than leaving it spinning forever.
   */
  useEffect(() => {
    if (!plan?.is_preparing) return
    const timer = setInterval(() => {
      api.workouts.getPlan(plan.id).then(setPlan).catch(() => {})
    }, 5000)
    return () => clearInterval(timer)
  }, [plan?.is_preparing, plan?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Build a map of dayId → most recent session (in-progress or today-completed)
  const today = new Date().toDateString()
  const daySessionMap = sessions.reduce<Record<string, WorkoutSessionSummary>>((acc, s) => {
    if (!s.exercise_day_id) return acc
    const existing = acc[s.exercise_day_id]
    // Prefer in-progress; otherwise keep the most recent completed today
    if (!existing) { acc[s.exercise_day_id] = s; return acc }
    if (!existing.is_completed && s.is_completed) return acc  // keep in-progress
    if (existing.is_completed && !s.is_completed) { acc[s.exercise_day_id] = s; return acc }
    if (new Date(s.started_at) > new Date(existing.started_at)) acc[s.exercise_day_id] = s
    return acc
  }, {})

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

  async function handleEndWeek() {
    if (!plan) return
    setEndingWeek(true)
    try {
      const result = await api.workouts.advanceWeek(plan.id)
      if (result.program_complete) {
        showToast('Program complete! Great work.')
        setPlan((p) => p ? { ...p, goal_check_in_shown: true } : p)
      } else {
        setPlan((p) => p ? { ...p, current_week: result.current_week } : p)
        setPreparingGuides(true)
        try {
          await api.workouts.prepareWeek(plan.id)
          showToast(`Week ${result.current_week} ready — guides pre-loaded!`)
        } catch {
          showToast(`Week ${result.current_week} starts now!`)
        } finally {
          setPreparingGuides(false)
        }
      }
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setEndingWeek(false)
    }
  }

  async function handleStartSession(dayId: string, force = false) {
    setStartingDay(dayId)
    try {
      const session = await api.workouts.startSession(dayId, force)
      if (session.already_active && session.conflict_day_name) {
        setConflictSession({ id: session.id, dayName: session.conflict_day_name, targetDayId: dayId })
        setStartingDay(null)
        return
      }
      navigate(`/workouts/session/${session.id}`)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setStartingDay(null)
    }
  }

  if (loading) return <div className="p-6"><SkeletonText lines={8} /></div>
  if (!plan) return null

  const durationWeeks = plan.duration_weeks ?? 1
  const currentWeek = plan.current_week ?? 1
  const maxWeekInDb = Math.max(...plan.days.map((d) => d.week_number))
  const maxWeek = Math.max(maxWeekInDb, plan.duration_weeks ?? 1)
  // Only show repeat-weeks UI when the DB actually contains multiple distinct weeks
  const hasRepeatWeeks = maxWeekInDb > 1
  const currentWeekDays = plan.days.filter((d) => d.week_number === currentWeek).length > 0
    ? plan.days.filter((d) => d.week_number === currentWeek)
    : plan.days.filter((d) => d.week_number === 1)
  const trainingDays = currentWeekDays.filter((d) => !d.is_rest_day).length

  return (
    <>
      {conflictSession && (
        <ConfirmModal
          title="You have an unfinished workout"
          message={`You're still in the middle of "${conflictSession.dayName}". Go back to finish it, or start this day anyway?`}
          confirmLabel="Start anyway"
          cancelLabel={`Continue "${conflictSession.dayName}"`}
          onConfirm={async () => {
            setConflictSession(null)
            await handleStartSession(conflictSession.targetDayId, true)
          }}
          onClose={() => {
            navigate(`/workouts/session/${conflictSession.id}`)
            setConflictSession(null)
          }}
        />
      )}
      <PageHeader
        title={plan.title}
        subtitle={`Week ${currentWeek} of ${durationWeeks} · ${trainingDays} training day${trainingDays !== 1 ? 's' : ''}`}
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setExplicitChatOpen(true)}
              className="bg-white/20 text-white text-sm font-semibold px-3 py-2 rounded-xl hover:bg-white/30 transition-colors"
            >
              ✏️ Edit with AI
            </button>
            {!plan.is_active ? (
              <button onClick={handleActivate} className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors">
                Set active
              </button>
            ) : (
              <span className="bg-white/20 text-white text-xs font-semibold px-3 py-1.5 rounded-full">Active</span>
            )}
          </div>
        }
      />

      <div className="p-6">
        <button onClick={() => navigate('/workouts')} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6 transition-colors">
          ← All plans
        </button>

        {plan.description && (
          <p className="text-sm text-gray-500 mb-6">{plan.description}</p>
        )}

        {/* Current week days */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-4">
            <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">
              Week {currentWeek}{maxWeek > 1 ? ` of ${maxWeek}` : ''}
            </p>
            {plan.is_active && (
              <button
                onClick={handleEndWeek}
                disabled={endingWeek || preparingGuides}
                className="text-xs text-brand-500 border border-brand-200 rounded-lg px-3 py-1.5 hover:bg-brand-50 transition-colors font-medium disabled:opacity-50"
              >
                {endingWeek ? 'Finishing…' : currentWeek >= maxWeek ? 'Finish program →' : 'Finish week →'}
              </button>
            )}
          </div>
          {preparingGuides && (
            <div className="mb-4 flex items-center gap-3 bg-brand-50 border border-brand-100 rounded-xl px-4 py-3">
              <div className="w-4 h-4 border-2 border-brand-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
              <p className="text-sm text-brand-700 font-medium">Preparing exercise guides for Week {plan.current_week}…</p>
            </div>
          )}

          <div className="space-y-6">
            {currentWeekDays.map((day) => (
              <div key={day.id}>
                {/* Day header */}
                {(() => {
                  const completedRecently = day.last_completed_at
                    && (Date.now() - new Date(day.last_completed_at).getTime()) < 7 * 86_400_000
                  return (
                <div className="flex items-center gap-3 mb-3">
                  <span className={`w-6 h-6 rounded-full text-xs font-bold flex items-center justify-center flex-shrink-0 ${completedRecently ? 'bg-emerald-100 text-emerald-600' : 'bg-brand-100 text-brand-500'}`}>
                    {completedRecently ? '✓' : day.day_number}
                  </span>
                  <p className="font-semibold text-gray-900 text-sm">{day.name}</p>
                  {day.focus && (
                    <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">{day.focus}</span>
                  )}
                  {!day.is_rest_day && (
                    <div className="ml-auto flex items-center gap-2">
                      <button
                        onClick={() => { setEditDayId(day.id); setEditDayNumber(day.day_number) }}
                        className="text-xs text-brand-500 border border-brand-300 px-3 py-1.5 rounded-lg hover:bg-brand-50 transition-colors"
                      >
                        Edit
                      </button>
                      {(() => {
                        const s = daySessionMap[day.id]
                        if (s && !s.is_completed) {
                          return (
                            <button
                              onClick={() => navigate(`/workouts/session/${s.id}`)}
                              className="text-sm bg-amber-500 hover:bg-amber-600 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
                            >
                              Continue
                            </button>
                          )
                        }
                        if (s && s.is_completed && new Date(s.started_at).toDateString() === today) {
                          return (
                            <button
                              onClick={() => navigate(`/workouts/session/${s.id}`)}
                              className="text-sm bg-emerald-500 hover:bg-emerald-600 text-white font-semibold px-3 py-1.5 rounded-lg transition-colors"
                            >
                              Done ✓ · Review
                            </button>
                          )
                        }
                        return (
                          <button
                            onClick={() => handleStartSession(day.id)}
                            disabled={startingDay === day.id}
                            className="text-sm bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
                          >
                            {startingDay === day.id ? '...' : 'Start'}
                          </button>
                        )
                      })()}
                    </div>
                  )}
                </div>
                  )
                })()}
                {/* Exercises */}
                {day.is_rest_day ? (
                  <RestDayCard />
                ) : (
                  <DayExerciseRow exercises={day.exercises}
                                  onClick={setSelectedExercise} />
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Other weeks (collapsed) */}
        {hasRepeatWeeks && (
          <div
            onClick={() => setWeeksExpanded((v) => !v)}
            className="mt-2 bg-gray-50 border border-gray-200 rounded-xl p-4 flex items-center justify-between cursor-pointer hover:bg-gray-100 transition-colors select-none"
          >
            <p className="text-sm font-medium text-gray-700">
              View weeks 2–{maxWeekInDb} ▸ progressive overload across phases
            </p>
            <span className="text-gray-400 text-sm">{weeksExpanded ? '▾' : '▸'}</span>
          </div>
        )}

        {weeksExpanded && hasRepeatWeeks && (() => {
          // Group days by week_number, exclude week 1 (shown above)
          const weekMap: Record<number, typeof plan.days> = {}
          plan.days.forEach((d) => {
            if (d.week_number > 1) {
              if (!weekMap[d.week_number]) weekMap[d.week_number] = []
              weekMap[d.week_number].push(d)
            }
          })
          const weekNums = Object.keys(weekMap).map(Number).sort((a, b) => a - b)
          return (
            <div className="mt-4 space-y-8">
              {weekNums.map((wk) => (
                <div key={wk}>
                  <p className="text-xs font-bold text-brand-500 uppercase tracking-widest mb-3">Week {wk}</p>
                  <div className="space-y-6 opacity-75">
                    {weekMap[wk].map((day) => (
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
                          <DayExerciseRow exercises={day.exercises}
                                          onClick={setSelectedExercise} />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )
        })()}
        {/* Weeks not generated yet. Normally nobody touches the button —
            finishing a week auto-starts the next one. */}
        <UpcomingWeeks
          plan={plan}
          onChanged={() => api.workouts.getPlan(plan.id).then(setPlan).catch(() => {})}
        />
      </div>

      {/* Chat FAB — sits above mobile bottom nav (4rem) and desktop support widget */}
      <button
        onClick={() => setExplicitChatOpen(true)}
        className="fixed bottom-24 right-4 sm:bottom-8 sm:right-6 z-30 bg-brand-500 hover:bg-brand-600 text-white rounded-full w-14 h-14 flex items-center justify-center shadow-lg transition-colors text-xl"
        title="Chat with AI Trainer"
      >
        💬
      </button>

      <ExerciseDrawer exercise={selectedExercise} onClose={() => setSelectedExercise(null)} />
      <WorkoutChatDrawer
        isOpen={chatOpen}
        onClose={() => { setExplicitChatOpen(false); setEditDayId(null); setEditDayNumber(null) }}
        planId={plan?.id}
        dayId={editDayId ?? undefined}
        filterDayNumber={editDayNumber ?? undefined}
        onPlanUpdated={() => plan && api.workouts.getPlan(plan.id).then(setPlan).catch(() => {})}
      />
    </>
  )
}

// ── Active Session ─────────────────────────────────────────────────────────

/**
 * The measurement type for a logged set.
 *
 * The server is authoritative: it is stamped on the Exercise, validated on
 * write, and echoed on every SetLog. The frontend no longer guesses from the
 * name -- the old local regexes disagreed with the backend's rules, which is why
 * the same exercise could render differently in two places.
 *
 * `weight_reps` is only used when the server told us nothing at all (an orphaned
 * SetLog whose Exercise was deleted).
 */
function getMeasurementType(
  planEx: Exercise | null | undefined,
  log?: Pick<SetLog, 'measurement_type'> | null,
): MeasurementType {
  return planEx?.measurement_type ?? log?.measurement_type ?? 'weight_reps'
}

type PendingLog = Partial<Record<SetField, number | null>> & { is_completed?: boolean }

/**
 * Draft key for a pending set edit.
 *
 * Keyed by (exercise, set number) rather than SetLog id on purpose. An AI plan
 * edit deletes and recreates the day's rows, so every SetLog gets a NEW id --
 * and the old code looked drafts up by the stale id, found nothing, and silently
 * dropped every ticked set. That is the "sets data going away" bug.
 */
function pendKey(exerciseId: string | null, setNumber: number): string {
  return `${exerciseId ?? 'orphan'}:${setNumber}`
}

function ActiveSession({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const { user } = useAuth()
  const unit: WeightUnit = (user?.profile?.preferred_unit ?? 'lb') as WeightUnit
  const [session, setSession] = useState<WorkoutSessionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [completing, setCompleting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [editChatOpen, setEditChatOpen] = useState(false)
  const [selectedExercise, setSelectedExercise] = useState<Exercise | null>(null)
  const [progressFor, setProgressFor] = useState<string | null>(null)
  const [supersetPickerOpen, setSupersetPickerOpen] = useState(false)
  const [pendingLogs, setPendingLogs] = useState<Record<string, PendingLog>>({})
  // Exercise cards collapse so a 7-exercise day is scannable. Only cards the
  // user has actually tapped are recorded here; everything else falls back to
  // the default (open the first unfinished exercise, collapse the rest), so
  // finished work folds away on its own as you progress.
  const [touchedCards, setTouchedCards] = useState<Record<string, boolean>>({})
  const storageKey = `workout_draft_${sessionId}`

  function toggleCard(key: string, currentlyOpen: boolean) {
    setTouchedCards((prev) => ({ ...prev, [key]: !currentlyOpen }))
  }

  useEffect(() => {
    const saved = localStorage.getItem(storageKey)
    if (saved) { try { setPendingLogs(JSON.parse(saved)) } catch {} }
    api.workouts.getSession(sessionId)
      .then(setSession)
      .catch(() => showToast('Failed to load session', 'error'))
      .finally(() => setLoading(false))
  }, [sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Mirror pendingLogs into localStorage. Also writes the empty case, so a
  // successful flush actually clears the draft instead of leaving a stale one
  // behind for the next visit to re-apply.
  useEffect(() => {
    if (Object.keys(pendingLogs).length > 0) {
      localStorage.setItem(storageKey, JSON.stringify(pendingLogs))
    } else {
      localStorage.removeItem(storageKey)
    }
  }, [pendingLogs, storageKey])

  /** Current value for a set field: pending edit wins, else what the server has. */
  function setFieldValue(log: SetLog, field: SetField): number | null {
    const pending = pendingLogs[pendKey(log.exercise_id, log.set_number)]
    if (pending && field in pending) return pending[field] ?? null
    return (log[field] as number | null) ?? null
  }

  function isSetCompleted(log: SetLog): boolean {
    const pending = pendingLogs[pendKey(log.exercise_id, log.set_number)]
    return pending?.is_completed ?? log.is_completed
  }

  function handleSetField(
    log: SetLog, field: SetField, rawValue: string, timeUnit: TimeUnit = 'sec',
    distanceUnit: DistanceUnit = 'm',
  ) {
    // Convert by measurement type, never by field name. The old code scaled
    // anything called `weight_kg` by KG_PER_LB, which corrupted duration seconds.
    // `timeUnit` is what the INPUT is labelled in; storage is always seconds.
    const typed = rawValue === '' ? null : Number(rawValue)
    const stored = Number.isNaN(typed as number)
      ? null : toStorageField(field, typed, unit, timeUnit, distanceUnit)
    const key = pendKey(log.exercise_id, log.set_number)
    setPendingLogs((prev) => ({ ...prev, [key]: { ...prev[key], [field]: stored } }))
    setIsDirty(true)
  }

  function handleToggle(log: SetLog) {
    const key = pendKey(log.exercise_id, log.set_number)
    const current = isSetCompleted(log)
    setPendingLogs((prev) => ({ ...prev, [key]: { ...prev[key], is_completed: !current } }))
    setIsDirty(true)
  }

  /** Copy the previous set's values into this one ("same as above"). */
  function handleCopyPrevious(log: SetLog, prev: SetLog, mType: MeasurementType) {
    const key = pendKey(log.exercise_id, log.set_number)
    const copied: PendingLog = {}
    for (const field of fieldsFor(mType)) {
      copied[field] = setFieldValue(prev, field)
    }
    setPendingLogs((p) => ({ ...p, [key]: { ...p[key], ...copied } }))
    setIsDirty(true)
  }

  /**
   * Push every pending edit to the server.
   *
   * Changed from Promise.allSettled to Promise.all: allSettled swallowed failures
   * so "Progress saved" appeared even when every POST had 500'd. Sends explicit
   * nulls so a cleared field is actually cleared (the old `!= null` guards made
   * clearing impossible).
   */
  async function flushPendingLogs(sess: WorkoutSessionDetail) {
    const entries = Object.entries(pendingLogs)
    if (entries.length === 0) return

    const requests: Array<Promise<unknown>> = []
    for (const [key, changes] of entries) {
      const log = sess.set_logs.find(
        (l) => pendKey(l.exercise_id, l.set_number) === key)
      if (!log || !log.exercise_id) continue
      const mType = getMeasurementType(
        sess.exercise_day?.exercises.find((e) => e.id === log.exercise_id), log)
      const allowed = fieldsFor(mType)
      const payload: Parameters<typeof api.workouts.logSet>[1] = {
        exercise_id: log.exercise_id,
        set_number: log.set_number,
      }
      for (const field of allowed) {
        if (field in changes) payload[field] = changes[field] ?? null
      }
      if (changes.is_completed !== undefined) payload.is_completed = changes.is_completed
      requests.push(api.workouts.logSet(sess.id, payload))
    }
    await Promise.all(requests)
  }

  async function handleSaveProgress() {
    if (!session) return
    setSaving(true)
    try {
      await flushPendingLogs(session)
      localStorage.removeItem(storageKey)
      showToast('Progress saved — come back anytime')
      navigate('/dashboard')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleComplete() {
    if (!session) return
    setCompleting(true)
    try {
      await flushPendingLogs(session)
      await api.workouts.completeSession(session.id)
      localStorage.removeItem(storageKey)
      showToast('Workout complete! Great work 💪')
      navigate('/dashboard')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setCompleting(false)
    }
  }

  async function handleReComplete() {
    if (!session) return
    setCompleting(true)
    try {
      await flushPendingLogs(session)
      if (isDirty) await api.workouts.completeSession(session.id)
      localStorage.removeItem(storageKey)
      const updated = await api.workouts.getSession(session.id)
      setSession(updated)
      setPendingLogs({})
      setEditing(false)
      setIsDirty(false)
      if (isDirty) showToast('Workout updated!')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setCompleting(false)
    }
  }

  if (loading) return <div className="p-6"><SkeletonText lines={10} /></div>
  if (!session) return null

  // Merge server logs with local pending edits for display, keyed by
  // (exercise, set number) so drafts survive the row replacement an AI plan
  // edit causes.
  const mergedLogs = session.set_logs.map((log) => ({
    ...log,
    ...pendingLogs[pendKey(log.exercise_id, log.set_number)],
  }))

  // Build the exercise list from the PLAN, not from the set logs.
  //
  // Grouping set_logs by exercise_name meant an exercise the AI had just added
  // (which has no logs yet) never appeared at all, and renaming an exercise
  // orphaned its logs because the join was on the name string. Driving off
  // exercise_day.exercises and joining on exercise_id fixes both.
  const planExercises = session.exercise_day?.exercises ?? []
  const logsByExerciseId = mergedLogs.reduce<Record<string, typeof mergedLogs>>((acc, log) => {
    const key = log.exercise_id ?? 'orphan'
    if (!acc[key]) acc[key] = []
    acc[key].push(log)
    return acc
  }, {})

  const exerciseEntries: Array<{
    key: string
    name: string
    planEx: Exercise | null
    logs: typeof mergedLogs
  }> = planExercises.map((ex) => ({
    key: ex.id,
    name: ex.name,
    planEx: ex,
    logs: (logsByExerciseId[ex.id] ?? []).sort((a, b) => a.set_number - b.set_number),
  }))

  // Any logs whose Exercise row is gone (SET_NULL after a past plan edit) still
  // get shown, grouped by their denormalized name, so history is never hidden.
  const orphanedByName = (logsByExerciseId['orphan'] ?? []).reduce<Record<string, typeof mergedLogs>>(
    (acc, log) => {
      if (!acc[log.exercise_name]) acc[log.exercise_name] = []
      acc[log.exercise_name].push(log)
      return acc
    }, {})
  for (const [name, logs] of Object.entries(orphanedByName)) {
    exerciseEntries.push({
      key: `orphan:${name}`,
      name,
      planEx: null,
      logs: logs.sort((a, b) => a.set_number - b.set_number),
    })
  }

  /**
   * Render units: a superset group becomes ONE card, everything else stays a
   * standalone entry on the existing renderer.
   *
   * Grouping is driven by `planEx.superset_group`, so orphaned logs (whose
   * Exercise row was SET_NULL by a past plan edit) can never be grouped —
   * they have no planEx, and the session page groups them by logged name
   * precisely so their history is not hidden.
   */
  // Shared with the three PLAN surfaces via lib/composites, so a grouping is
  // read the same way everywhere. It used to be inline here, which is why the
  // plan views had no notion of supersets at all.
  type RenderUnit =
    | { kind: 'single'; key: string; entry: typeof exerciseEntries[number] }
    | { kind: 'superset'; key: string; entries: typeof exerciseEntries }

  const renderUnits: RenderUnit[] = groupIntoUnits(
    exerciseEntries, (entry) => entry.planEx ?? undefined,
  ).map((unit) => (
    unit.kind === 'composite'
      ? { kind: 'superset' as const,
          key: `ss:${unit.group}:${unit.items[0].key}`,
          entries: unit.items }
      : { kind: 'single' as const, key: unit.item.key, entry: unit.item }
  ))

  const completedSets = mergedLogs.filter((l) => l.is_completed).length
  const totalSets = mergedLogs.length

  // Default-open card: the first exercise that still has work left.
  const firstUnfinishedKey = exerciseEntries.find(
    (e) => !(e.logs.length > 0 && e.logs.every((l) => l.is_completed))
  )?.key
  const openCount = exerciseEntries.filter(
    (e) => (e.key in touchedCards ? touchedCards[e.key] : e.key === firstUnfinishedKey)
  ).length

  function setAllCards(open: boolean) {
    setTouchedCards(Object.fromEntries(exerciseEntries.map((e) => [e.key, open])))
  }
  const progress = totalSets > 0 ? Math.round((completedSets / totalSets) * 100) : 0
  const isCompleted = session.is_completed && !editing

  return (
    <>
      <PageHeader
        title={session.exercise_day?.name ?? 'Workout'}
        subtitle={isCompleted
          ? `Completed ${new Date(session.completed_at!).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
          : (session.exercise_day?.focus ?? '')}
        action={!isCompleted ? (
          <div className="flex items-center gap-2">
            {session.plan_id && session.exercise_day && (
              <button
                onClick={() => setEditChatOpen(true)}
                className="bg-white/20 text-white text-sm font-medium px-3 py-2 rounded-xl hover:bg-white/30 transition-colors"
              >
                Edit day
              </button>
            )}
            <button
              onClick={handleSaveProgress}
              disabled={saving || completing}
              className="bg-white/20 text-white text-sm font-medium px-3 py-2 rounded-xl hover:bg-white/30 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={handleComplete}
              disabled={completing || saving}
              className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors disabled:opacity-50"
            >
              {completing ? 'Finishing…' : 'Finish workout'}
            </button>
          </div>
        ) : editing ? (
          <button
            onClick={handleReComplete}
            disabled={completing}
            className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors disabled:opacity-50"
          >
            {completing ? 'Saving…' : isDirty ? 'Finish workout' : 'Done ✓'}
          </button>
        ) : (
          <button
            onClick={() => { setEditing(true); setIsDirty(false) }}
            className="bg-white/20 text-white text-sm font-semibold px-4 py-2 rounded-xl hover:bg-white/30 transition-colors"
          >
            Edit
          </button>
        )}
      />
      <div className="p-4 md:p-6 max-w-lg">
        <button onClick={() => navigate(-1)} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
          ← Back
        </button>

        {/* Progress bar */}
        <div className="mb-6">
          <div className="flex justify-between text-xs text-gray-500 mb-1.5">
            <span>{completedSets} of {totalSets} sets done</span>
            <span className="font-semibold text-brand-500">{progress}%</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-500 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        <ExerciseDrawer exercise={selectedExercise} onClose={() => setSelectedExercise(null)} />
        {progressFor && <ExerciseDetailsModal name={progressFor} onClose={() => setProgressFor(null)} />}
        {supersetPickerOpen && session.exercise_day && (
          <SupersetPicker
            dayId={session.exercise_day.id}
            onClose={() => setSupersetPickerOpen(false)}
            onSaved={() => api.workouts.getSession(session.id).then(setSession).catch(() => {})}
          />
        )}
        {session.plan_id && session.exercise_day && (
          <WorkoutChatDrawer
            isOpen={editChatOpen}
            onClose={() => setEditChatOpen(false)}
            planId={session.plan_id}
            dayId={session.exercise_day.id}
            hasUnsavedSets={Object.keys(pendingLogs).length > 0}
            onBeforePlanUpdate={async () => {
              // Applying a plan edit deletes and recreates this day's set rows,
              // so anything still only in local state must reach the server
              // first. This is what used to silently discard every ticked set.
              if (Object.keys(pendingLogs).length > 0) await flushPendingLogs(session)
            }}
            onPlanUpdated={async () => {
              setEditChatOpen(false)
              try {
                const updated = await api.workouts.getSession(session.id)
                setSession(updated)
                // Drafts were flushed above and the rows have new ids; clearing
                // prevents a stale draft overwriting the refetched values.
                setPendingLogs({})
                setIsDirty(false)
                showToast('Workout updated')
              } catch (err) {
                showToast(getErrorMessage(err), 'error')
              }
            }}
          />
        )}

        {exerciseEntries.length > 1 && (
          <div className="flex justify-end items-center gap-3 mb-2">
            {/* Only offered while the session is editable and the day is a
                real plan day — orphaned logs have no day to group. */}
            {!isCompleted && session.exercise_day && (
              <button
                onClick={() => setSupersetPickerOpen(true)}
                className="text-xs font-medium text-brand-500 hover:text-brand-600 transition-colors"
              >
                ⟲ Supersets
              </button>
            )}
            <button
              onClick={() => setAllCards(openCount <= 1)}
              className="text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors"
            >
              {openCount <= 1 ? 'Expand all' : 'Collapse all'}
            </button>
          </div>
        )}

        <div className="space-y-4 mb-8">
          {renderUnits.map((ru) => {
            if (ru.kind === 'superset') {
              const members: SupersetMember[] = ru.entries.map((e) => ({
                planEx: e.planEx as Exercise,
                logs: e.logs,
                measurementType: getMeasurementType(e.planEx, e.logs[0]),
              }))
              const isOpen = ru.key in touchedCards
                ? touchedCards[ru.key]
                : ru.entries.some((e) => e.key === firstUnfinishedKey)
              return (
                <SupersetCard
                  key={ru.key}
                  members={members}
                  unit={unit}
                  isCompleted={isCompleted}
                  isOpen={isOpen}
                  onToggleOpen={() => toggleCard(ru.key, isOpen)}
                  setFieldValue={setFieldValue}
                  isSetCompleted={isSetCompleted}
                  onFieldChange={handleSetField}
                  onToggleSet={handleToggle}
                  onShowProgress={setProgressFor}
                />
              )
            }
            const { key, name: exerciseName, planEx, logs } = ru.entry
            const doneCount = logs.filter((l) => l.is_completed).length
            const allDone = doneCount === logs.length && logs.length > 0
            const mType = getMeasurementType(planEx, logs[0])
            const isOpen = key in touchedCards
              ? touchedCards[key]
              : key === firstUnfinishedKey

            return (
              <div key={key} className={`bg-white rounded-2xl border overflow-hidden transition-colors ${allDone ? 'border-emerald-200' : 'border-gray-100'}`}>
                {/* Exercise header — the whole row toggles the card. The action
                    buttons stop propagation so they still work, which keeps the
                    original layout intact (wrapping the name in a <button>
                    broke the flex sizing and pushed the actions off-screen). */}
                <div
                  onClick={() => toggleCard(key, isOpen)}
                  className={`flex items-center justify-between px-4 py-3 cursor-pointer select-none ${allDone ? 'bg-emerald-50' : 'bg-gray-50/60'}`}
                >
                  <div className="min-w-0 flex items-center gap-2">
                    <span className="text-gray-400 text-[10px] flex-shrink-0 w-2">
                      {isOpen ? '▾' : '▸'}
                    </span>
                    <div className="min-w-0">
                      <p className="font-bold text-gray-900 text-sm truncate">{exerciseName}</p>
                      {planEx && (
                        <p className="text-xs text-gray-400 mt-0.5">
                          {planEx.sets} sets · {planEx.reps}
                          {planEx.rest_seconds ? ` · ${planEx.rest_seconds}s rest` : ''}
                        </p>
                      )}
                    </div>
                  </div>
                  <div
                    className="flex items-center gap-2 flex-shrink-0 ml-3"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {isOpen && mType !== 'warmup' && (
                      <button
                        onClick={() => setProgressFor(exerciseName)}
                        className="text-xs text-gray-500 border border-gray-200 rounded-lg px-2.5 py-1 font-medium hover:bg-gray-50 transition-colors"
                      >
                        Progress
                      </button>
                    )}
                    {isOpen && (
                      <button
                        onClick={() => setSelectedExercise(planEx ?? { id: '', name: exerciseName, sets: 0, reps: '', order: 0, rest_seconds: null, notes: '', measurement_type: mType, composite_id: null, composite_rest_seconds: null })}
                        className="text-xs text-brand-500 border border-brand-200 rounded-lg px-2.5 py-1 font-medium hover:bg-brand-50 transition-colors"
                      >
                        How to
                      </button>
                    )}
                    <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${
                      allDone
                        ? 'bg-emerald-100 text-emerald-700'
                        : doneCount > 0
                          ? 'bg-brand-100 text-brand-600'
                          : 'bg-gray-100 text-gray-500'
                    }`}>
                      {doneCount}/{logs.length}
                    </span>
                    {allDone && <span className="text-emerald-500 font-bold">✓</span>}
                  </div>
                </div>

                {/* Set rows, driven entirely by the measurement-type contract */}
                {isOpen && (() => {
                  // Which inputs to show comes from the shared contract, so the
                  // frontend can never disagree with what the server accepts.
                  const fields = fieldsFor(mType)
                  // A 25-30 min treadmill walk asks for minutes, a 45 sec plank
                  // for seconds. Storage is seconds either way.
                  const timeUnit = timeUnitFor(planEx?.reps)
                  // A carry prescribed "40 yards" must ask for yards, not the
                  // 36.6 metres it is stored as.
                  const distanceUnit = distanceUnitFor(planEx?.reps)

                  if (fields.length === 0) {
                    // Warmup: a single completion tick, no numeric inputs at all.
                    if (!logs.length) {
                      return (
                        <div className="px-4 py-3 text-sm text-gray-400">
                          {planEx?.reps ?? 'No sets logged yet'}
                        </div>
                      )
                    }
                    return (
                      <div className={`flex items-center justify-between px-4 py-3 ${logs[0]?.is_completed ? 'bg-emerald-50/70' : ''}`}>
                        <span className="text-sm text-gray-600">{planEx?.reps ?? '5-10 min'}</span>
                        <button
                          onClick={!isCompleted ? () => handleToggle(logs[0]) : undefined}
                          disabled={isCompleted}
                          className={`w-9 h-9 rounded-full border-2 flex items-center justify-center text-sm font-bold transition-all ${
                            logs[0]?.is_completed
                              ? 'bg-emerald-500 border-emerald-500 text-white shadow-sm'
                              : 'border-gray-300 text-transparent hover:border-brand-400 hover:scale-110'
                          } ${isCompleted ? '' : 'cursor-pointer active:scale-95'}`}
                        >✓</button>
                      </div>
                    )
                  }

                  if (!logs.length) {
                    // An exercise the AI just added, before any sets exist.
                    return (
                      <div className="px-4 py-3 text-sm text-gray-400">
                        No sets logged yet — finish and reopen to log this one.
                      </div>
                    )
                  }

                  const gridCols = fields.length === 2
                    ? 'grid-cols-[2rem_1fr_1fr_2.5rem]'
                    : 'grid-cols-[2rem_1fr_2.5rem]'

                  return (
                    <>
                      {/* The plan sometimes prescribes in a unit the inputs
                          cannot accept — a carry in yards, or burpees in
                          minutes. Say which unit to enter rather than leaving
                          the user to guess. */}
                      {(() => {
                        const expects = prescriptionMismatch(mType, planEx?.reps)
                        if (!expects) return null
                        return (
                          <div className="mx-4 mb-1 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5">
                            Plan says <span className="font-semibold">{planEx?.reps}</span> —
                            log the{' '}
                            <span className="font-semibold">
                              {expects === 'time'
                                ? `time it took you, in ${timeUnit === 'min' ? 'minutes' : 'seconds'}`
                                : 'number of reps you did'}
                            </span>.
                          </div>
                        )
                      })()}
                      <div className={`grid ${gridCols} gap-2 px-4 pt-2 pb-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wide`}>
                        <span>#</span>
                        {fields.map((f) => (
                          <span key={f}>{fieldLabel(f, unit, exerciseName, timeUnit, distanceUnit)}</span>
                        ))}
                        <span />
                      </div>
                      {/* Fill every set from set 1 — typing the same weight and
                          reps for each set is the tedious part. */}
                      {!isCompleted && logs.length > 1 && (
                        <div className="px-4 pb-1 -mt-0.5">
                          <button
                            onClick={() => logs.slice(1).forEach((l) => handleCopyPrevious(l, logs[0], mType))}
                            className="text-[11px] font-semibold text-brand-500 hover:text-brand-600 transition-colors"
                          >
                            ↓ Same as set 1 for all
                          </button>
                        </div>
                      )}
                      <div className="divide-y divide-gray-50 pb-1">
                        {logs.map((log, logIdx) => (
                            <div
                              key={log.id}
                              className={`grid ${gridCols} gap-2 items-center px-4 py-2.5 transition-colors ${log.is_completed ? 'bg-emerald-50/70' : ''}`}
                            >
                              <span className={`text-xs font-bold ${log.is_completed ? 'text-emerald-500' : 'text-gray-400'}`}>
                                {log.set_number}
                              </span>
                              {fields.map((field) => {
                                // Controlled value: pending edit wins over server
                                // state. Previously these were uncontrolled
                                // (defaultValue + stable key), so a refetch after
                                // a plan update never repainted the inputs.
                                const display = toDisplayField(
                                  field, setFieldValue(log, field), unit,
                                  timeUnit, distanceUnit)
                                if (isCompleted) {
                                  // formatDuration takes SECONDS, so read the
                                  // stored value rather than the minutes shown
                                  // in the input.
                                  const stored = setFieldValue(log, field)
                                  return (
                                    <span key={field} className="text-sm text-gray-700 font-medium">
                                      {field === 'duration_seconds' && stored !== null
                                        ? formatDuration(stored)
                                        : display ?? '—'}
                                    </span>
                                  )
                                }
                                return (
                                  <input
                                    key={field}
                                    type="number" min={0}
                                    step={field === 'weight_kg' || (field === 'duration_seconds' && timeUnit === 'min') ? 0.5 : 1}
                                    placeholder="0"
                                    value={display ?? ''}
                                    onChange={(e) => handleSetField(
                                      log, field, e.target.value, timeUnit,
                                      distanceUnit)}
                                    onDoubleClick={() => {
                                      // Double-tap a later set to copy the one above it.
                                      if (logIdx > 0) handleCopyPrevious(log, logs[logIdx - 1], mType)
                                    }}
                                    title={logIdx > 0 ? 'Double-tap to copy the set above' : undefined}
                                    className="border border-gray-200 rounded-xl px-2 py-2 text-sm text-center focus:outline-none focus:ring-2 focus:ring-brand-400 w-full bg-white"
                                  />
                                )
                              })}
                              <button
                                onClick={!isCompleted ? () => handleToggle(log) : undefined}
                                disabled={isCompleted}
                                className={`w-9 h-9 rounded-full border-2 flex items-center justify-center text-sm font-bold transition-all ${
                                  log.is_completed
                                    ? 'bg-emerald-500 border-emerald-500 text-white shadow-sm'
                                    : 'border-gray-300 text-transparent hover:border-brand-400 hover:scale-110'
                                } ${isCompleted ? '' : 'cursor-pointer active:scale-95'}`}
                              >✓</button>
                            </div>
                          ))}
                      </div>
                    </>
                  )
                })()}
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}

// ── Progress Chart ─────────────────────────────────────────────────────────

function LineChart({ points, formatValue }: {
  points: { date: string; value: number }[]
  formatValue?: (v: number) => string
}) {
  if (points.length < 2) return (
    <p className="text-sm text-gray-400 text-center py-8">Log at least 2 sessions to see a trend.</p>
  )

  const W = 480, H = 180, PAD = { top: 16, right: 16, bottom: 32, left: 48 }
  const values = points.map((p) => p.value)
  const minV = Math.min(...values)
  const maxV = Math.max(...values)
  const range = maxV - minV || 1

  const x = (i: number) => PAD.left + (i / (points.length - 1)) * (W - PAD.left - PAD.right)
  const y = (v: number) => PAD.top + (1 - (v - minV) / range) * (H - PAD.top - PAD.bottom)

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(p.value)}`).join(' ')
  const fmt = formatValue ?? ((v: number) => v.toFixed(1))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {[0, 0.5, 1].map((t) => {
        const yPos = PAD.top + t * (H - PAD.top - PAD.bottom)
        const val = maxV - t * range
        return (
          <g key={t}>
            <line x1={PAD.left} y1={yPos} x2={W - PAD.right} y2={yPos} stroke="#f3f4f6" strokeWidth="1" />
            <text x={PAD.left - 4} y={yPos + 4} textAnchor="end" fontSize="10" fill="#9ca3af">{fmt(val)}</text>
          </g>
        )
      })}
      <path d={pathD} fill="none" stroke="#be123c" strokeWidth="2.5" strokeLinejoin="round" />
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(p.value)} r="4" fill="#be123c" />
          <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="9" fill="#9ca3af">
            {new Date(p.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
          </text>
        </g>
      ))}
    </svg>
  )
}

function ExerciseDetailsModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { user } = useAuth()
  const unit: WeightUnit = (user?.profile?.preferred_unit ?? 'lb') as WeightUnit

  const [history, setHistory] = useState<ExerciseHistory | null>(null)
  const [loading, setLoading] = useState(true)
  // How many SESSIONS of detail to list. 0 means all.
  const [sessionLimit, setSessionLimit] = useState(3)

  useEffect(() => {
    setLoading(true)
    api.workouts.getExerciseHistory(name)
      .then(setHistory)
      .catch(() => setHistory(null))
      .finally(() => setLoading(false))
  }, [name])

  const logs = history?.logs ?? []
  // The server tells us the type and which field drives the chart, so seconds
  // can never be plotted as pounds again (a 180s plank used to show as a
  // 396.8 lb personal best).
  const mType = history?.measurement_type ?? 'weight_reps'
  const spec = chartSpecFor(mType)
  const metric = history?.metric ?? spec.metric

  /** Best value per session date, in storage units. */
  const chartPoints = (() => {
    if (!metric) return []
    const byDate = new Map<string, number>()
    for (const p of logs) {
      const date = p.workout_session__started_at.split('T')[0]
      const raw = p[metric]
      if (raw === null || raw === undefined) continue
      if (raw > (byDate.get(date) ?? -Infinity)) byDate.set(date, raw)
    }
    return Array.from(byDate.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, stored]) => ({
        date,
        // Convert to display units only for mass; seconds and reps pass through.
        value: spec.unit === 'mass' ? (toDisplayField('weight_kg', stored, unit) ?? 0) : stored,
      }))
  })()

  /**
   * Logged sets grouped by session, NEWEST FIRST.
   *
   * The table used to render `logs.slice(0, 30)` straight off the response.
   * The endpoint orders ascending by date, so that showed the OLDEST 30 sets
   * and silently hid everything recent: with 42 sets of Dumbbell Lateral
   * Raises across 14 sessions, the four most recent sessions were invisible.
   * Reported as "I don't see full scroll for excercises in progress with a lot
   * of sessions already done".
   */
  const sessions = (() => {
    const byDate = new Map<string, typeof logs>()
    for (const p of logs) {
      const date = p.workout_session__started_at.split('T')[0]
      const bucket = byDate.get(date)
      if (bucket) bucket.push(p)
      else byDate.set(date, [p])
    }
    return Array.from(byDate.entries())
      .sort(([a], [b]) => b.localeCompare(a))
      .map(([date, rows]) => ({
        date,
        rows: [...rows].sort((x, y) => x.set_number - y.set_number),
      }))
  })()

  const visibleSessions = sessionLimit === 0
    ? sessions : sessions.slice(0, sessionLimit)

  // Personal best uses the metric for this type, not weight_kg unconditionally.
  const pb = metric
    ? logs.reduce<number | null>((max, p) => {
        const raw = p[metric]
        return raw !== null && raw !== undefined ? Math.max(max ?? 0, raw) : max
      }, null)
    : null

  const axisFormat = spec.unit === 'seconds'
    ? (v: number) => formatDuration(Math.round(v))
    : spec.unit === 'reps'
      ? (v: number) => String(Math.round(v))
      : undefined

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pb-20 sm:pb-4 bg-black/50" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg flex flex-col max-h-[80vh] sm:max-h-[90vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <h2 className="font-bold text-gray-900 text-base">{name}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">✕</button>
        </div>
        <div className="overflow-y-auto flex-1 p-5">
          {loading ? (
            <SkeletonText lines={6} />
          ) : logs.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">No data logged yet.</p>
          ) : (
            <>
              {pb != null && (
                <div className="mb-4 bg-emerald-50 rounded-xl px-4 py-3 flex items-center justify-between">
                  <span className="text-sm font-medium text-emerald-700">
                    {spec.unit === 'seconds' ? 'Longest hold' : spec.unit === 'reps' ? 'Most reps' : 'Personal best'}
                  </span>
                  <span className="font-bold text-emerald-700">
                    {formatChartValue(mType, pb, unit)}
                  </span>
                </div>
              )}
              <div className="mb-4">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                  {chartLabel(mType, unit)}
                </p>
                <LineChart points={chartPoints} formatValue={axisFormat} />
              </div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                  Recent sessions
                </p>
                {/* Default to the last 3 so a long history is scannable, with
                    a way to widen it. The chart and PB above always use the
                    FULL history — only this detail list is filtered. */}
                <select
                  value={sessionLimit}
                  onChange={(e) => setSessionLimit(Number(e.target.value))}
                  className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-400"
                >
                  <option value={3}>Last 3</option>
                  <option value={5}>Last 5</option>
                  <option value={10}>Last 10</option>
                  <option value={0}>All {sessions.length}</option>
                </select>
              </div>
              <div className="bg-gray-50 rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="text-left px-4 py-2.5 text-xs font-semibold text-gray-400">Set</th>
                      {/* Columns follow the measurement type instead of always
                          showing Weight/Reps. */}
                      {fieldsFor(mType).map((f) => (
                        <th key={f} className="text-right px-4 py-2.5 text-xs font-semibold text-gray-400">
                          {fieldLabel(f, unit, name)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  {visibleSessions.map((s) => (
                    <tbody key={s.date}>
                      {/* One date header per session, so the sets underneath
                          read as a workout rather than an undifferentiated
                          wall of rows. */}
                      <tr className="bg-gray-100/80">
                        <td colSpan={1 + fieldsFor(mType).length}
                            className="px-4 py-1.5 text-[11px] font-semibold text-gray-500">
                          {new Date(s.date + 'T00:00:00').toLocaleDateString('en-US', {
                            weekday: 'short', month: 'short', day: 'numeric',
                          })}
                          <span className="font-normal text-gray-400">
                            {' · '}{s.rows.length} set{s.rows.length === 1 ? '' : 's'}
                          </span>
                        </td>
                      </tr>
                      {s.rows.map((p, i) => (
                        <tr key={i} className="border-b border-gray-100 last:border-0">
                          <td className="px-4 py-2 text-gray-500 text-xs">{p.set_number}</td>
                          {fieldsFor(mType).map((f) => {
                            const raw = p[f]
                            return (
                              <td key={f} className="px-4 py-2 text-right font-semibold text-gray-900">
                                {raw === null || raw === undefined
                                  ? '—'
                                  : f === 'weight_kg'
                                    ? `${toDisplayField(f, raw, unit)}${unit}`
                                    : f === 'duration_seconds'
                                      ? formatDuration(raw)
                                      : raw}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  ))}
                </table>
              </div>
              {sessionLimit !== 0 && sessions.length > visibleSessions.length && (
                <button
                  onClick={() => setSessionLimit(0)}
                  className="mt-2 w-full text-xs font-semibold text-brand-500 hover:text-brand-600 py-2 transition-colors"
                >
                  Show all {sessions.length} sessions
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ProgressTab() {
  const { user } = useAuth()
  const unit: WeightUnit = (user?.profile?.preferred_unit ?? 'lb') as WeightUnit

  const [allExercises, setAllExercises] = useState<import('../lib/api').ExerciseSummary[]>([])
  const [search, setSearch] = useState('')
  const [detailsFor, setDetailsFor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.workouts.listExercises()
      .then(setAllExercises)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const filtered = allExercises.filter((ex) =>
    ex.exercise_name.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="p-4 md:p-6 max-w-2xl">
      {detailsFor && <ExerciseDetailsModal name={detailsFor} onClose={() => setDetailsFor(null)} />}

      <div className="mb-4">
        <input
          placeholder="Search exercises..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
      </div>

      {loading ? (
        <SkeletonText lines={8} />
      ) : filtered.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-10">
          {allExercises.length === 0 ? 'No exercises logged yet. Complete a workout to see data here.' : 'No matches.'}
        </p>
      ) : (
        <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400">Exercise</th>
                <th className="text-right px-4 py-3 text-xs font-semibold text-gray-400">Best</th>
                <th className="text-right px-4 py-3 text-xs font-semibold text-gray-400">Sessions</th>
                <th className="text-right px-4 py-3 text-xs font-semibold text-gray-400" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((ex) => (
                <tr key={ex.exercise_name} className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-gray-900 text-sm">{ex.exercise_name}</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {new Date(ex.last_session).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-right font-semibold text-gray-900">
                    {/* Best value in the unit that matches this exercise's type.
                        Previously always Max(weight_kg), so a plank's seconds
                        were shown as a weight. */}
                    {(() => {
                      const metric = chartSpecFor(ex.measurement_type).metric
                      const best = metric === 'weight_kg' ? ex.best_weight_kg
                        : metric === 'duration_seconds' ? ex.best_duration_seconds
                        : metric === 'reps_completed' ? ex.best_reps
                        : null
                      return best === null || best === undefined
                        ? '—'
                        : formatChartValue(ex.measurement_type, best, unit)
                    })()}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-500">{ex.total_sessions}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => setDetailsFor(ex.exercise_name)}
                      className="text-xs text-brand-500 border border-brand-200 rounded-lg px-2.5 py-1 hover:bg-brand-50 transition-colors"
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
