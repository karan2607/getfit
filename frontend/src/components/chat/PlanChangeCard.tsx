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
  }>
  keep_list?: string[]
  warnings?: Array<{ type: string; name: string; message: string }>
}

export interface VerificationDiff {
  added: Array<{ name: string; day?: string; week?: number }>
  removed: Array<{ name: string; day?: string; week?: number }>
  changed: Array<{ name: string; day?: string; week?: number }>
  unapproved: Array<{ name: string; day?: string; week?: number }>
  keep_list_violations: Array<{ name: string; day?: string; week?: number }>
  clean: boolean
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
                {item.replaces ? (
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

  return (
    <div
      className={`mb-4 rounded-2xl border p-4 ${
        failed ? 'border-red-300 bg-red-50' : 'border-accent-200 bg-accent-50'
      }`}
    >
      <p className="text-sm font-semibold text-ink-600">
        {failed ? 'Applied, but not as approved' : 'Done'}
      </p>

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
          <p className="text-sm text-red-700 font-medium">
            {diff.unapproved.length} change
            {diff.unapproved.length === 1 ? ' was' : 's were'} not approved:
          </p>
          <ul className="mt-1 space-y-1">
            {diff.unapproved.map((e, i) => (
              <li key={i} className="text-sm text-red-700">
                {e.name} <span className="text-red-500">({e.day})</span>
              </li>
            ))}
          </ul>
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
