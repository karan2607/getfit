import { useEffect, useState } from 'react'
import { api, type WorkoutPlanPreview, type DietPlanPreview } from '../../lib/api'
import { useToast } from '../Toast'
import { getErrorMessage } from '../../lib/errors'

interface Props {
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
  planId?: string
  dietPlanId?: string
  onSaved?: () => void
  onDietSaved?: () => void
  onPreviewUpdate?: (plan: WorkoutPlanPreview) => void
}

const THINKING_MESSAGES = [
  'Warming up...',
  'Checking your reps...',
  'Counting sets...',
  'Designing your workout...',
  'Loading the barbell...',
  'Chalking up...',
  'Studying your form...',
  'Plotting your gains...',
  'Reviewing your macros...',
  'Spotting you...',
]

function ThinkingCard() {
  const [idx, setIdx] = useState(0)

  useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % THINKING_MESSAGES.length), 2000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="mt-2 bg-brand-50 border border-brand-100 rounded-xl px-4 py-4 space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex gap-1">
          <span className="w-2 h-2 rounded-full bg-brand-400 animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-2 h-2 rounded-full bg-brand-400 animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-2 h-2 rounded-full bg-brand-400 animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        <span className="text-xs text-brand-500 font-medium transition-all">{THINKING_MESSAGES[idx]}</span>
      </div>
      <div className="space-y-2">
        <div className="h-3 bg-brand-100 rounded-full w-3/4 animate-pulse" />
        <div className="h-3 bg-brand-100 rounded-full w-1/2 animate-pulse" />
        <div className="h-3 bg-brand-100 rounded-full w-2/3 animate-pulse" />
      </div>
    </div>
  )
}

