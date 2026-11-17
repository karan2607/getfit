import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import WorkoutChatDrawer from './WorkoutChatDrawer'
import { api } from '../lib/api'

/**
 * The drawer's confirm surface.
 *
 * This is where a plan change is most naturally asked for -- the user is
 * looking at the plan -- and it shipped with NO confirm control at all. The
 * coach would propose, the server would record the draft, and no button ever
 * appeared, in that turn or any other. Reported as "it is not able to update
 * the plan now and just doesn't give me the button too".
 *
 * Every existing test rendered PlanChangeDraft directly with a proposal
 * handed to it as a prop, so nothing ever asked whether a proposal REACHES
 * the card. That is why a green suite said this was fine.
 */

const pending = {
  change_id: 'c1',
  plan_id: 'p1',
  needs_confirmation: true,
  applied: false,
  draft: [{ day: 'Push', action: 'remove', name: 'Dumbbell Lateral Raises' }],
}

describe('WorkoutChatDrawer', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.chat, 'listSessions').mockResolvedValue([] as never)
    vi.spyOn(api.chat, 'createSession').mockResolvedValue({ id: 's1' } as never)
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1', title: 'T', source: 'workout', messages: [],
    } as never)
  })

  it('offers the draft the server is already holding', async () => {
    vi.spyOn(api.workouts, 'pendingPlanChange').mockResolvedValue(
      { proposed_change: pending } as never,
    )
    render(<WorkoutChatDrawer isOpen onClose={vi.fn()} planId="p1" />)

    await waitFor(() =>
      expect(screen.getByText(/nothing applied yet/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /confirm/i })).toBeEnabled()
    expect(screen.getByText(/Remove\s+Dumbbell Lateral Raises/)).toBeInTheDocument()
  })

  it('shows no confirm card when nothing is pending', async () => {
    vi.spyOn(api.workouts, 'pendingPlanChange').mockResolvedValue(
      { proposed_change: null } as never,
    )
    render(<WorkoutChatDrawer isOpen onClose={vi.fn()} planId="p1" />)

    await waitFor(() => expect(api.workouts.pendingPlanChange).toHaveBeenCalled())
    expect(screen.queryByText(/nothing applied yet/i)).not.toBeInTheDocument()
  })

  it('does not ask for a pending draft while it is closed', () => {
    vi.spyOn(api.workouts, 'pendingPlanChange').mockResolvedValue(
      { proposed_change: pending } as never,
    )
    render(<WorkoutChatDrawer isOpen={false} onClose={vi.fn()} planId="p1" />)
    expect(api.workouts.pendingPlanChange).not.toHaveBeenCalled()
  })
})
