import { describe, expect, it } from 'vitest'
import type { WeightSuggestion } from './api'
import { actualsFromDrafts, weightToConfirm } from './weightSuggestions'

const suggestion: WeightSuggestion = {
  weight: 100, weight_kg: 45.359, unit: 'lb', confidence: 'high',
  basis: 'from this slot: 100lb x 12',
}

describe('weightToConfirm', () => {
  it('confirms the suggestion when the input was left showing it', () => {
    expect(weightToConfirm(undefined, null, suggestion)).toBe(45.359)
    expect(weightToConfirm({ reps_completed: 12 }, null, suggestion)).toBe(45.359)
  })

  it('never overrides a typed weight', () => {
    expect(weightToConfirm({ weight_kg: 50 }, null, suggestion)).toBeUndefined()
  })

  it('treats clearing the input as an edit, not a blank to fill', () => {
    expect(weightToConfirm({ weight_kg: null }, null, suggestion)).toBeUndefined()
  })

  it('never overrides a saved weight', () => {
    expect(weightToConfirm(undefined, 40, suggestion)).toBeUndefined()
  })

  it('has nothing to confirm without a number', () => {
    expect(weightToConfirm(undefined, null, undefined)).toBeUndefined()
    expect(weightToConfirm(undefined, null,
      { ...suggestion, weight: null, weight_kg: null, confidence: 'none' })).toBeUndefined()
  })
})

describe('actualsFromDrafts', () => {
  it('sends reps and completion, keyed back to exercise and set', () => {
    expect(actualsFromDrafts({
      'ex-1:2': { reps_completed: 15, is_completed: true },
      'ex-2:1': { is_completed: false },
    })).toEqual([
      { exercise_id: 'ex-1', set_number: 2, reps_completed: 15, is_completed: true },
      { exercise_id: 'ex-2', set_number: 1, is_completed: false },
    ])
  })

  it('skips orphans and weight-only drafts', () => {
    expect(actualsFromDrafts({
      'orphan:1': { is_completed: true },
      'ex-1:1': {} as { is_completed?: boolean },
    })).toEqual([])
  })
})
