import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import WorkoutChatDrawer from './WorkoutChatDrawer'
import { ApiError, api } from '../lib/api'
import * as stream from '../lib/stream'
import * as auth from '../lib/auth'

const showToast = vi.fn()
vi.mock('./Toast', () => ({ useToast: () => ({ showToast }) }))

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

/**
 * The drawer as a chat surface, not just a draft-adopter.
 *
 * Everything below was invisible before: the drawer destructured `error`
 * (which only `loadSession` ever sets) and rendered no failure state, no
 * Stop, no Retry. A stream that died left a half sentence with a blinking
 * cursor and no explanation -- on the surface where plan changes are most
 * often asked for, which is why "it won't update my plan" kept coming back.
 */
describe('WorkoutChatDrawer failures', () => {
  const streamOf = (events: stream.StreamEvent[], thrown?: unknown) =>
    async function* () {
      for (const e of events) yield e
      if (thrown) throw thrown
    }

  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(auth, 'getToken').mockReturnValue('tok')
    vi.spyOn(api.chat, 'createSession').mockResolvedValue({ id: 's1' } as never)
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1', title: 'T', source: 'embedded', messages: [],
    } as never)
    vi.spyOn(api.workouts, 'pendingPlanChange').mockResolvedValue(
      { proposed_change: null } as never,
    )
  })

  async function open() {
    render(<WorkoutChatDrawer isOpen onClose={vi.fn()} planId="p1" />)
    await waitFor(() => expect(api.chat.getSession).toHaveBeenCalled())
  }

  async function send(text = 'move my pull day') {
    const box = await screen.findByRole('textbox')
    fireEvent.change(box, { target: { value: text } })
    fireEvent.keyDown(box, { key: 'Enter' })
  }

  it('says so when the stream fails', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'half a ' }, { error: 'AI stream failed (402)' }]) as never,
    )
    await open()
    await send()
    await waitFor(() =>
      expect(screen.getAllByText(/AI is unavailable/i).length).toBeGreaterThan(0),
    )
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('keeps what arrived before the failure', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'half a sentence' }, { error: 'boom' }]) as never,
    )
    await open()
    await send()
    await waitFor(() =>
      expect(screen.getAllByText('half a sentence').length).toBeGreaterThan(0),
    )
  })

  it('offers a way to stop a turn', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      (() => async function* () {
        yield { chunk: 'thinking' }
        await new Promise(() => {})
      }()) as never,
    )
    await open()
    await send()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument(),
    )
  })

  it('does not silently eat messages when the chat cannot start', async () => {
    vi.spyOn(api.chat, 'createSession').mockRejectedValue(new Error('offline'))
    render(<WorkoutChatDrawer isOpen onClose={vi.fn()} planId="p1" />)
    await waitFor(() =>
      expect(screen.getByText(/Couldn't start this chat/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /try again/i })).toBeEnabled()
  })

  it('retries starting the chat when asked', async () => {
    const create = vi.spyOn(api.chat, 'createSession')
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ id: 's1' } as never)
    render(<WorkoutChatDrawer isOpen onClose={vi.fn()} planId="p1" />)
    await waitFor(() => screen.getByRole('button', { name: /try again/i }))
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.queryByText(/Couldn't start this chat/i)).not.toBeInTheDocument(),
    )
  })

  it('tells the user when applying a draft is refused', async () => {
    vi.spyOn(api.workouts, 'pendingPlanChange').mockResolvedValue({
      proposed_change: {
        change_id: 'c1', plan_id: 'p1', needs_confirmation: true,
        applied: false, draft: [{ day: 'Push', action: 'remove', name: 'Dips' }],
      },
    } as never)
    vi.spyOn(api.workouts, 'applyPlanChange').mockRejectedValue(
      new ApiError(409, 'That draft is no longer valid.'),
    )
    render(<WorkoutChatDrawer isOpen onClose={vi.fn()} planId="p1" />)
    await waitFor(() => screen.getByRole('button', { name: /confirm/i }))
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }))
    await waitFor(() => expect(showToast).toHaveBeenCalled())
    expect(String(showToast.mock.calls[0][0])).toMatch(/no longer valid/i)
  })
})
