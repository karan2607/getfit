import { useEffect, useState } from 'react'
import { api, type WorkoutPlanPreview } from '../../lib/api'
import { useToast } from '../Toast'
import { getErrorMessage } from '../../lib/errors'

interface Props {
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
  planId?: string
  onSaved?: () => void
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
  const [expanded, setExpanded] = useState(false)

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
            onClick={() => setExpanded((v) => !v)}
            className="text-xs text-emerald-700 hover:text-emerald-900 font-medium px-2 py-1 rounded-lg hover:bg-emerald-100 transition-colors"
          >
            {expanded ? 'Hide' : 'Preview'}
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

      {expanded && (
        <div className="border-t border-emerald-200 px-4 py-3 space-y-2">
          {plan.days.map((day) => (
            <div key={day.day_number} className="flex items-start gap-2 text-xs">
              <span className="w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 font-bold flex items-center justify-center flex-shrink-0 mt-0.5">
                {day.day_number}
              </span>
              <div>
                <p className="font-medium text-gray-800">{day.name}</p>
                {day.is_rest_day ? (
                  <p className="text-gray-400">Rest day</p>
                ) : (
                  <p className="text-gray-500">
                    {day.exercises.slice(0, 3).map((e) => e.name).join(', ')}
                    {day.exercises.length > 3 ? ` +${day.exercises.length - 3}` : ''}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
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

export default function ChatBubble({ role, content, isStreaming, planId, onSaved, onPreviewUpdate }: Props) {
  const isUser = role === 'user'

  // While streaming, if a workout-plan block is being built, show thinking card instead of raw JSON
  const isGeneratingPlan = !isUser && isStreaming && content.includes('```workout-plan')

  const { text, plan } = isUser || isGeneratingPlan
    ? { text: isGeneratingPlan ? content.slice(0, content.indexOf('```workout-plan')).trim() : content, plan: null }
    : parseWorkoutPlan(content)

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
        {plan && !isStreaming && <InlinePlanCard plan={plan} planId={planId} onSaved={onSaved} onPreviewUpdate={onPreviewUpdate} />}
      </div>
    </div>
  )
}
