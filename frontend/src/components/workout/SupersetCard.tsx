import type { Exercise, SetLog, MeasurementType } from '../../lib/api'
import {
  type SetField, type WeightUnit, type TimeUnit,
  fieldsFor, fieldLabel, toDisplayField, formatDuration, timeUnitFor,
} from '../../lib/units'
import { compositeLabel, compositeRounds, compositeRest } from '../../lib/composites'

/**
 * A superset, rendered round by round.
 *
 * A superset is N rounds of every member done back to back, so the rows are
 * grouped by ROUND rather than by exercise — that is the order the work
 * actually happens in, and it makes "set 2 of the row" findable without
 * scrolling between two cards.
 *
 * Why this is a separate component rather than a mode of the existing card:
 * members have DIFFERENT column sets (a bench press takes weight + reps, a
 * push-up takes reps only), so the single shared grid the standalone renderer
 * uses cannot express it. Keeping them apart means a plan with no supersets
 * renders through completely untouched code — which matters, because this is
 * the area that produced the draft-key bug and the weight/duration conversion
 * bug.
 *
 * Every value goes through the same helpers as the standalone card
 * (`fieldsFor`, `toDisplayField`, `fieldLabel`, `timeUnitFor`) and the same
 * save path (`onFieldChange` -> `handleSetField`), so there is no second
 * notion of how a set is read or written.
 */

export interface SupersetMember {
  planEx: Exercise
  logs: SetLog[]
  measurementType: MeasurementType
}

