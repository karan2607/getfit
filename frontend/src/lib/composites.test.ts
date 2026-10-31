import { describe, it, expect } from 'vitest'
import {
  groupIntoUnits, compositeLabel, compositeRounds, compositeRest,
  type Groupable,
} from './composites'

/**
 * Grouping was verified by hand with a node script when it was written,
 * because there was no test runner. These are those cases, plus the
 * degradation paths that matter most — a half-migrated or hand-edited row
 * must fall back to an ordinary card rather than render a one-member
 * "superset".
 */

type Row = Groupable & { name: string; rest_seconds?: number | null }

const ex = (name: string, composite_id: string | null = null,
            rest_seconds: number | null = null,
            composite_rest_seconds: number | null = null): Row =>
  ({ name, composite_id, rest_seconds, composite_rest_seconds })

const shape = (items: Row[]) =>
  groupIntoUnits(items).map((u) =>
    u.kind === 'single' ? u.item.name
      : `[${u.items.map((i) => i.name).join('+')}]`).join(' ')

describe('groupIntoUnits', () => {
  it('leaves ungrouped exercises alone', () => {
    expect(shape([ex('A'), ex('B'), ex('C')])).toBe('A B C')
  })

  it('collapses a pair into one unit', () => {
    expect(shape([ex('W'), ex('A'), ex('B', 'c1'), ex('C', 'c1'), ex('D')]))
      .toBe('W A [B+C] D')
  })

  it('collapses three members into one unit', () => {
    expect(shape([ex('A'), ex('B', 'c1'), ex('C', 'c1'), ex('D', 'c1')]))
      .toBe('A [B+C+D]')
  })

  it('keeps two groups separate', () => {
    expect(shape([ex('A', 'c1'), ex('B', 'c1'), ex('C', 'c2'), ex('D', 'c2')]))
      .toBe('[A+B] [C+D]')
  })

  it('degrades a lone member to an ordinary card', () => {
    // The server rejects these, but a hand-edited or partially migrated row
    // must not render as a one-exercise superset.
    expect(shape([ex('A'), ex('B', 'c1'), ex('C')])).toBe('A B C')
  })

  it('does not gather non-adjacent members', () => {
    // Grouping is positional because the order IS the prescription; gathering
    // would silently reorder the user's day.
    expect(shape([ex('A', 'c1'), ex('X'), ex('B', 'c1')])).toBe('A X B')
  })

  it('handles an empty list', () => {
    expect(groupIntoUnits([])).toEqual([])
  })

  it('reads the group through a custom accessor', () => {
    // The session page groups entries that WRAP the plan exercise.
    const entries = [
      { key: '1', planEx: ex('A', 'c1') },
      { key: '2', planEx: ex('B', 'c1') },
      { key: '3', planEx: null },
    ]
    const units = groupIntoUnits(entries, (e) => e.planEx ?? undefined)
    expect(units.map((u) => u.kind)).toEqual(['composite', 'single'])
  })
})

describe('compositeLabel', () => {
  it('names a group by its size', () => {
    // Calling a three-exercise block a "superset" is the kind of small
    // wrongness that makes the app feel untrustworthy to someone who trains.
    expect(compositeLabel(2)).toBe('Superset')
    expect(compositeLabel(3)).toBe('Triset')
    expect(compositeLabel(4)).toBe('Giant set')
  })
})

describe('compositeRounds', () => {
  it('is the members set count', () => {
    expect(compositeRounds([4, 4])).toBe(4)
  })

  it('leaves a gap rather than breaking on a mismatch', () => {
    expect(compositeRounds([4, 3])).toBe(4)
  })

  it('is zero for no members', () => {
    expect(compositeRounds([])).toBe(0)
  })
})

describe('compositeRest', () => {
  it('prefers the container over any member', () => {
    expect(compositeRest([
      ex('A', 'c1', 60, 90), ex('B', 'c1', 120, 90),
    ])).toBe(90)
  })

  it('falls back to the last member for pre-container data', () => {
    // Not dead code: this is exactly what the card did before containers
    // existed, and what the migration used to populate them — so the two
    // agree on the same answer.
    expect(compositeRest([ex('A', 'c1', 60), ex('B', 'c1', 90)])).toBe(90)
  })

  it('skips a trailing member with no rest', () => {
    expect(compositeRest([ex('A', 'c1', 60), ex('B', 'c1', null)])).toBe(60)
  })

  it('is null when nothing has any rest', () => {
    expect(compositeRest([ex('A', 'c1'), ex('B', 'c1')])).toBeNull()
  })
})
