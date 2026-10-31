import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SupersetCard, { type SupersetMember } from './SupersetCard'
import type { Exercise, SetLog } from '../../lib/api'

/**
 * The three things reported about this card, asserted on what the user sees:
 *
 *   - progress read "0/8" for a 2-member 4-round superset (members x rounds,
 *     a number corresponding to nothing anyone does). It should read rounds.
 *   - the collapsed header showed only "A  +  B", no prescription at all.
 *   - rest was repeated under every round instead of stated once.
 *
 * Plus the one that matters most for data integrity: push-ups must not render
 * a weight input, because a value typed there is discarded by the server.
 */

const exercise = (over: Partial<Exercise> = {}): Exercise => ({
  id: 'e1', name: 'Barbell Bench Press', sets: 4, reps: '12',
  rest_seconds: 90, notes: '', order: 0, measurement_type: 'weight_reps',
  composite_id: 'c1', composite_rest_seconds: 90, ...over,
})

const log = (over: Partial<SetLog> = {}): SetLog => ({
  id: 's1', exercise_id: 'e1', exercise_name: 'Barbell Bench Press',
  set_number: 1, reps_completed: null, weight_kg: null, duration_seconds: null,
  speed_kmh: null, incline_pct: null, distance_m: null,
  measurement_type: 'weight_reps', is_completed: false, notes: '', ...over,
})

function members(): SupersetMember[] {
  const bench = exercise()
  const push = exercise({
    id: 'e2', name: 'Push-ups', reps: '10',
    measurement_type: 'bodyweight_reps',
  })
  return [
    { planEx: bench, measurementType: 'weight_reps',
      logs: [1, 2, 3, 4].map((n) => log({ id: `b${n}`, set_number: n })) },
    { planEx: push, measurementType: 'bodyweight_reps',
      logs: [1, 2, 3, 4].map((n) => log({
        id: `p${n}`, set_number: n, exercise_id: 'e2',
        exercise_name: 'Push-ups', measurement_type: 'bodyweight_reps' })) },
  ]
}

function renderCard(over: Record<string, unknown> = {}) {
  return render(
    <SupersetCard
      members={members()}
      unit="lb"
      isCompleted={false}
      isOpen
      onToggleOpen={vi.fn()}
      setFieldValue={() => null}
      isSetCompleted={() => false}
      onFieldChange={vi.fn()}
      onToggleSet={vi.fn()}
      onShowProgress={vi.fn()}
      {...over}
    />,
  )
}

describe('SupersetCard', () => {
  it('reports progress in ROUNDS, never members x rounds', () => {
    renderCard()
    expect(screen.getByText(/Round 1\/4/)).toBeInTheDocument()
    // The reported bug: 2 members x 4 rounds = "0/8".
    expect(screen.queryByText('0/8')).not.toBeInTheDocument()
  })

  it('shows each member with its own sets x reps', () => {
    renderCard()
    expect(screen.getByText('4 × 12')).toBeInTheDocument()   // bench
    expect(screen.getByText('4 × 10')).toBeInTheDocument()   // push-ups
  })

  it('states rest once, not under every round', () => {
    renderCard()
    const rest = screen.queryAllByText(/90s rest after each round/)
    expect(rest).toHaveLength(1)
  })

  it('names the group by its size', () => {
    renderCard()
    expect(screen.getByText(/Superset/)).toBeInTheDocument()
  })

  it('gives push-ups no weight input', () => {
    // Data integrity, not cosmetics: bodyweight_reps has no weight_kg in its
    // contract, so the server nulls anything typed there.
    //
    // Scoped to the push-ups ROW rather than counting labels card-wide:
    // labels render per member per round, so a card-wide count conflates "the
    // wrong member has a weight box" with "there are four rounds".
    renderCard()
    // One button per member PER ROUND, so take the first round's.
    const pushRow = screen.getAllByRole('button', { name: 'Push-ups' })[0]
      .closest('div.rounded-xl') as HTMLElement
    expect(pushRow).toBeTruthy()
    expect(pushRow.querySelectorAll('input[type=number]')).toHaveLength(1)
    expect(pushRow.textContent).toContain('Reps')
    expect(pushRow.textContent).not.toMatch(/\(lb\)/)

    // The bench row, by contrast, has both.
    const benchRow = screen.getAllByRole('button', { name: 'Barbell Bench Press' })[0]
      .closest('div.rounded-xl') as HTMLElement
    expect(benchRow.querySelectorAll('input[type=number]')).toHaveLength(2)
    expect(benchRow.textContent).toMatch(/\(lb\)/)
  })

  it('renders every round', () => {
    renderCard()
    for (const n of [1, 2, 3, 4]) {
      expect(screen.getByText(`Round ${n}`)).toBeInTheDocument()
    }
  })
})
