import { useState } from 'react'

/**
 * The draft the user approves, and the diff that proves what happened.
 *
 * Built from the server's recorded draft rather than from the reply text.
 * A card rendered from prose would be asking the user to approve a
 * PARAPHRASE -- written by the same model whose plan edits dropped a calf
 * raise -- and the confirmation would not be tied to anything the server
 * could check afterwards.
 */

export interface ProposedChange {
  change_id: string
  /** Which plan the draft belongs to, so Confirm knows where to send it. */
  plan_id?: string
  needs_confirmation: boolean
  applied: boolean
  draft: Array<{
    day?: string
    name?: string
    week?: number
    action?: string
    replaces?: string
    /** For action="move_day": the 1-based slot the day moves to. */
    to_position?: number
  }>
  keep_list?: string[]
  warnings?: Array<{ type: string; name: string; message: string }>
}

/**
 * A day that was renamed or moved. Reported apart from `changed`, which is
 * about exercises: moving a day touches no exercise at all, and the user is
 * confirming one fact -- that the day is where they asked for it.
 */
export interface DayChange {
  /** The earliest week the change covers. */
  week: number
  /**
   * Every week it covers.
   *
   * A day move with no week applies to all of them, so an 8-week plan was
   * reporting 24 rows for one logical change. Absent on diffs recorded
   * before the server collapsed them.
   */
  weeks?: number[]
  from_name: string
  name: string
  from_position: number
  position: number
}

/** "week 3", "weeks 1-8", or "weeks 2, 5, 7" when they do not run on. */
function weekSpan(d: DayChange) {
  const weeks = d.weeks?.length ? d.weeks : [d.week]
  if (weeks.length === 1) return `week ${weeks[0]}`
  const contiguous = weeks.every((w, i) => i === 0 || w === weeks[i - 1] + 1)
  return contiguous
    ? `weeks ${weeks[0]}-${weeks[weeks.length - 1]}`
    : `weeks ${weeks.join(', ')}`
}

export interface VerificationDiff {
  added: Array<{ name: string; day?: string; week?: number }>
  removed: Array<{ name: string; day?: string; week?: number }>
  changed: Array<{ name: string; day?: string; week?: number }>
  unapproved: Array<{ name: string; day?: string; week?: number }>
  keep_list_violations: Array<{ name: string; day?: string; week?: number }>
  /**
   * Exercises the confirmed draft promised to add that the plan does not
   * have. The before/after diff cannot see these -- something never added
   * appears in neither snapshot -- so a draft that delivered one of five
   * exercises used to verify clean.
   */
  missing_adds?: Array<{ name: string; day?: string; week?: number }>
  /** Absent on diffs recorded before day changes could be proposed. */
  day_changes?: DayChange[]
  clean: boolean
}

/** What one day change says, in the words the user used to ask for it. */
function dayChangeLabel(item: {
  action?: string
  day?: string
  name?: string
  to_position?: number
}) {
  if (item.action === 'rename_day') {
    return { verb: 'Rename day', detail: `${item.day} → ${item.name}` }
  }
  const slot = item.to_position
  return {
    verb: 'Move day',
    detail: `${item.day ?? item.name} → position ${slot ?? '?'}`,
  }
}

