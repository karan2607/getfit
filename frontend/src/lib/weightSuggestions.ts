import type { WeightSuggestion } from './api'

/**
 * Suggested weights are advisory. They are shown in the weight input as a
 * placeholder, never stored in a draft or a SetLog on their own -- a
 * suggestion that wrote itself into history would become the history the
 * next suggestion is computed from.
 *
 * The one way a suggestion becomes a logged value is the user confirming it:
 * ticking the set done while the weight input still shows only the
 * suggestion. Anything the user typed -- including clearing the input -- is
 * in the draft, and the draft always wins.
 */
export function weightToConfirm(
  draft: Record<string, unknown> | undefined,
  storedWeightKg: number | null,
  suggestion: WeightSuggestion | undefined,
): number | undefined {
  if (!suggestion || suggestion.weight_kg == null) return undefined
  if (draft && 'weight_kg' in draft) return undefined
  if (storedWeightKg != null) return undefined
  return suggestion.weight_kg
}

export interface SuggestionActual {
  exercise_id: string
  set_number: number
  reps_completed?: number | null
  is_completed?: boolean
}

/**
 * Today's unsaved sets, in the shape the suggestions endpoint reads, so later
 * exercises' suggestions follow what is actually happening before Save.
 * Drafts are keyed `${exerciseId}:${setNumber}` (see pendKey).
 */
export function actualsFromDrafts(
  drafts: Record<string, { reps_completed?: number | null; is_completed?: boolean }>,
): SuggestionActual[] {
  const out: SuggestionActual[] = []
  for (const [key, draft] of Object.entries(drafts)) {
    const split = key.lastIndexOf(':')
    const exerciseId = key.slice(0, split)
    const setNumber = Number(key.slice(split + 1))
    if (split < 0 || exerciseId === 'orphan' || Number.isNaN(setNumber)) continue
    const hasReps = 'reps_completed' in draft
    if (!hasReps && draft.is_completed === undefined) continue
    out.push({
      exercise_id: exerciseId,
      set_number: setNumber,
      ...(hasReps ? { reps_completed: draft.reps_completed ?? null } : {}),
      ...(draft.is_completed !== undefined ? { is_completed: draft.is_completed } : {}),
    })
  }
  return out
}
