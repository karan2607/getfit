import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Chat from './Chat'
import { api } from '../lib/api'

/**
 * A draft the server is holding has to be answerable from the chat page.
 *
 * The drawer learned to pick one up; the chat page did not, and it is where
 * most plan changes are actually asked for. Reopening the very session that
 * proposed the change showed the coach's "confirm change <id>" message with
 * no button under it -- reproduced on production before this test was
 * written. The page has no plan id to look it up with, which is why the
 * lookup it calls is plan-agnostic.
 */

vi.mock('../components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const pending = {
  change_id: 'c1',
  plan_id: 'p1',
  needs_confirmation: true,
  applied: false,
  draft: [{ day: 'Push', action: 'remove', name: 'Dumbbell Lateral Raises' }],
}

const show = () =>
  render(
    <MemoryRouter initialEntries={['/chat/s1']}>
      <Routes><Route path="/chat/:sessionId" element={<Chat />} /></Routes>
    </MemoryRouter>,
  )

describe('Chat page', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.chat, 'listSessions').mockResolvedValue(
      [{ id: 's1', title: 'T' }] as never,
    )
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1',
      title: 'T',
      messages: [
        { id: 'm1', role: 'assistant', content: 'Confirm change c1 to apply it.' },
      ],
    } as never)
  })

  it('offers the draft the server is still holding', async () => {
    vi.spyOn(api.workouts, 'pendingChange').mockResolvedValue(
      { proposed_change: pending } as never,
    )
    show()

    await waitFor(() =>
      expect(screen.getByText(/nothing applied yet/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /confirm/i })).toBeEnabled()
    expect(screen.getByText(/Remove\s+Dumbbell Lateral Raises/)).toBeInTheDocument()
  })

  it('asks for the draft without needing a plan id', async () => {
    vi.spyOn(api.workouts, 'pendingChange').mockResolvedValue(
      { proposed_change: pending } as never,
    )
    show()
    await waitFor(() => expect(api.workouts.pendingChange).toHaveBeenCalled())
    expect(api.workouts.pendingChange).toHaveBeenCalledWith()
  })

  it('shows no card when nothing is pending', async () => {
    vi.spyOn(api.workouts, 'pendingChange').mockResolvedValue(
      { proposed_change: null } as never,
    )
    show()
    await waitFor(() => expect(api.workouts.pendingChange).toHaveBeenCalled())
    expect(screen.queryByText(/nothing applied yet/i)).not.toBeInTheDocument()
  })

  it('stays usable when the lookup fails', async () => {
    vi.spyOn(api.workouts, 'pendingChange').mockRejectedValue(new Error('offline'))
    show()
    await waitFor(() =>
      expect(screen.getByText(/Confirm change c1/)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/nothing applied yet/i)).not.toBeInTheDocument()
  })
})