export function PlanChangeDraft({
  change,
  onConfirm,
  onCancel,
  busy,
}: {
  change: ProposedChange
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
}) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className="mb-4 rounded-2xl border border-accent-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-4 min-h-[44px] bg-accent-50 text-left"
      >
        <span className="text-sm font-semibold text-ink-600">
          Proposed change — nothing applied yet
        </span>
        <span className="text-ink-400 text-xs">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="px-4 py-3">
          <div className="space-y-3">
            {change.draft.map((item, i) => (
              <div key={i}>
                <p className="text-xs text-ink-400">
                  {item.day ?? 'Plan'}
                  {item.week ? ` · week ${item.week}` : ' · all weeks'}
                </p>
                {item.action === 'rename_day' || item.action === 'move_day' ? (
                  <div className="mt-1">
                    <p className="text-xs uppercase tracking-wide text-ink-400">
                      {dayChangeLabel(item).verb}
                    </p>
                    <p className="text-sm font-semibold text-ink-600">
                      {dayChangeLabel(item).detail}
                    </p>
                  </div>
                ) : item.replaces ? (
                  <div className="mt-1">
                    <p className="text-xs uppercase tracking-wide text-ink-400">Replace</p>
                    <p className="text-sm text-ink-500 line-through">{item.replaces}</p>
                    <p className="text-xs uppercase tracking-wide text-ink-400 mt-1">With</p>
                    <p className="text-sm font-semibold text-ink-600">{item.name}</p>
                  </div>
                ) : (
                  <p className="text-sm font-medium text-ink-600 mt-0.5">
                    {item.action === 'remove' ? 'Remove ' : item.action === 'add' ? 'Add ' : ''}
                    {item.name}
                  </p>
                )}
              </div>
            ))}
          </div>

          {/* The reassurance belongs on the DRAFT too, not only afterwards:
              the worry is "what else will this touch?", and answering it
              before the user commits is the point of showing a draft. */}
          {change.keep_list?.length ? (
            <p className="text-xs text-ink-400 mt-3">
              Protected: {change.keep_list.join(', ')}
            </p>
          ) : null}

          {/* Unknown exercises are a WARNING, never a block: refusing them
              would stop the coach ever introducing a new movement. */}
          {change.warnings?.length ? (
            <ul className="mt-3 space-y-1">
              {change.warnings.map((w, i) => (
                <li key={i} className="text-xs text-amber-700">⚠ {w.message}</li>
              ))}
            </ul>
          ) : null}

          <div className="flex gap-2 mt-4">
            <button
              type="button"
              onClick={onConfirm}
              disabled={busy}
              className="min-h-[44px] px-5 rounded-full bg-accent-500 hover:bg-accent-600 disabled:opacity-50 text-white text-sm font-medium"
            >
              {busy ? 'Applying…' : 'Confirm'}
            </button>
            <button
              type="button"
              onClick={onCancel}
              disabled={busy}
              className="min-h-[44px] px-4 rounded-full border border-gray-300 text-sm font-medium text-gray-700"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export function PlanChangeResult({
  diff,
  untouched,
  onUndo,
}: {
  diff: VerificationDiff
  untouched?: number
  onUndo?: () => void
}) {
  // A change that touched something nobody approved must NEVER read as
  // success. That is the whole reason the diff is computed server-side from
  // recorded snapshots rather than taken from the model's account of itself.
  const failed = !diff.clean
  // Optional on the wire: a card rendered from a change recorded before
  // this check existed has no such field.
  const missingAdds = diff.missing_adds ?? []

  return (
    <div
      className={`mb-4 rounded-2xl border p-4 ${
        failed ? 'border-red-300 bg-red-50' : 'border-accent-200 bg-accent-50'
      }`}
    >
      <p className="text-sm font-semibold text-ink-600">
        {failed ? 'Applied, but not as approved' : 'Done'}
      </p>

      {/* Days that moved or were renamed. First, because a restructure is
          what the user asked for and the exercise list below it is the
          detail -- and because a week that was only reordered touches no
          exercise, so without this the card would say "Done" and show
          nothing at all. */}
      {diff.day_changes?.length ? (
        <div className="mt-3 rounded-xl border border-gray-200 bg-white p-3 space-y-2">
          {diff.day_changes.map((d, i) => (
            <div key={`d${i}`} className="flex items-start gap-2">
              <span className="text-accent-500 mt-0.5" aria-hidden>↕</span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-ink-600">
                  {d.from_name === d.name ? d.name : `${d.from_name} → ${d.name}`}
                </p>
                <p className="text-xs text-ink-400">
                  {d.from_position === d.position
                    ? `Day ${d.position}`
                    : `Day ${d.from_position} → day ${d.position}`}
                  {' · '}
                  {weekSpan(d)}
                </p>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {/* Removed exercises are struck through and paired with what replaced
          them, rather than listed as two unrelated lines. A swap is ONE
          change, and reading it as "something left, something arrived" is
          what makes a plan edit feel unaccountable. */}
      {diff.changed.length + diff.added.length + diff.removed.length > 0 && (
        <div className="mt-3 rounded-xl border border-gray-200 bg-white p-3 space-y-3">
          {diff.removed.map((gone, i) => {
            const arrived = diff.added[i]
            return (
              <div key={`r${i}`} className="flex items-start gap-2">
                <span className="text-ink-300 mt-0.5" aria-hidden>⊘</span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-ink-400 line-through">{gone.name}</p>
                  {arrived && (
                    <p className="text-sm font-medium text-ink-600 mt-1">
                      <span className="text-accent-500" aria-hidden>→ </span>
                      {arrived.name}
                    </p>
                  )}
                  <p className="text-xs text-ink-400 mt-0.5">{gone.day}</p>
                </div>
              </div>
            )
          })}

          {/* Additions with no matching removal: genuinely new work. */}
          {diff.added.slice(diff.removed.length).map((e, i) => (
            <div key={`a${i}`} className="flex items-start gap-2">
              <span className="text-accent-500 mt-0.5" aria-hidden>＋</span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-ink-600">{e.name}</p>
                <p className="text-xs text-ink-400">{e.day}</p>
              </div>
            </div>
          ))}

          {diff.changed.map((e, i) => (
            <div key={`c${i}`} className="flex items-start gap-2">
              <span className="text-ink-400 mt-0.5" aria-hidden>～</span>
              <div className="min-w-0">
                <p className="text-sm text-ink-600">{e.name}</p>
                <p className="text-xs text-ink-400">{e.day}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Proof of restraint. "24 other exercises untouched" is the sentence
          that answers the actual worry: what ELSE did it change? */}
      {typeof untouched === 'number' && untouched > 0 && !failed && (
        <p className="text-sm text-ink-500 mt-2">
          {untouched} other {untouched === 1 ? 'exercise' : 'exercises'} untouched.
          Nothing else in your plan changed.
        </p>
      )}

      {failed && (
        <div className="mt-3">
          {/* Each kind of failure says its own sentence. They used to share
              one, so a change whose only fault was a missing exercise
              announced "0 exercises changed that were not in what you
              confirmed". */}
          {diff.unapproved.length > 0 && (
            <>
              {/* "1 change was not approved" was reported as unreadable --
                  it names a state the user was never shown and gives them
                  nothing to do about it. What it actually means is that the
                  plan came out different from what they confirmed, which is
                  worth saying in those words. */}
              <p className="text-sm text-red-700 font-medium">
                Applied — but {diff.unapproved.length === 1
                  ? 'one exercise changed that was not'
                  : `${diff.unapproved.length} exercises changed that were not`}{' '}
                in what you confirmed:
              </p>
              <ul className="mt-1 space-y-1">
                {diff.unapproved.map((e, i) => (
                  <li key={i} className="text-sm text-red-700">
                    {e.name} <span className="text-red-500">({e.day})</span>
                  </li>
                ))}
              </ul>
              <p className="text-sm text-ink-500 mt-2">
                Everything you did confirm went through. Tell the coach to put
                {diff.unapproved.length === 1 ? ' it' : ' them'} back if that is
                not what you wanted.
              </p>
            </>
          )}
          {missingAdds.length > 0 && (
            <div className={diff.unapproved.length > 0 ? 'mt-3' : ''}>
              <p className="text-sm text-red-700 font-medium">
                {missingAdds.length === 1
                  ? 'One exercise you confirmed is'
                  : `${missingAdds.length} exercises you confirmed are`}{' '}
                not in your plan:
              </p>
              <ul className="mt-1 space-y-1">
                {missingAdds.map((e, i) => (
                  <li key={i} className="text-sm text-red-700">
                    {e.name}{e.day ? <span className="text-red-500"> ({e.day})</span> : null}
                  </li>
                ))}
              </ul>
              <p className="text-sm text-ink-500 mt-2">
                Ask the coach to add {missingAdds.length === 1 ? 'it' : 'them'} again.
              </p>
            </div>
          )}
          {diff.keep_list_violations.length > 0 && (
            <p className="text-sm text-red-700 mt-2">
              {diff.keep_list_violations.length} protected{' '}
              {diff.keep_list_violations.length === 1 ? 'exercise was' : 'exercises were'}{' '}
              affected.
            </p>
          )}
        </div>
      )}

      {onUndo && (
        <button
          type="button"
          onClick={onUndo}
          className="mt-3 min-h-[44px] px-4 rounded-full border border-gray-300 text-sm font-medium text-gray-700"
        >
          Undo
        </button>
      )}
    </div>
  )
}
