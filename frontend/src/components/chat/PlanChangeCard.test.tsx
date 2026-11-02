import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PlanChangeDraft, PlanChangeResult } from './PlanChangeCard'

/**
 * The trust moment. These assert on what the USER sees, because the failure
 * being prevented is a reply that says "plan updated successfully" while an
 * exercise nobody approved has quietly gone.
 */

const diff = (over: Partial<Parameters<typeof PlanChangeResult>[0]['diff']> = {}) => ({
  added: [], removed: [], changed: [],
  unapproved: [], keep_list_violations: [], clean: true,
  ...over,
})

describe('PlanChangeDraft', () => {
  const change = {
    change_id: 'c1',
    needs_confirmation: true,
    applied: false,
    draft: [{ day: 'Upper Push', name: 'Machine Chest Press' }],
  }

  it('says plainly that nothing has been applied', () => {
    render(<PlanChangeDraft change={change} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText(/nothing applied yet/i)).toBeInTheDocument()
  })

  it('lists the exact draft rather than a summary', () => {
    render(<PlanChangeDraft change={change} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText('Machine Chest Press')).toBeInTheDocument()
    expect(screen.getByText(/Upper Push/)).toBeInTheDocument()
  })

  it('shows an unknown exercise as a warning, not a blocker', () => {
    render(
      <PlanChangeDraft
        change={{ ...change, warnings: [{ type: 'unknown_exercise', name: 'Weighted Dips', message: 'Weighted Dips: new, not in your library' }] }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByText(/not in your library/)).toBeInTheDocument()
    // Still confirmable: novelty is not the hazard, silent near-matching is.
    expect(screen.getByRole('button', { name: /confirm/i })).toBeEnabled()
  })
})

describe('PlanChangeResult', () => {
  it('never reports success when something unapproved changed', () => {
    render(
      <PlanChangeResult
        diff={diff({
          removed: [{ name: 'Standing Calf Raise', day: 'Lower Body' }],
          unapproved: [{ name: 'Standing Calf Raise', day: 'Lower Body' }],
          clean: false,
        })}
      />,
    )
    expect(screen.queryByText(/^Done$/)).not.toBeInTheDocument()
    expect(screen.getByText(/not as approved/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Standing Calf Raise/).length).toBeGreaterThan(0)
  })

  it('calls out protected exercises that were affected', () => {
    render(
      <PlanChangeResult
        diff={diff({
          unapproved: [{ name: 'Standing Calf Raise', day: 'Lower Body' }],
          keep_list_violations: [{ name: 'Standing Calf Raise', day: 'Lower Body' }],
          clean: false,
        })}
      />,
    )
    expect(screen.getByText(/protected/i)).toBeInTheDocument()
  })

  it('shows proof of restraint on a clean change', () => {
    render(
      <PlanChangeResult
        diff={diff({ changed: [{ name: 'Machine Chest Press', day: 'Upper Push' }] })}
        untouched={24}
      />,
    )
    expect(screen.getByText(/^Done$/)).toBeInTheDocument()
    expect(screen.getByText(/24 other exercises untouched/i)).toBeInTheDocument()
  })

  it('does not claim restraint when the change was not clean', () => {
    render(
      <PlanChangeResult
        diff={diff({ unapproved: [{ name: 'X', day: 'D' }], clean: false })}
        untouched={24}
      />,
    )
    expect(screen.queryByText(/untouched/i)).not.toBeInTheDocument()
  })
})
