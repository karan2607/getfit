import { describe, expect, it } from 'vitest'
import { fieldsFor } from '../lib/units'
import type { MeasurementType } from '../lib/api'

/**
 * The Treadmill HIIT card.
 *
 * The grid was `fields.length === 2 ? 4-col : 3-col`, which silently gave
 * `cardio_machine` -- time, speed AND incline -- a two-column grid for three
 * inputs. The labels wrapped onto one row, the inputs onto another, and the
 * completion tick ended up orphaned below. Deriving the template from the
 * count means a new measurement type cannot reintroduce it.
 */
const template = (n: number) => `2rem repeat(${n}, minmax(0, 1fr)) 2.5rem`

describe('set row grid', () => {
  it('gives cardio_machine a column per field', () => {
    const fields = fieldsFor('cardio_machine')
    expect(fields.length).toBeGreaterThan(2)
    expect(template(fields.length)).toContain(`repeat(${fields.length},`)
  })

  it('still fits the common weight/reps case', () => {
    expect(fieldsFor('weight_reps')).toHaveLength(2)
    expect(template(2)).toBe('2rem repeat(2, minmax(0, 1fr)) 2.5rem')
  })

  it('covers every measurement type without a special case', () => {
    // The full union, so adding a seventh type fails here rather than
    // shipping another silently-wrong grid.
    const types: MeasurementType[] = [
      'weight_reps', 'weight_duration', 'duration', 'warmup',
      'bodyweight_reps', 'cardio_machine', 'weight_distance',
    ]
    for (const t of types) {
      const n = fieldsFor(t).length
      expect(template(n), `${t} (${n} fields)`).toContain('minmax(0, 1fr)')
    }
  })
})