export default function SupersetCard({
  members, unit, isCompleted, isOpen, onToggleOpen,
  setFieldValue, isSetCompleted, onFieldChange, onToggleSet, onShowProgress,
}: {
  members: SupersetMember[]
  unit: WeightUnit
  isCompleted: boolean
  isOpen: boolean
  onToggleOpen: () => void
  setFieldValue: (log: SetLog, field: SetField) => number | null
  isSetCompleted: (log: SetLog) => boolean
  onFieldChange: (log: SetLog, field: SetField, raw: string, timeUnit: TimeUnit) => void
  onToggleSet: (log: SetLog) => void
  onShowProgress: (name: string) => void
}) {
  // Rounds = the most sets any member has. They are validated server-side to
  // be equal, but a legacy or hand-edited row could differ, and a missing
  // member should leave a gap rather than break the card.
  const rounds = compositeRounds(members.map((m) => m.logs.length))
  const allLogs = members.flatMap((m) => m.logs)
  const doneCount = allLogs.filter((l) => isSetCompleted(l)).length
  const allDone = allLogs.length > 0 && doneCount === allLogs.length
  const rest = compositeRest(members.map((m) => m.planEx))

  function roundComplete(round: number) {
    const logs = members.map((m) => m.logs[round]).filter(Boolean) as SetLog[]
    return logs.length > 0 && logs.every((l) => isSetCompleted(l))
  }

  const roundsDone = Array.from({ length: rounds }, (_, r) => r)
    .filter((r) => roundComplete(r)).length

  return (
    <div className={`bg-white rounded-2xl border-2 overflow-hidden transition-colors ${allDone ? 'border-emerald-200' : 'border-brand-200'}`}>
      {/* Header: the pairing is the identity of this card, so both names go here. */}
      <div
        onClick={onToggleOpen}
        className={`px-4 py-3 cursor-pointer select-none ${allDone ? 'bg-emerald-50' : 'bg-brand-50/70'}`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0 flex items-center gap-2">
            <span className="text-gray-400 text-[10px] flex-shrink-0 w-2">
              {isOpen ? '▾' : '▸'}
            </span>
            <div className="min-w-0">
              <p className="text-[10px] font-bold text-brand-500 uppercase tracking-widest">
                ⟲ {compositeLabel(members.length)} · {rounds} round{rounds === 1 ? '' : 's'}
                {rest != null && ` · ${rest}s rest after each round`}
              </p>
              {/* Each member on its OWN line with its OWN sets x reps. The
                  header used to be just "A  +  B", so a collapsed card showed
                  no prescription at all -- and a shared figure would be wrong
                  for at least one member (bench 4x12 weighted vs push-ups
                  4x10 bodyweight). */}
              {members.map((member) => (
                <div key={member.planEx.id}
                     className="flex items-baseline justify-between gap-2">
                  <p className="font-bold text-gray-900 text-sm truncate">
                    {member.planEx.name}
                  </p>
                  <p className="text-[11px] font-semibold text-gray-500 whitespace-nowrap flex-shrink-0">
                    {member.planEx.sets} × {member.planEx.reps}
                  </p>
                </div>
              ))}
            </div>
          </div>
          {/* Rounds completed, NOT sets. This read "0/8" for a 2-member
              4-round superset -- members x rounds, a number that corresponds
              to nothing the user is doing. You do rounds. */}
          <span className={`text-xs font-semibold flex-shrink-0 ${allDone ? 'text-emerald-600' : 'text-gray-400'}`}>
            {allDone ? 'done' : `Round ${Math.min(roundsDone + 1, rounds)}/${rounds}`}
          </span>
        </div>
      </div>

      {isOpen && (
        <div className="divide-y divide-gray-100">
          {Array.from({ length: rounds }, (_, round) => (
            <div key={round} className="px-4 py-3">
              <div className="flex items-center justify-between mb-2">
                <p className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">
                  Round {round + 1}
                </p>
                {/* One tap for the whole round instead of one per member. */}
                {!isCompleted && (
                  <button
                    onClick={() => members.forEach((m) => {
                      const log = m.logs[round]
                      if (log && isSetCompleted(log) === roundComplete(round)) {
                        onToggleSet(log)
                      }
                    })}
                    className={`text-[11px] font-semibold transition-colors ${roundComplete(round) ? 'text-emerald-600' : 'text-brand-500 hover:text-brand-600'}`}
                  >
                    {roundComplete(round) ? '✓ round done' : 'mark round done'}
                  </button>
                )}
              </div>

              <div className="space-y-2.5">
                {members.map((member) => {
                  const log = member.logs[round]
                  if (!log) return null
                  const fields = fieldsFor(member.measurementType)
                  const timeUnit = timeUnitFor(member.planEx.reps)
                  const done = isSetCompleted(log)

                  return (
                    <div
                      key={member.planEx.id}
                      className={`rounded-xl px-3 py-2 ${done ? 'bg-emerald-50/70' : 'bg-gray-50/70'}`}
                    >
                      <div className="flex items-center justify-between gap-2 mb-1.5">
                        <button
                          onClick={() => onShowProgress(member.planEx.name)}
                          className="text-xs font-semibold text-gray-700 truncate text-left hover:text-brand-500 transition-colors"
                        >
                          {member.planEx.name}
                        </button>
                        <button
                          onClick={!isCompleted ? () => onToggleSet(log) : undefined}
                          disabled={isCompleted}
                          className={`w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold flex-shrink-0 transition-all ${done ? 'bg-emerald-500 border-emerald-500 text-white' : 'border-gray-300 text-transparent hover:border-brand-400'}`}
                        >
                          ✓
                        </button>
                      </div>

                      {/* Labels are INLINE per member: two members can have
                          different column sets, so a shared header row cannot
                          describe both. */}
                      <div className="flex gap-2">
                        {fields.map((field) => {
                          const display = toDisplayField(
                            field, setFieldValue(log, field), unit, timeUnit)
                          return (
                            <div key={field} className="flex-1 min-w-0">
                              <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5 truncate">
                                {fieldLabel(field, unit, member.planEx.name, timeUnit)}
                              </label>
                              {isCompleted ? (
                                <p className="text-sm font-medium text-gray-700 px-1 py-1">
                                  {field === 'duration_seconds'
                                    && setFieldValue(log, field) !== null
                                    ? formatDuration(setFieldValue(log, field) as number)
                                    : display ?? '—'}
                                </p>
                              ) : (
                                <input
                                  type="number" min={0}
                                  step={field === 'weight_kg'
                                    || (field === 'duration_seconds' && timeUnit === 'min')
                                    ? 0.5 : 1}
                                  placeholder="0"
                                  value={display ?? ''}
                                  onChange={(e) => onFieldChange(
                                    log, field, e.target.value, timeUnit)}
                                  className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-center focus:outline-none focus:ring-2 focus:ring-brand-400 bg-white"
                                />
                              )}
                            </div>
                          )
                        })}
                        {fields.length === 0 && (
                          <p className="text-xs text-gray-400 py-1">
                            {member.planEx.reps}
                          </p>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Rest is stated once on the header. Repeating it under every
                  round was the same fact N times. What is useful per round is
                  which round comes next. */}
              {rest != null && round < rounds - 1 && roundComplete(round) && (
                <p className="text-[11px] text-gray-400 text-center mt-2.5">
                  ⏱ rest, then round {round + 2}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
