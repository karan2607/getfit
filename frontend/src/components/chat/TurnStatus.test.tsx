import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InFlight } from './TurnStatus'

describe('InFlight', () => {
  it('shows the real step when the agent reports one', () => {
    render(<InFlight elapsed={12} onStop={vi.fn()} status="Reading week 4 of your plan" />)
    expect(screen.getByRole('status')).toHaveTextContent('Reading week 4 of your plan… 12s')
  })

  it('keeps the elapsed time alongside the step', () => {
    // A step with no elapsed time is indistinguishable from a hang.
    render(<InFlight elapsed={40} onStop={vi.fn()} status="Drafting the change" />)
    expect(screen.getByRole('status')).toHaveTextContent('40s')
  })

  it('falls back to the staged timer copy with no step', () => {
    render(<InFlight elapsed={3} onStop={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('Thinking… 3s')
  })

  it('explains a long wait rather than looking frozen', () => {
    render(<InFlight elapsed={50} onStop={vi.fn()} />)
    expect(screen.getByRole('status').textContent).toMatch(/45s|Waking up/)
  })
})
