import { useEffect, useState } from 'react'
import { api, type PlanTemplate } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'

/**
 * Starting a plan without the AI.
 *
 * Generation was the only route to a plan, so when it failed the app was a
 * dead end: onboarding finished, the dashboard said "no active plan", and
 * every other screen needed one. For the whole of a billing lapse that was
 * the entire product for a new user.
 *
 * A template is not a worse plan -- it is a plan that does not depend on a
 * third party being up.
 */
export default function TemplatePicker({
  reason,
  onCreated,
}: {
  /** Why the user is seeing this, when they did not go looking for it. */
  reason?: string
  onCreated: (planId: string) => void
}) {
  const { showToast } = useToast()
  const [templates, setTemplates] = useState<PlanTemplate[] | null>(null)
  const [creating, setCreating] = useState<string | null>(null)

  useEffect(() => {
    api.workouts.listTemplates()
      .then((res) => setTemplates(res.templates))
      .catch(() => setTemplates([]))
  }, [])

  async function choose(key: string) {
    setCreating(key)
    try {
      const plan = await api.workouts.createFromTemplate(key)
      onCreated(plan.id)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setCreating(null)
    }
  }

  if (templates === null) return null
  if (templates.length === 0) return null

  return (
    <div>
      {reason && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-sm text-amber-800">{reason}</p>
        </div>
      )}

      <div className="space-y-3">
        {templates.map((t) => (
          <div key={t.key} className="rounded-2xl border border-gray-200 bg-white p-4">
            <p className="text-base font-semibold text-ink-600">{t.name}</p>
            <p className="text-sm text-gray-500 mt-0.5">{t.summary}</p>
            <p className="text-sm text-gray-500 mt-2">{t.description}</p>

            {/* Say so rather than silently handing back a different plan
                than the one named. */}
            {t.adjusted && (
              <p className="text-xs text-amber-700 mt-2">
                Adjusted to avoid exercises you've ruled out.
              </p>
            )}

            <button
              type="button"
              onClick={() => choose(t.key)}
              disabled={creating !== null}
              className="mt-3 inline-flex items-center justify-center min-h-[44px] px-5 rounded-full bg-accent-500 hover:bg-accent-600 disabled:opacity-50 text-white text-sm font-medium"
            >
              {creating === t.key ? 'Creating…' : 'Use this template'}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
