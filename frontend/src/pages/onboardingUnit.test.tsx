import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { api } from '../lib/api'

/**
 * The unit step shows kilograms by default and only SET preferred_unit when
 * a button was tapped. Someone who typed 75 into a field labelled "Weight
 * (kg)" and pressed on submitted no unit at all, so the profile fell back
 * to the model default of lb and Profile then read 165.3 lb -- their
 * kilograms, relabelled.
 */
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { profile: {} }, setUser: vi.fn() }),
}))
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<object>('react-router-dom')),
  useNavigate: () => vi.fn(),
}))
vi.mock('../components/Toast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

describe('onboarding sends a unit', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('submits the unit shown, even when nobody tapped it', async () => {
    const update = vi.spyOn(api.profile, 'update').mockResolvedValue({} as never)
    vi.spyOn(api.auth, 'me').mockResolvedValue({} as never)

    const Onboarding = (await import('./Onboarding')).default
    render(<Onboarding />)

    // Fill what the body step requires -- and nothing else. The unit
    // buttons are deliberately never tapped: that is the reported case.
    for (let i = 0; i < 12; i++) {
      const next = screen.getByRole('button', { name: /continue|get started/i })
      if ((next as HTMLButtonElement).disabled) {
        await userEvent.type(screen.getByLabelText(/^age$/i), '31')
        await userEvent.selectOptions(screen.getByLabelText(/gender/i), 'male')
        await userEvent.type(screen.getByLabelText(/height/i), '178')
        await userEvent.type(screen.getByLabelText(/weight/i), '75')
        continue
      }
      await userEvent.click(next)
      if (update.mock.calls.length) break
    }

    await waitFor(() => expect(update).toHaveBeenCalled())
    const sent = update.mock.calls[0][0] as { preferred_unit?: string }
    expect(sent.preferred_unit).toBeDefined()
  })
})