function PlanPreviewModal({ plan, onClose }: { plan: WorkoutPlanPreview; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 animate-fade-in">
      <div className="bg-white rounded-2xl w-full max-w-lg max-h-[80vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <p className="font-bold text-gray-900">{plan.title}</p>
            {plan.description && <p className="text-xs text-gray-500 mt-0.5">{plan.description}</p>}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none transition-colors">
            ✕
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4 space-y-4">
          {plan.days.map((day) => (
            <div key={day.day_number}>
              <div className="flex items-center gap-2 mb-2">
                <span className="w-6 h-6 rounded-full bg-brand-100 text-brand-600 text-xs font-bold flex items-center justify-center flex-shrink-0">
                  {day.day_number}
                </span>
                <p className="font-semibold text-sm text-gray-900">{day.name}</p>
                {day.focus && (
                  <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">{day.focus}</span>
                )}
              </div>
              {day.is_rest_day ? (
                <p className="text-xs text-gray-400 pl-8">Rest day</p>
              ) : (
                <div className="pl-8 space-y-1">
                  {day.exercises.map((ex, i) => (
                    <div key={i} className="flex items-center justify-between text-xs text-gray-600">
                      <span>{ex.name}</span>
                      <span className="text-gray-400 ml-2 whitespace-nowrap">{ex.sets} × {ex.reps}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function InlinePlanCard({
  plan, planId, onSaved, onPreviewUpdate,
}: {
  plan: WorkoutPlanPreview
  planId?: string
  onSaved?: () => void
  onPreviewUpdate?: (plan: WorkoutPlanPreview) => void
}) {
  const { showToast } = useToast()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)

  async function handleSave() {
    if (onPreviewUpdate) {
      onPreviewUpdate(plan)
      return
    }
    setSaving(true)
    try {
      if (planId) {
        await api.workouts.replacePlan(planId, plan)
        setSaved(true)
        showToast('Plan updated!')
        onSaved?.()
      } else {
        await api.workouts.savePlan(plan)
        setSaved(true)
        showToast('Plan saved to Workouts!')
      }
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  const workoutDays = plan.days.filter((d) => !d.is_rest_day)

  return (
    <>
      <div className="mt-3 bg-emerald-50 border border-emerald-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 flex items-center justify-between">
          <div>
            <p className="font-semibold text-gray-900 text-sm">{plan.title}</p>
            <p className="text-xs text-gray-500">
              {workoutDays.length} workout days · {plan.duration_weeks ?? '?'} weeks
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPreviewOpen(true)}
              className="text-xs text-emerald-700 hover:text-emerald-900 font-medium px-2 py-1 rounded-lg hover:bg-emerald-100 transition-colors"
            >
              Preview
            </button>
            <button
              onClick={handleSave}
              disabled={saving || saved}
              className="text-xs bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
            >
              {saved ? '✓ Applied' : saving ? 'Saving...' : onPreviewUpdate ? 'Use this plan' : planId ? 'Update plan' : 'Save plan'}
            </button>
          </div>
        </div>
      </div>
      {previewOpen && <PlanPreviewModal plan={plan} onClose={() => setPreviewOpen(false)} />}
    </>
  )
}

function InlineDietPlanCard({
  plan, dietPlanId, onSaved,
}: {
  plan: DietPlanPreview
  dietPlanId?: string
  onSaved?: () => void
}) {
  const { showToast } = useToast()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function handleSave() {
    setSaving(true)
    try {
      if (dietPlanId) {
        await api.diet.deletePlan(dietPlanId)
      }
      await api.diet.savePlan(plan)
      setSaved(true)
      showToast(dietPlanId ? 'Diet plan updated!' : 'Diet plan saved!')
      onSaved?.()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  const numDays = plan.meals.reduce((max, m) => Math.max(max, m.day_number ?? 1), 1)

  return (
    <div className="mt-3 bg-blue-50 border border-blue-200 rounded-xl overflow-hidden">
      <div className="px-4 py-3 flex items-center justify-between">
        <div>
          <p className="font-semibold text-gray-900 text-sm">{plan.title}</p>
          <p className="text-xs text-gray-500">
            {numDays}-day plan · {plan.target_calories} kcal/day
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving || saved}
          className="text-xs bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-medium px-3 py-1.5 rounded-lg transition-colors"
        >
          {saved ? '✓ Saved' : saving ? 'Saving...' : dietPlanId ? 'Update plan' : 'Save plan'}
        </button>
      </div>
    </div>
  )
}

function parseWorkoutPlan(content: string): { text: string; plan: WorkoutPlanPreview | null } {
  const match = content.match(/```workout-plan\n([\s\S]*?)\n```/)
  if (!match) return { text: content, plan: null }
  try {
    const plan = JSON.parse(match[1])
    const text = content.replace(/```workout-plan\n[\s\S]*?\n```/, '').trim()
    return { text, plan }
  } catch {
    return { text: content, plan: null }
  }
}

function parseDietPlan(content: string): { text: string; plan: DietPlanPreview | null } {
  const match = content.match(/```diet-plan\n([\s\S]*?)\n```/)
  if (!match) return { text: content, plan: null }
  try {
    const plan = JSON.parse(match[1])
    const text = content.replace(/```diet-plan\n[\s\S]*?\n```/, '').trim()
    return { text, plan }
  } catch {
    return { text: content, plan: null }
  }
}

export default function ChatBubble({ role, content, isStreaming, planId, dietPlanId, onSaved, onDietSaved, onPreviewUpdate }: Props) {
  const isUser = role === 'user'

  // While streaming, if a plan block is being built, show thinking card instead of raw JSON
  const isGeneratingWorkoutPlan = !isUser && isStreaming && content.includes('```workout-plan')
  const isGeneratingDietPlan = !isUser && isStreaming && content.includes('```diet-plan')
  const isGeneratingPlan = isGeneratingWorkoutPlan || isGeneratingDietPlan

  const planBlockStart = isGeneratingWorkoutPlan
    ? content.indexOf('```workout-plan')
    : isGeneratingDietPlan
      ? content.indexOf('```diet-plan')
      : -1

  const { text: workoutText, plan: workoutPlan } = isUser || isGeneratingPlan
    ? { text: isGeneratingPlan && planBlockStart >= 0 ? content.slice(0, planBlockStart).trim() : content, plan: null }
    : parseWorkoutPlan(content)

  const { text, plan: dietPlan } = !isUser && !isGeneratingPlan && !workoutPlan
    ? parseDietPlan(workoutText ?? content)
    : { text: workoutText, plan: null }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-brand-500 flex items-center justify-center text-white text-sm font-bold mr-2 flex-shrink-0 mt-0.5">
          AI
        </div>
      )}
      <div className={`max-w-[75%] ${isUser ? '' : 'w-full'}`}>
        {(text || (!isGeneratingPlan && isStreaming)) && (
          <div
            className={`rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
              isUser
                ? 'bg-brand-500 text-white rounded-br-sm'
                : 'bg-white border border-gray-200 text-gray-800 rounded-bl-sm shadow-sm'
            }`}
          >
            {text}
            {isStreaming && !isGeneratingPlan && (
              <span className="inline-block w-1.5 h-4 bg-current opacity-70 animate-pulse ml-0.5 align-text-bottom" />
            )}
          </div>
        )}
        {isGeneratingPlan && <ThinkingCard />}
        {workoutPlan && !isStreaming && <InlinePlanCard plan={workoutPlan} planId={planId} onSaved={onSaved} onPreviewUpdate={onPreviewUpdate} />}
        {dietPlan && !isStreaming && <InlineDietPlanCard plan={dietPlan} dietPlanId={dietPlanId} onSaved={onDietSaved} />}
      </div>
    </div>
  )
}
