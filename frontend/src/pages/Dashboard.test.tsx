import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import Dashboard from './Dashboard'
import { api } from '../lib/api'

/**
 * The three plan states the Dashboard must tell apart.
 *
 * It only ever looked for `is_active`, so a saved-but-inactive plan was
 * indistinguishable from having none — reported as "the Dashboard said no
 * workout plan yet even though a saved plan existed". The copy then sent you
 * off to create another one, which is the wrong next step and the reason this
 * is worth a test rather than a tweak.
 */

const mockAuth = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockAuth() }))
vi.mock('../components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

type Plan = { id: string; title: string; is_active: boolean }

const plan = (over: Partial<Plan> = {}): Plan => ({
  id: 'p1', title: 'Beginner Full-Body Strength', is_active: false, ...over,
})

function stub(plans: Plan[]) {
  vi.spyOn(api.workouts, 'listPlans').mockResolvedValue(plans as never)
  vi.spyOn(api.workouts, 'listSessions').mockResolvedValue([] as never)
  vi.spyOn(api.diet, 'listPlans').mockResolvedValue([] as never)
  vi.spyOn(api.body, 'history').mockResolvedValue([] as never)
  vi.spyOn(api.workouts, 'getPlan').mockResolvedValue({
    ...plans.find((p) => p.is_active),
    days: [],
  } as never)
}

const show = () =>
  render(<MemoryRouter><Dashboard /></MemoryRouter>)

describe('Dashboard plan states', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockAuth.mockReturnValue({ user: { name: 'Karan', email: 'k@test.local' } })
  })

  it('offers plan creation when there are genuinely no plans', async () => {
    stub([])
    show()
    // "Let's get started" appears TWICE for a brand-new user: once in the
    // Today's Workout card and once in the gradient CTA below it. That
    // duplication predates this change and is left alone, so these assert
    // on each card's own unique call to action instead.
    expect(await screen.findByText('Create your plan')).toBeInTheDocument()
    expect(screen.getByText('Create workout plan')).toBeInTheDocument()
  })

  it('says a saved plan is inactive rather than missing', async () => {
    stub([plan()])
    show()
    expect(await screen.findByText(/saved but not active/)).toBeInTheDocument()
    // The actual regression: neither card may read as having no plan.
    expect(screen.queryByText('Create your plan')).not.toBeInTheDocument()
    expect(screen.queryByText('Create workout plan')).not.toBeInTheDocument()
  })

  it('names the saved plan and offers to activate it', async () => {
    stub([plan()])
    show()
    expect(await screen.findByText(/Beginner Full-Body Strength/)).toBeInTheDocument()
    expect(screen.getByText('Set it active')).toBeInTheDocument()
  })

  it('counts them when several are saved and none is active', async () => {
    stub([plan(), plan({ id: 'p2', title: 'Push Pull Legs' })])
    show()
    expect(await screen.findByText(/2 plans saved, none active/)).toBeInTheDocument()
    expect(screen.getByText('Choose a plan')).toBeInTheDocument()
  })

  it('never says "No workout plan" in the stat card while one is saved', async () => {
    stub([plan()])
    show()
    await screen.findByText(/saved but not active/)
    expect(screen.queryByText('No workout plan')).not.toBeInTheDocument()
    expect(screen.getByText('1 saved, none active')).toBeInTheDocument()
  })

  it('says so plainly when there is no plan at all', async () => {
    stub([])
    show()
    await screen.findByText('Create your plan')
    expect(screen.getByText('No workout plan')).toBeInTheDocument()
  })
})
