/**
 * Grouping exercises into composites (supersets / trisets).
 *
 * Why this is one shared helper
 * -----------------------------
 * Four surfaces render a day's exercises, and only ONE of them knew about
 * supersets: the live session card. The three plan views listed every exercise
 * as an independent card, so a superset was invisible until you started the
 * workout — which is exactly when it is too late to notice the plan is wrong.
 *
 * This is also the single adaptation point for how a composite is identified.
 * Today that is `superset_group` on each exercise; if that moves to a real
 * container the only place that needs to change is `groupKeyOf`.
 *
 * Degradation is deliberate: a group with fewer than two members is NOT a
 * composite. The server rejects those, but a hand-edited or partially migrated
 * row should fall back to an ordinary card rather than render a
 * one-exercise "superset".
 */

/** The minimum an item needs for grouping. Both plan rows and session entries fit. */
export interface Groupable {
  superset_group?: number | null
}

export type CompositeUnit<T> =
  | { kind: 'single'; item: T }
  | { kind: 'composite'; group: number; items: T[] }

function groupKeyOf(item: Groupable | null | undefined): number | null {
  return item?.superset_group ?? null
}

/**
 * Collapse consecutive same-group items into composite units.
 *
 * Grouping is positional on purpose: members must be ADJACENT, which the
 * server already enforces. Gathering non-adjacent members would silently
 * reorder the user's day, and the order is the prescription.
 */
export function groupIntoUnits<T>(
  items: T[],
  keyFor: (item: T) => Groupable | null | undefined = (item) => item as Groupable,
): CompositeUnit<T>[] {
  const units: CompositeUnit<T>[] = []

  for (const item of items) {
    const group = groupKeyOf(keyFor(item))
    const last = units[units.length - 1]
    if (group !== null && last && last.kind === 'composite'
        && last.group === group) {
      last.items.push(item)
      continue
    }
    units.push(group !== null
      ? { kind: 'composite', group, items: [item] }
      : { kind: 'single', item })
  }

  return units.map((unit) =>
    unit.kind === 'composite' && unit.items.length < 2
      ? { kind: 'single' as const, item: unit.items[0] }
      : unit)
}

/**
 * "Superset" for two members, "Triset" for three, "Giant set" beyond.
 *
 * Named by size because that is what the words mean — calling a
 * three-exercise block a superset is the kind of small wrongness that makes
 * the whole app feel untrustworthy to someone who trains.
 */
export function compositeLabel(memberCount: number): string {
  if (memberCount <= 2) return 'Superset'
  if (memberCount === 3) return 'Triset'
  return 'Giant set'
}

/**
 * Rounds in a composite: the members' set count.
 *
 * Members are validated server-side to have equal `sets`, so the max is the
 * answer and a mismatched legacy row leaves a gap rather than breaking.
 */
export function compositeRounds(sets: number[]): number {
  return sets.length ? Math.max(...sets) : 0
}

/**
 * Rest between rounds, in seconds.
 *
 * Rest belongs to the ROUND, not to a member — you do every exercise back to
 * back and then rest once. The last member's value is the one that means
 * "after the round", which is why it wins over the others.
 */
export function compositeRest(restSeconds: Array<number | null>): number | null {
  for (let i = restSeconds.length - 1; i >= 0; i--) {
    if (restSeconds[i] != null) return restSeconds[i]
  }
  return null
}
