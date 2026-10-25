import { useState, useEffect } from 'react'
import { api, type HealthDailySummary } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'

/**
 * Correct a day that synced wrong.
 *
 * Reported as "Sometimes wrong data is being sent which needs correction."
 * Before this a bad number could be overwritten by another sync but never
 * removed, and a day that should not exist could not be deleted at all.
 *
 * The important behaviour: **blanking a field clears it.** That is the whole
 * point, and it is why this uses a different endpoint from sync — sync ignores
 * nulls so a partial Shortcut payload cannot wipe values it simply did not
 * include.
 */

const FIELDS: Array<{
  key: keyof Pick<HealthDailySummary,
    'steps' | 'active_calories' | 'resting_calories' | 'resting_heart_rate' | 'sleep_hours'>
  label: string
  step: number
}> = [
  { key: 'steps', label: 'Steps', step: 1 },
  { key: 'active_calories', label: 'Active kcal', step: 1 },
  { key: 'resting_calories', label: 'Resting kcal', step: 1 },
  { key: 'resting_heart_rate', label: 'Resting HR', step: 1 },
  { key: 'sleep_hours', label: 'Sleep (h)', step: 0.1 },
]

function toInput(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

export default function DailyEditor({ summaries, onChanged }: {
  summaries: HealthDailySummary[]
  onChanged: () => void
}) {
  const { showToast } = useToast()
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)

  const day = summaries.find((s) => s.date === editing) ?? null

  useEffect(() => {
    if (!day) return
    setDraft(Object.fromEntries(
      FIELDS.map((f) => [f.key, toInput(day[f.key])])))
  }, [editing]) // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    if (!day) return
    // Only send what actually changed. A blank input becomes an explicit null,
    // which is what clears the stored value.
    const patch: Record<string, number | null> = {}
    for (const field of FIELDS) {
      const before = toInput(day[field.key])
      const after = (draft[field.key] ?? '').trim()
      if (before === after) continue
      patch[field.key] = after === '' ? null : Number(after)
    }
    if (Object.keys(patch).length === 0) {
      setEditing(null)
      return
    }
    setBusy(true)
    try {
      const res = await api.health.updateSummary(day.date, patch)
      showToast(res.cleared.length
        ? `Updated — cleared ${res.cleared.join(', ')}`
        : 'Updated')
      setEditing(null)
      onChanged()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  async function removeDay(date: string) {
    if (!window.confirm(
      `Delete all health data for ${date}? This cannot be undone.`)) return
    setBusy(true)
    try {
      await api.health.deleteSummary(date)
      showToast(`Deleted ${date}`)
      setEditing(null)
      onChanged()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  if (summaries.length === 0) return null

  return (
    <div className="mb-6">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
        Daily data
      </p>
      <p className="text-[11px] text-gray-400 mb-3">
        Tap a day to correct it. Clearing a box removes that value.
      </p>

      <div className="bg-white rounded-2xl border border-gray-100 divide-y divide-gray-50 overflow-hidden">
        {summaries.slice(0, 14).map((s) => {
          const isEditing = editing === s.date
          return (
            <div key={s.date} className={isEditing ? 'bg-gray-50/70' : ''}>
              <button
                onClick={() => setEditing(isEditing ? null : s.date)}
                className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-gray-50 transition-colors"
              >
                <span className="text-sm font-medium text-gray-900 flex-shrink-0">
                  {new Date(s.date + 'T00:00:00').toLocaleDateString('en-US', {
                    weekday: 'short', month: 'short', day: 'numeric',
                  })}
                </span>
                <span className="text-[11px] text-gray-400 truncate">
                  {s.steps != null && `${s.steps.toLocaleString()} steps`}
                  {s.sleep_hours != null && ` · ${s.sleep_hours}h sleep`}
                  {s.resting_heart_rate != null && ` · ${Math.round(s.resting_heart_rate)} bpm`}
                </span>
                <span className="text-gray-300 text-xs flex-shrink-0">
                  {isEditing ? '▾' : '▸'}
                </span>
              </button>

              {isEditing && (
                <div className="px-4 pb-3">
                  <div className="grid grid-cols-2 gap-2 mb-3">
                    {FIELDS.map((field) => (
                      <div key={field.key}>
                        <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                          {field.label}
                        </label>
                        <input
                          type="number" min={0} step={field.step}
                          value={draft[field.key] ?? ''}
                          onChange={(e) => setDraft((prev) => ({
                            ...prev, [field.key]: e.target.value }))}
                          placeholder="—"
                          className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 bg-white"
                        />
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={save}
                      disabled={busy}
                      className="text-sm font-semibold px-4 py-2 rounded-xl bg-brand-500 text-white hover:bg-brand-600 disabled:opacity-40 transition-colors"
                    >
                      {busy ? 'Saving…' : 'Save'}
                    </button>
                    <button
                      onClick={() => setEditing(null)}
                      className="text-sm font-medium px-3 py-2 rounded-xl text-gray-500 hover:bg-gray-100 transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => removeDay(s.date)}
                      disabled={busy}
                      className="ml-auto text-sm font-semibold px-3 py-2 rounded-xl text-red-600 hover:bg-red-50 disabled:opacity-40 transition-colors"
                    >
                      Delete day
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
