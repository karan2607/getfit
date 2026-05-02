import { useState } from 'react'
import { api, type WorkoutPlanPreview } from '../../lib/api'
import { useToast } from '../Toast'
import { getErrorMessage } from '../../lib/errors'

interface Props {
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
}

function InlinePlanCard({ plan }: { plan: WorkoutPlanPreview }) {
  const { showToast } = useToast()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [expanded, setExpanded] = useState(false)

  async function handleSave() {
    setSaving(true)
    try {
      await api.workouts.savePlan(plan)
      setSaved(true)
      showToast('Plan saved to Workouts!')
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
            {saved ? '✓ Saved' : saving ? 'Saving...' : 'Save plan'}
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
                  <p className="text-gray-500">{day.exercises.slice(0, 3).map((e) => e.name).join(', ')}{day.exercises.length > 3 ? ` +${day.exercises.length - 3}` : ''}</p>
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

export default function ChatBubble({ role, content, isStreaming }: Props) {
  const isUser = role === 'user'
  const { text, plan } = isUser ? { text: content, plan: null } : parseWorkoutPlan(content)

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center text-white text-sm font-bold mr-2 flex-shrink-0 mt-0.5">
          AI
        </div>
      )}
      <div className={`max-w-[75%] ${isUser ? '' : 'w-full'}`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
            isUser
              ? 'bg-emerald-600 text-white rounded-br-sm'
              : 'bg-white border border-gray-200 text-gray-800 rounded-bl-sm shadow-sm'
          }`}
        >
          {text}
          {isStreaming && (
            <span className="inline-block w-1.5 h-4 bg-current opacity-70 animate-pulse ml-0.5 align-text-bottom" />
          )}
        </div>
        {plan && !isStreaming && <InlinePlanCard plan={plan} />}
      </div>
    </div>
  )
}
