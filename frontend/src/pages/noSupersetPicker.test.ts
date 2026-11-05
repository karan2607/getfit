import { describe, expect, it } from 'vitest'
import workoutsSource from './Workouts.tsx?raw'

/**
 * The workout screen had a "⟲ Supersets" button that let the user build
 * their own pairings. Supersets are the model's call, made when it builds or
 * edits the plan, so the session page must not offer a manual picker.
 */
describe('workout session page', () => {
  it('does not offer a manual superset picker', () => {
    expect(workoutsSource).not.toMatch(/SupersetPicker/)
    expect(workoutsSource).not.toContain('⟲ Supersets')
  })
})
