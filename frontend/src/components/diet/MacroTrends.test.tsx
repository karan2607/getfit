import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import MacroTrends from './MacroTrends'
import { api, type MealLogDay } from '../../lib/api'

/**
 * Requested: "Since we get so many meal details daily now, we should also add
 * charts with daily protein, carbs, and fiber data." There were no meal-log
 * charts, and fiber was not stored on a logged meal at all.
 */

const day = (date: string, over: Partial<MealLogDay> = {}): MealLogDay => ({
  date, meals: 0, calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: null, ...over,
})

// jsdom has no ResizeObserver, which recharts' ResponsiveContainer needs.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

describe('MacroTrends', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('shows a protein, carbs and fiber chart with daily averages', async () => {
    vi.spyOn(api.diet, 'getMealLogDaily').mockResolvedValue([
      day('2026-11-20'),
      day('2026-11-21', { meals: 3, protein_g: 120, carbs_g: 200, fiber_g: 20 }),
      day('2026-11-22', { meals: 2, protein_g: 140, carbs_g: 180, fiber_g: 30 }),
    ])
    render(<MacroTrends targets={{ protein_g: 150 }} />)

    expect(await screen.findByText('Protein')).toBeInTheDocument()
    expect(screen.getByText('Carbs')).toBeInTheDocument()
    expect(screen.getByText('Fiber')).toBeInTheDocument()
    // Averaged over days that had meals, not over the empty day too.
    expect(screen.getByText('130')).toBeInTheDocument()
    expect(screen.getByText('190')).toBeInTheDocument()
    expect(screen.getByText('25')).toBeInTheDocument()
    expect(screen.getByText('target 150g')).toBeInTheDocument()
  })

  it('does not read unrecorded fiber as 0g', async () => {
    vi.spyOn(api.diet, 'getMealLogDaily').mockResolvedValue([
      day('2026-11-22', { meals: 2, protein_g: 100, carbs_g: 150 }),
    ])
    render(<MacroTrends />)
    expect(await screen.findByText(/No fiber logged/)).toBeInTheDocument()
  })

  it('renders nothing when no meals were logged', async () => {
    const spy = vi.spyOn(api.diet, 'getMealLogDaily').mockResolvedValue([day('2026-11-22')])
    const { container } = render(<MacroTrends />)
    await vi.waitFor(() => expect(spy).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
