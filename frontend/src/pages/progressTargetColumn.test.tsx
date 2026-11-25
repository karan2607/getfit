import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ExerciseDetailsModal } from './Workouts'
import { api, type ExerciseHistory, type ExerciseHistoryPoint } from '../lib/api'

/**
 * Requested: "add another column that shows how much was the request set
 * during the exercises. If it said 8-10 and I did 10, I should see both in the
 * progress modal." The target only appeared as text in the date header.
 */

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { profile: { preferred_unit: 'lb' } } }),
}))

// jsdom has no ResizeObserver, which recharts' ResponsiveContainer needs.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

const point = (over: Partial<ExerciseHistoryPoint>): ExerciseHistoryPoint => ({
  workout_session__started_at: '2026-11-20T10:00:00Z',
  set_number: 1, weight_kg: 20, reps_completed: 10, duration_seconds: null,
  speed_kmh: null, incline_pct: null, distance_m: null, target_reps: '8-10',
  suggested_weight_kg: null,
  ...over,
})

const history = (logs: ExerciseHistoryPoint[]): ExerciseHistory => ({
  exercise_name: 'Dumbbell Bench Press',
  measurement_type: 'weight_reps',
  metric: 'weight_kg',
  logs,
})

describe('ExerciseDetailsModal target column', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('shows the prescribed target beside the reps done on each set', async () => {
    vi.spyOn(api.workouts, 'getExerciseHistory').mockResolvedValue(history([
      point({ set_number: 1, reps_completed: 10 }),
      point({ set_number: 2, reps_completed: 9 }),
    ]))
    render(<ExerciseDetailsModal name="Dumbbell Bench Press" onClose={() => {}} />)

    expect(await screen.findByRole('columnheader', { name: 'Target' })).toBeInTheDocument()
    const rows = screen.getAllByRole('row').filter((r) => within(r).queryByText('8-10'))
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('10')).toBeInTheDocument()
    expect(within(rows[1]).getByText('9')).toBeInTheDocument()
  })

  it('hides the column when no set has a target', async () => {
    vi.spyOn(api.workouts, 'getExerciseHistory').mockResolvedValue(history([
      point({ target_reps: null }),
    ]))
    render(<ExerciseDetailsModal name="Dumbbell Bench Press" onClose={() => {}} />)

    expect(await screen.findByRole('columnheader', { name: 'Set' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Target' })).not.toBeInTheDocument()
  })
})

/**
 * Requested: "the progress table still doesn't show what the recommendation
 * was from the past entries." The table showed what was done and what the
 * plan asked for; what the app suggested at the time was nowhere, so there
 * was no way to tell a matched suggestion from a missed one.
 */
describe('ExerciseDetailsModal suggested column', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('shows what the app recommended beside what was done', async () => {
    vi.spyOn(api.workouts, 'getExerciseHistory').mockResolvedValue(history([
      // 95 lb lifted against a 110 lb suggestion.
      point({ weight_kg: 43.0912, reps_completed: 8,
              suggested_weight_kg: 49.8951 }),
    ]))
    render(<ExerciseDetailsModal name="Dumbbell Bench Press" onClose={() => {}} />)

    expect(await screen.findByRole('columnheader', { name: 'Suggested' }))
      .toBeInTheDocument()
    const row = screen.getAllByRole('row').find((r) => within(r).queryByText('95lb'))
    expect(row).toBeDefined()
    expect(within(row as HTMLElement).getByText('110lb')).toBeInTheDocument()
  })

  it('hides the column when no set carries a suggestion', async () => {
    vi.spyOn(api.workouts, 'getExerciseHistory').mockResolvedValue(history([
      point({ suggested_weight_kg: null }),
    ]))
    render(<ExerciseDetailsModal name="Dumbbell Bench Press" onClose={() => {}} />)

    expect(await screen.findByRole('columnheader', { name: 'Set' }))
      .toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Suggested' })).toBeNull()
  })
})
