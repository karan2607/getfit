import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { api } from '../lib/api'

/**
 * The day's totals were rendered only when an active diet plan existed,
 * because they shared a block with the "vs Target" bars. Someone logging
 * meals without a plan -- which is most people before they make one --
 * could add food all day and never see what they had eaten.
 *
 * The targets still need a plan. The totals never did.
 */
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { profile: { preferred_unit: 'lb' } } }),
}))
vi.mock('../components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

globalThis.ResizeObserver ??= class {
  observe() {} unobserve() {} disconnect() {}
} as unknown as typeof ResizeObserver

const meals = [
  { id: '1', food_name: 'Oats', meal_type: 'breakfast', calories: 400,
    protein_g: 15, carbs_g: 60, fat_g: 8, date: '2026-11-24', quantity: 1,
    unit: 'serving' },
  { id: '2', food_name: 'Chicken', meal_type: 'lunch', calories: 500,
    protein_g: 45, carbs_g: 10, fat_g: 20, date: '2026-11-24', quantity: 1,
    unit: 'serving' },
]

describe('Diet totals without a plan', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.diet, 'getMealLogs').mockResolvedValue(meals as never)
    vi.spyOn(api.diet, 'listPlans').mockResolvedValue([] as never)
  })

  it("shows the day's calories even with no active plan", async () => {
    const { MealLogger } = await import('./Diet')
    render(<MealLogger />)
    await waitFor(() =>
      expect(screen.getAllByText(/900 kcal/).length).toBeGreaterThan(0))
  })

  it('does not pretend there is a target', async () => {
    const { MealLogger } = await import('./Diet')
    render(<MealLogger />)
    await waitFor(() =>
      expect(screen.getAllByText(/900 kcal/).length).toBeGreaterThan(0))
    expect(screen.queryByText(/vs target/i)).toBeNull()
  })
})
