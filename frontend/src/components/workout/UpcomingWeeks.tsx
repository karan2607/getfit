import { useState } from 'react'
import { api, type WorkoutPlanDetail, type ProgramPhase } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'

/**
 * The weeks that have not been generated yet.
 *
 * Plans are built one week at a time. An 8-week plan in a single AI call is
 * ~25,000 output tokens, and at that size the response gets truncated at the
 * model's output ceiling — four models produced unparseable JSON in 2 of 14
 * attempts. Week-sized calls are ~3,000 tokens.
 *
 * The better reason: a week generated now can be written against what you
 * actually lifted. Weeks 2+ used to be guessed at plan-creation time, before a
 * single session existed.
 *
 * Normally nobody sees the button — finishing the last session of a week
 * auto-starts the next one. This is the override, and the only place to pass a
 * note to the AI ("knee felt off on squats").
 */

function phaseFor(outline: ProgramPhase[] | null, week: number) {
  if (!outline) return null
  return outline.find((p) => p.start_week <= week && week <= p.end_week) ?? null
}

export default function UpcomingWeeks({ plan, onChanged }: {
  plan: WorkoutPlanDetail
  onChanged: () => void
}) {
  const { showToast } = useToast()
  const [noteFor, setNoteFor] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const duration = plan.duration_weeks ?? 1
  const ready = plan.weeks_ready ?? 0
  // Legacy plans have every week materialised, so there is nothing to show.
  if (ready >= duration) return null

  const upcoming: number[] = []
  for (let w = ready + 1; w <= duration; w++) upcoming.push(w)

  const next = upcoming[0]
  const preparing = plan.is_preparing ? plan.preparing_week : null

  async function prepare(week: number) {
    setBusy(true)
    try {
      const res = await api.workouts.prepareNextWeek(plan.id, week, note)
      showToast(res.started ? `Week ${week} is preparing…` : res.detail)
      setNoteFor(null)
      setNote('')
      onChanged()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-8">
      <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3">
        Coming up
      </p>

      {plan.prepare_error && (
        <div className="mb-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded-xl px-3 py-2 flex items-center justify-between gap-3">
          <span>Week {next} didn't generate: {plan.prepare_error}</span>
          <button
            onClick={() => prepare(next)}
            disabled={busy}
            className="font-semibold text-red-700 underline flex-shrink-0 disabled:opacity-50"
          >
            Try again
          </button>
        </div>
      )}

      <div className="space-y-2">
        {upcoming.map((week) => {
          const phase = phaseFor(plan.program_outline, week)
          const isPreparing = preparing === week
          const isNext = week === next

          return (
            <div
              key={week}
              className={`rounded-xl border px-4 py-3 ${isPreparing ? 'border-brand-200 bg-brand-50/50' : 'border-gray-100 bg-white'}`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-gray-900">
                    Week {week}
                    {phase && (
                      <span className="font-normal text-gray-400"> · {phase.name}</span>
                    )}
                  </p>
                  {/* The phase focus is what makes an unbuilt week meaningful
                      rather than just a gap in the list. */}
                  {phase?.focus && (
                    <p className="text-[11px] text-gray-400 mt-0.5">{phase.focus}</p>
                  )}
                  {isPreparing && (
                    <p className="text-[11px] text-brand-600 font-medium mt-1">
                      Preparing now — this page updates when it's ready.
                    </p>
                  )}
                  {!isPreparing && !isNext && (
                    <p className="text-[11px] text-gray-400 mt-1">
                      Prepares after week {week - 1}
                    </p>
                  )}
                  {!isPreparing && isNext && (
                    <p className="text-[11px] text-gray-400 mt-1">
                      Prepares automatically when you finish week {week - 1}
                    </p>
                  )}
                </div>

                {isNext && !isPreparing && (
                  <button
                    onClick={() => setNoteFor(noteFor === week ? null : week)}
                    className="text-xs font-semibold text-brand-500 hover:text-brand-600 flex-shrink-0 transition-colors"
                  >
                    {noteFor === week ? 'Cancel' : 'Prepare now'}
                  </button>
                )}
              </div>

              {/* Free text is the highest-value input available and previously
                  had nowhere to go. Optional — Skip generates without it. */}
              {noteFor === week && (
                <div className="mt-3 pt-3 border-t border-gray-100">
                  <label className="block text-[11px] font-semibold text-gray-500 mb-1.5">
                    Anything I should know? (optional)
                  </label>
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    maxLength={600}
                    placeholder="e.g. knee felt off on squats, bench felt easy, travelling Thursday"
                    className="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 resize-none"
                  />
                  <div className="flex gap-2 mt-2">
                    <button
                      onClick={() => prepare(week)}
                      disabled={busy}
                      className="text-sm font-semibold px-4 py-2 rounded-xl bg-brand-500 text-white hover:bg-brand-600 disabled:opacity-40 transition-colors"
                    >
                      {busy ? 'Starting…' : `Prepare week ${week}`}
                    </button>
                    <button
                      onClick={() => { setNote(''); prepare(week) }}
                      disabled={busy}
                      className="text-sm font-medium px-3 py-2 rounded-xl text-gray-500 hover:bg-gray-50 disabled:opacity-40 transition-colors"
                    >
                      Skip
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
