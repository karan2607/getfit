import { useState, useEffect } from 'react'
import { api } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'

/**
 * Pair exercises into supersets by hand.
 *
 * The list only contains partners the SERVER's validator accepts, so a bad
 * pairing is not reachable here either — bench press + push-ups is both-push,
 * both-chest and both-compound, and each ruins the other. The reason comes
 * back with each option so the UI can say *why* something is offered, rather
 * than silently omitting it.
 *
 * The warmup and the day's first working exercise are never offered: the
 * primary lift is done fresh and heavy.
 */

const KIND_LABEL: Record<string, string> = {
  antagonist: 'opposing muscles — nothing is compromised',
  non_competing: 'unrelated muscles — no interference',
  exhaust: 'same muscle, one isolation',
}

export default function SupersetPicker({ dayId, onClose, onSaved }: {
  dayId: string
  onClose: () => void
  onSaved: () => void
}) {
  const { showToast } = useToast()
  const [options, setOptions] = useState<Awaited<
    ReturnType<typeof api.workouts.getSupersetOptions>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [pairs, setPairs] = useState<string[][]>([])
  const [first, setFirst] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.workouts.getSupersetOptions(dayId)
      .then(setOptions)
      .catch(() => setOptions(null))
      .finally(() => setLoading(false))
  }, [dayId])

  const used = new Set(pairs.flat())
  const byId = new Map(options?.exercises.map((e) => [e.id, e]) ?? [])
  const firstEntry = first ? byId.get(first) : null

  function addPair(a: string, b: string) {
    setPairs((prev) => [...prev, [a, b]])
    setFirst(null)
  }

  async function save() {
    setSaving(true)
    try {
      await api.workouts.setDaySupersets(dayId, pairs)
      showToast(pairs.length
        ? `${pairs.length} superset${pairs.length === 1 ? '' : 's'} saved`
        : 'Supersets cleared')
      onSaved()
      onClose()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  const maxGroups = options?.max_groups ?? 2
  const atCap = pairs.length >= maxGroups

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4 pb-20 sm:pb-4 bg-black/50"
         onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md max-h-[80vh] flex flex-col overflow-hidden"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <h2 className="font-bold text-gray-900 text-base">Supersets</h2>
            <p className="text-[11px] text-gray-400">
              Two exercises done back to back. Max {maxGroups} per day.
            </p>
          </div>
          <button onClick={onClose}
                  className="text-gray-400 hover:text-gray-600 text-xl leading-none">
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : !options || options.exercises.length === 0 ? (
            <p className="text-sm text-gray-500">
              No valid pairings on this day. Supersets need two exercises that
              do not compete — opposing muscle groups, or a lift plus
              unrelated core work.
            </p>
          ) : (
            <>
              {pairs.length > 0 && (
                <div className="mb-4 space-y-2">
                  {pairs.map((pair, index) => (
                    <div key={index}
                         className="flex items-center justify-between gap-2 bg-brand-50 border border-brand-200 rounded-xl px-3 py-2">
                      <p className="text-xs font-semibold text-gray-800 min-w-0">
                        {pair.map((id) => byId.get(id)?.name
                          ?? options.exercises.flatMap((e) => e.partners)
                               .find((p) => p.id === id)?.name
                          ?? id).join('  +  ')}
                      </p>
                      <button
                        onClick={() => setPairs((prev) =>
                          prev.filter((_, i) => i !== index))}
                        className="text-[11px] font-semibold text-red-600 flex-shrink-0"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {atCap ? (
                <p className="text-xs text-gray-400">
                  At the {maxGroups}-superset limit. Remove one to add another.
                </p>
              ) : !firstEntry ? (
                <>
                  <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    Pick the first exercise
                  </p>
                  <div className="space-y-1">
                    {options.exercises
                      .filter((e) => !used.has(e.id)
                        && e.partners.some((p) => !used.has(p.id)))
                      .map((e) => (
                        <button key={e.id} onClick={() => setFirst(e.id)}
                                className="w-full text-left text-sm px-3 py-2 rounded-xl hover:bg-gray-50 transition-colors">
                          {e.name}
                        </button>
                      ))}
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
                      Pair <span className="text-gray-900">{firstEntry.name}</span> with
                    </p>
                    <button onClick={() => setFirst(null)}
                            className="text-[11px] font-semibold text-gray-400">
                      Back
                    </button>
                  </div>
                  <div className="space-y-1">
                    {firstEntry.partners
                      .filter((p) => !used.has(p.id))
                      .map((p) => (
                        <button
                          key={p.id}
                          onClick={() => addPair(firstEntry.id, p.id)}
                          disabled={!p.same_sets}
                          className="w-full text-left px-3 py-2 rounded-xl hover:bg-gray-50 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
                        >
                          <p className="text-sm text-gray-900">{p.name}</p>
                          {/* Say why it is offered — and why a mismatched set
                              count blocks it, which is otherwise baffling. */}
                          <p className="text-[11px] text-gray-400">
                            {p.same_sets
                              ? (KIND_LABEL[p.kind] ?? p.reason)
                              : 'different number of sets — a superset is N rounds of both'}
                          </p>
                        </button>
                      ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>

        <div className="px-5 py-4 border-t border-gray-100 flex gap-2 flex-shrink-0">
          <button
            onClick={save}
            disabled={saving}
            className="flex-1 bg-brand-500 hover:bg-brand-600 disabled:opacity-40 text-white font-medium rounded-xl py-2.5 text-sm transition-colors"
          >
            {saving ? 'Saving…' : pairs.length ? 'Save supersets' : 'Clear supersets'}
          </button>
        </div>
      </div>
    </div>
  )
}
