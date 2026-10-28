import { useState, useEffect } from 'react'
import { api, type SupersetPartner } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'
import { compositeLabel } from '../../lib/composites'

/**
 * Build composites by hand: add, remove, reorder, and ungroup.
 *
 * The list only contains partners the SERVER's validator accepts, so a bad
 * pairing is not reachable here either — bench press + push-ups is both-push,
 * both-chest and both-compound, and each ruins the other.
 *
 * Refused pairings are now SHOWN with their reason rather than omitted. An
 * absence reads as a bug: the user can see the exercise is on the day, so the
 * question is why it isn't offered. "Both push, both chest" is an answer.
 *
 * Groups can hold up to `max_members` (triset, giant set). A new member must
 * be compatible with EVERY existing member, which mirrors the server's
 * pairwise check — so what the picker offers and what the server accepts
 * cannot drift apart.
 *
 * The warmup and the day's first working exercise are never offered: the
 * primary lift is done fresh and heavy.
 */

const KIND_LABEL: Record<string, string> = {
  antagonist: 'opposing muscles — nothing is compromised',
  non_competing: 'unrelated muscles — no interference',
  exhaust: 'same muscle, one isolation',
}

/** Rejection codes from exercise_muscles, in the user's language. */
const REJECTION_LABEL: Record<string, string> = {
  competing: 'same muscle, both compound — each ruins the other',
  two_compounds: 'two heavy compounds — exhausting, not efficient',
  two_holds: 'two timed holds save no time',
  spinal_load: 'both load the back, and you noted a back limitation',
  overlap: 'shares muscles — the second would be done pre-fatigued',
  unavailable: 'needs equipment this gym does not have',
  same_exercise: 'that is the same exercise',
  no_data: 'no movement data, so this cannot be checked',
  warmup: 'warmup blocks are not supersetted',
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
  const [groups, setGroups] = useState<string[][]>([])
  const [building, setBuilding] = useState<string[] | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.workouts.getSupersetOptions(dayId)
      .then(setOptions)
      .catch(() => setOptions(null))
      .finally(() => setLoading(false))
  }, [dayId])

  const maxGroups = options?.max_groups ?? 2
  const maxMembers = options?.max_members ?? 4
  const used = new Set([...groups.flat(), ...(building ?? [])])
  const byId = new Map(options?.exercises.map((e) => [e.id, e]) ?? [])

  /** Any name we know, including one only reachable as somebody's partner. */
  function nameOf(id: string): string {
    const direct = byId.get(id)
    if (direct) return direct.name
    for (const entry of options?.exercises ?? []) {
      const hit = [...entry.partners, ...entry.rejected]
        .find((p) => p.id === id)
      if (hit) return hit.name
    }
    return id
  }

  /**
   * Partners valid for EVERY member being built, with equal sets.
   *
   * Intersecting mirrors the server's pairwise rule. Offering a partner that
   * only works with one member would let the user build a group the server
   * then rejects, which is the worst version of this UI.
   */
  function candidatesFor(members: string[]): SupersetPartner[] {
    const lists = members.map((id) => byId.get(id)?.partners ?? [])
    if (!lists.length) return []
    const [first, ...rest] = lists
    return first.filter((partner) =>
      !used.has(partner.id)
      && partner.same_sets
      && rest.every((list) => list.some(
        (other) => other.id === partner.id && other.same_sets)))
  }

  function move(groupIndex: number, from: number, to: number) {
    setGroups((prev) => prev.map((group, i) => {
      if (i !== groupIndex || to < 0 || to >= group.length) return group
      const next = [...group]
      const [item] = next.splice(from, 1)
      next.splice(to, 0, item)
      return next
    }))
  }

  async function save() {
    setSaving(true)
    try {
      await api.workouts.setDaySupersets(dayId, groups)
      showToast(groups.length
        ? `${groups.length} group${groups.length === 1 ? '' : 's'} saved`
        : 'Supersets cleared')
      onSaved()
      onClose()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  const atGroupCap = groups.length >= maxGroups
  const buildCandidates = building ? candidatesFor(building) : []

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4 pb-20 sm:pb-4 bg-black/50"
         onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md max-h-[80vh] flex flex-col overflow-hidden"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <h2 className="font-bold text-gray-900 text-base">Supersets</h2>
            <p className="text-[11px] text-gray-400">
              Done back to back as one round. Up to {maxGroups} per day,
              {' '}{maxMembers} exercises each.
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
              No valid pairings on this day. Supersets need exercises that do
              not compete — opposing muscle groups, or a lift plus unrelated
              core work.
            </p>
          ) : (
            <>
              {/* Saved groups: reorderable, removable, ungroupable. */}
              {groups.map((group, groupIndex) => (
                <div key={groupIndex}
                     className="mb-3 bg-brand-50 border border-brand-200 rounded-xl p-3">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-[10px] font-bold text-brand-500 uppercase tracking-widest">
                      {compositeLabel(group.length)}
                    </p>
                    <button
                      onClick={() => setGroups((prev) =>
                        prev.filter((_, i) => i !== groupIndex))}
                      className="text-[11px] font-semibold text-red-600"
                    >
                      Ungroup
                    </button>
                  </div>
                  <div className="space-y-1">
                    {group.map((id, memberIndex) => (
                      <div key={id} className="flex items-center gap-1.5">
                        {/* Order is the prescription — which exercise you do
                            first in a round matters, so it is editable. */}
                        <div className="flex flex-col flex-shrink-0">
                          <button
                            onClick={() => move(groupIndex, memberIndex, memberIndex - 1)}
                            disabled={memberIndex === 0}
                            aria-label={`Move ${nameOf(id)} earlier`}
                            className="text-[9px] leading-none text-gray-400 disabled:opacity-20 hover:text-brand-500 px-1"
                          >
                            ▲
                          </button>
                          <button
                            onClick={() => move(groupIndex, memberIndex, memberIndex + 1)}
                            disabled={memberIndex === group.length - 1}
                            aria-label={`Move ${nameOf(id)} later`}
                            className="text-[9px] leading-none text-gray-400 disabled:opacity-20 hover:text-brand-500 px-1"
                          >
                            ▼
                          </button>
                        </div>
                        <p className="text-xs font-semibold text-gray-800 min-w-0 flex-1 truncate">
                          {nameOf(id)}
                        </p>
                        {group.length > 2 && (
                          <button
                            onClick={() => setGroups((prev) => prev.map((g, i) =>
                              i === groupIndex ? g.filter((x) => x !== id) : g))}
                            aria-label={`Remove ${nameOf(id)}`}
                            className="text-[11px] font-semibold text-gray-400 hover:text-red-600 flex-shrink-0"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  {/* A third exercise has to be valid against BOTH existing
                      members, so the list is an intersection. */}
                  {group.length < maxMembers
                    && candidatesFor(group).length > 0 && (
                    <select
                      value=""
                      onChange={(e) => {
                        if (!e.target.value) return
                        const id = e.target.value
                        setGroups((prev) => prev.map((g, i) =>
                          i === groupIndex ? [...g, id] : g))
                      }}
                      className="mt-2 w-full text-xs border border-brand-200 rounded-lg px-2 py-1.5 bg-white text-gray-700"
                    >
                      <option value="">+ add another exercise…</option>
                      {candidatesFor(group).map((partner) => (
                        <option key={partner.id} value={partner.id}>
                          {partner.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              ))}

              {atGroupCap ? (
                <p className="text-xs text-gray-400">
                  At the {maxGroups}-group limit. Ungroup one to add another.
                </p>
              ) : !building || building.length === 0 ? (
                <>
                  <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    Pick the first exercise
                  </p>
                  <div className="space-y-1">
                    {options.exercises
                      .filter((e) => !used.has(e.id)
                        && e.partners.some((p) => !used.has(p.id)))
                      .map((e) => (
                        <button key={e.id} onClick={() => setBuilding([e.id])}
                                className="w-full text-left text-sm px-3 py-2 rounded-xl hover:bg-gray-50 transition-colors">
                          {e.name}
                        </button>
                      ))}
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide min-w-0 truncate">
                      Pair <span className="text-gray-900">
                        {building.map(nameOf).join(' + ')}
                      </span> with
                    </p>
                    <button onClick={() => setBuilding(null)}
                            className="text-[11px] font-semibold text-gray-400 flex-shrink-0">
                      Back
                    </button>
                  </div>
                  <div className="space-y-1">
                    {buildCandidates.map((partner) => (
                      <button
                        key={partner.id}
                        onClick={() => {
                          setGroups((prev) => [...prev, [...building, partner.id]])
                          setBuilding(null)
                        }}
                        className="w-full text-left px-3 py-2 rounded-xl hover:bg-gray-50 transition-colors"
                      >
                        <p className="text-sm text-gray-900">{partner.name}</p>
                        <p className="text-[11px] text-gray-400">
                          {KIND_LABEL[partner.kind] ?? partner.reason}
                        </p>
                      </button>
                    ))}
                    {buildCandidates.length === 0 && (
                      <p className="text-xs text-gray-500">
                        Nothing on this day pairs with that. See below for why.
                      </p>
                    )}
                  </div>

                  {/* Why the others are missing. Hiding this made the picker
                      look broken rather than opinionated. */}
                  {(() => {
                    const entry = byId.get(building[0])
                    const refused = [
                      ...(entry?.rejected ?? []).map((p) => ({
                        ...p, why: REJECTION_LABEL[p.kind] ?? p.reason })),
                      ...(entry?.partners ?? [])
                        .filter((p) => !p.same_sets)
                        .map((p) => ({
                          ...p,
                          why: 'different number of sets — a superset is N rounds of both',
                        })),
                    ].filter((p) => !used.has(p.id))
                    if (!refused.length) return null
                    return (
                      <div className="mt-4 pt-3 border-t border-gray-100">
                        <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">
                          Not offered
                        </p>
                        <div className="space-y-1.5">
                          {refused.map((p) => (
                            <div key={p.id}>
                              <p className="text-xs text-gray-500">{p.name}</p>
                              <p className="text-[11px] text-gray-400">{p.why}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })()}
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
            {saving ? 'Saving…' : groups.length ? 'Save supersets' : 'Clear supersets'}
          </button>
        </div>
      </div>
    </div>
  )
}
