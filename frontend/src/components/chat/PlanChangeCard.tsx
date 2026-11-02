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
  draft: Array<{ day?: string; name?: string; week?: number; action?: string }>
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
          <ul className="space-y-1">
            {change.draft.map((item, i) => (
              <li key={i} className="text-sm text-ink-600">
                <span className="text-ink-400">
                  {item.day ?? 'Plan'}
                  {item.week ? ` · week ${item.week}` : ''}
                </span>
                {' — '}
                <span className="font-medium">{item.name}</span>
                {item.action ? <span className="text-ink-400"> ({item.action})</span> : null}
              </li>
            ))}
          </ul>

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

      {diff.changed.length + diff.added.length + diff.removed.length > 0 && (
        <ul className="mt-2 space-y-1">
          {diff.removed.map((e, i) => (
            <li key={`r${i}`} className="text-sm text-ink-500">
              − {e.name} <span className="text-ink-400">({e.day})</span>
            </li>
          ))}
          {diff.added.map((e, i) => (
            <li key={`a${i}`} className="text-sm text-ink-600">
              + {e.name} <span className="text-ink-400">({e.day})</span>
            </li>
          ))}
          {diff.changed.map((e, i) => (
            <li key={`c${i}`} className="text-sm text-ink-600">
              ~ {e.name} <span className="text-ink-400">({e.day})</span>
            </li>
          ))}
        </ul>
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
