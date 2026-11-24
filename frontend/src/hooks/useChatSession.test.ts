import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useChatSession } from './useChatSession'
import * as stream from '../lib/stream'
import * as auth from '../lib/auth'
import { ApiError, api } from '../lib/api'

/**
 * The transport behaviours the user reported as broken.
 *
 * Before this, a failed turn cleared `streamingContent` on BOTH the error
 * event and the catch path, so a partial reply the server had already saved
 * was thrown away client-side; the failure was a transient banner that
 * vanished on reload; there was no way to cancel; and retrying meant retyping.
 */

function streamOf(events: stream.StreamEvent[], thrown?: unknown) {
  return async function* () {
    for (const event of events) yield event
    if (thrown) throw thrown
  }
}

describe('useChatSession', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(auth, 'getToken').mockReturnValue('tok')
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1', title: 'T', source: 'main', messages: [],
    } as never)
  })

  it('keeps the partial reply when the stream fails', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'half a sen' }, { error: 'AI stream failed (402)' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    await waitFor(() => expect(result.current.failedTurn).not.toBeNull())
    expect(result.current.failedTurn!.partial).toBe('half a sen')
  })

  it('maps the failure instead of showing the raw code', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ error: 'AI stream failed (402)' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    await waitFor(() => expect(result.current.failedTurn).not.toBeNull())
    expect(result.current.failedTurn!.error.message).not.toMatch(/402/)
    expect(result.current.failedTurn!.error.message).toMatch(/our side/i)
  })

  it('keeps the text so a retry never means retyping', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ error: 'boom' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('swap my bench press') })

    await waitFor(() => expect(result.current.failedTurn).not.toBeNull())
    expect(result.current.failedTurn!.content).toBe('swap my bench press')
  })

  it('does not duplicate the user message on retry', async () => {
    const spy = vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ error: 'boom' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.loadSession('s1') })
    await act(async () => { await result.current.sendMessage('hello') })
    await waitFor(() => expect(result.current.failedTurn).not.toBeNull())

    const afterFirst = result.current.session!.messages.length
    spy.mockImplementation(
      streamOf([{ chunk: 'ok' }, { done: true, message_id: 'm1' }]) as never,
    )
    await act(async () => { await result.current.retry() })

    await waitFor(() => expect(result.current.isSending).toBe(false))
    // One assistant reply added, and NOT a second copy of the question.
    expect(result.current.session!.messages.length).toBe(afterFirst + 1)
    expect(
      result.current.session!.messages.filter((m) => m.content === 'hello').length,
    ).toBe(1)
  })

  it('reports a stopped turn as stopped, not failed', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'partial' }], new DOMException('x', 'AbortError')) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    await waitFor(() => expect(result.current.failedTurn).not.toBeNull())
    expect(result.current.failedTurn!.stopped).toBe(true)
    expect(result.current.failedTurn!.partial).toBe('partial')
  })

  it('appends the assistant message on a clean finish', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'Hello' }, { done: true, message_id: 'm1' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.loadSession('s1') })
    await act(async () => { await result.current.sendMessage('hi') })

    await waitFor(() => expect(result.current.isSending).toBe(false))
    const last = result.current.session!.messages.at(-1)!
    expect(last.role).toBe('assistant')
    expect(last.content).toBe('Hello')
    expect(result.current.failedTurn).toBeNull()
  })

  it('passes an abort signal so the turn can be stopped', async () => {
    const spy = vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ done: true, message_id: 'm1' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })
    expect(spy.mock.calls[0][4]).toBeInstanceOf(AbortSignal)
  })
})

/**
 * The proposal lifecycle.
 *
 * Reported as "it is not able to update the plan now and just doesn't give me
 * the button too". The server was never at fault: the draft it recorded stayed
 * applicable the whole time. The client threw the button away, because every
 * `done` frame assigned `proposed_change ?? null` -- so one follow-up question
 * after a proposal wiped it. Measured on production: draft c77d3a6c was
 * discarded by the UI and then applied cleanly by hand.
 */
describe('useChatSession proposals', () => {
  const proposal = {
    change_id: 'c1', plan_id: 'p1', needs_confirmation: true, applied: false,
    draft: [{ day: 'Push', action: 'remove', name: 'Dumbbell Lateral Raises' }],
  }

  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(auth, 'getToken').mockReturnValue('tok')
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1', title: 'T', source: 'main', messages: [],
    } as never)
  })

  async function proposeThen(result: { current: ReturnType<typeof useChatSession> }) {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: 'here it is' },
        { done: true, message_id: 'm1', proposed_change: proposal },
      ]) as never,
    )
    await act(async () => { await result.current.sendMessage('remove the raises') })
    await waitFor(() => expect(result.current.proposedChange).not.toBeNull())
  }

  it('keeps the draft when the next turn proposes nothing', async () => {
    const { result } = renderHook(() => useChatSession('s1'))
    await proposeThen(result)

    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: '90 seconds.' },
        { done: true, message_id: 'm2', proposed_change: null },
      ]) as never,
    )
    await act(async () => { await result.current.sendMessage('what is my rest time?') })

    // The draft is still on the server and still applicable, so the button
    // has to still be there.
    expect(result.current.proposedChange?.change_id).toBe('c1')
  })

  it('replaces the draft when a later turn proposes a new one', async () => {
    const { result } = renderHook(() => useChatSession('s1'))
    await proposeThen(result)

    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { done: true, message_id: 'm2',
          proposed_change: { ...proposal, change_id: 'c2' } },
      ]) as never,
    )
    await act(async () => { await result.current.sendMessage('actually, make it this') })
    expect(result.current.proposedChange?.change_id).toBe('c2')
  })

  it('clears the draft once it is confirmed', async () => {
    const { result } = renderHook(() => useChatSession('s1'))
    await proposeThen(result)
    vi.spyOn(api.workouts, 'applyPlanChange').mockResolvedValue({
      change_id: 'c1', applied: true, clean: true,
      verification_diff: {
        added: [], removed: [], changed: [], unapproved: [],
        keep_list_violations: [], clean: true,
      },
    } as never)

    await act(async () => { await result.current.confirmProposal() })
    expect(result.current.proposedChange).toBeNull()
    expect(result.current.appliedDiff?.clean).toBe(true)
  })

  it('keeps the draft when applying it fails, so the button survives', async () => {
    const { result } = renderHook(() => useChatSession('s1'))
    await proposeThen(result)
    vi.spyOn(api.workouts, 'applyPlanChange').mockRejectedValue(new Error('offline'))

    await act(async () => {
      await result.current.confirmProposal().catch(() => {})
    })
    expect(result.current.proposedChange?.change_id).toBe('c1')
  })

  it('adopts a draft the server was already holding', async () => {
    const { result } = renderHook(() => useChatSession('s1'))
    act(() => { result.current.adoptProposal(proposal) })
    expect(result.current.proposedChange?.change_id).toBe('c1')
  })
})

/**
 * Turns that used to end up somewhere other than where they started.
 *
 * None of this was covered: every existing test sends once, into a fixed
 * session id, and never unmounts. So two sends in one frame, a session
 * switch mid-turn, and an unmount mid-turn were all untested and all broken.
 */
describe('useChatSession concurrency', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(auth, 'getToken').mockReturnValue('tok')
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1', title: 'T', source: 'main', messages: [],
    } as never)
  })

  it('refuses a second send while one is in flight, in the same frame', async () => {
    const spy = vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      (() => async function* () {
        yield { chunk: 'working' }
        await new Promise(() => {})
      }()) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))

    // Both calls before React can re-render: this is a double-tap.
    let first: unknown, second: unknown
    await act(async () => {
      first = result.current.sendMessage('one')
      second = result.current.sendMessage('two')
      await Promise.resolve()
    })

    expect(spy).toHaveBeenCalledTimes(1)
    expect(await second).toBe(false)
    void first
  })

  it('says a message was not accepted when there is no session', async () => {
    const { result } = renderHook(() => useChatSession(undefined))
    let accepted: unknown
    await act(async () => { accepted = await result.current.sendMessage('hi') })
    expect(accepted).toBe(false)
  })

  it('says so, and explains, when the token has gone', async () => {
    vi.spyOn(auth, 'getToken').mockReturnValue(null as never)
    const { result } = renderHook(() => useChatSession('s1'))
    let accepted: unknown
    await act(async () => { accepted = await result.current.sendMessage('hi') })
    expect(accepted).toBe(false)
    await waitFor(() => expect(result.current.error).toMatch(/sign in/i))
  })

  it('abandons the stream when the surface unmounts', async () => {
    let signal: AbortSignal | undefined
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      ((_s: string, _c: string, _t: string, _e: unknown, sig: AbortSignal) => {
        signal = sig
        return (async function* () {
          yield { chunk: 'half' }
          await new Promise(() => {})
        })()
      }) as never,
    )
    const { result, unmount } = renderHook(() => useChatSession('s1'))
    await act(async () => {
      result.current.sendMessage('hi')
      await Promise.resolve()
    })
    expect(signal?.aborted).toBe(false)
    unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('gives optimistic messages ids that cannot collide', async () => {
    // A frozen clock, because the old id was `temp-${Date.now()}` and two
    // sends milliseconds apart happened to differ -- so without this the
    // test passes against the bug it exists to catch.
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'ok' }, { done: true, message_id: 'm1' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.loadSession('s1') })
    await act(async () => { await result.current.sendMessage('one') })
    await act(async () => { await result.current.sendMessage('two') })
    // Only the optimistic ids are this test's business; the assistant ids
    // come from the server and the mock reuses one.
    const temps = (result.current.session?.messages ?? [])
      .map((m) => m.id)
      .filter((id) => id.startsWith('temp-'))
    expect(temps).toHaveLength(2)
    expect(new Set(temps).size).toBe(2)
  })
})

/**
 * Telling the user the truth about how a turn ended.
 *
 * Each of these reported something that was not so: a hung server said the
 * user had stopped it, a cut-off stream looked like a finished answer, a
 * capped turn read as a confident one, and a draft the server had refused
 * outright kept offering a Confirm button that could only fail again.
 */
describe('useChatSession honesty', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(auth, 'getToken').mockReturnValue('tok')
    vi.spyOn(api.chat, 'getSession').mockResolvedValue({
      id: 's1', title: 'T', source: 'main', messages: [],
    } as never)
  })

  it('does not render the partial twice after a failure', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'half a sentence' }, { error: 'boom' }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.failedTurn?.partial).toBe('half a sentence')
    expect(result.current.streamingContent).toBe('')
  })

  it('keeps a draft that arrived on the error frame', async () => {
    const draft = { change_id: 'c1', plan_id: 'p1', draft: [] }
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: 'drafted it, then ' },
        { error: 'boom', proposed_change: draft },
      ]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('move my pull day') })

    expect(result.current.proposedChange).toEqual(draft)
  })

  it('does not blame the user when the connection went quiet', async () => {
    const idle = new stream.StreamIdleError()
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'thinking' }], idle) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.failedTurn?.stopped).toBe(false)
    expect(result.current.failedTurn?.error.message).not.toMatch(/^Stopped/)
    expect(result.current.failedTurn?.error.retryable).toBe(true)
  })

  it('still reports the user’s own Stop as stopped', async () => {
    const abort = new DOMException('aborted', 'AbortError')
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'thinking' }], abort) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.failedTurn?.stopped).toBe(true)
  })

  it('finishes cleanly on a done frame that saved nothing', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'text' }, { done: true }]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.loadSession('s1') })
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.isSending).toBe(false)
    expect(result.current.streamingContent).toBe('')
    expect(result.current.session?.messages.map((m) => m.role)).toEqual(['user'])
  })

  it('says when a reply was cut short', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: 'it stops mid-' },
        { done: true, message_id: 'm1', truncated: true },
      ]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.notice).toMatch(/cut short/i)
  })

  it('says when the agent ran out of steps', async () => {
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: 'partial' },
        { done: true, message_id: 'm1', stopped_early: true },
      ]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.notice).toMatch(/ran out of steps/i)
  })

  it('does not say it ran out of steps when it drafted a change', async () => {
    const draft = {
      change_id: 'c1', plan_id: 'p1', needs_confirmation: true,
      applied: false, draft: [],
    }
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: 'The change is drafted below.' },
        { done: true, message_id: 'm1', stopped_early: true,
          proposed_change: draft },
      ]) as never,
    )
    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('hi') })

    expect(result.current.proposedChange).toEqual(draft)
    expect(result.current.notice ?? '').not.toMatch(/ran out of steps/i)
  })

  it('drops a draft the server refuses as stale, and looks for a live one', async () => {
    const pending = {
      change_id: 'c2', plan_id: 'p1', needs_confirmation: true,
      applied: false, draft: [],
    }
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([
        { chunk: 'ok' },
        {
          done: true, message_id: 'm1',
          proposed_change: { change_id: 'c1', plan_id: 'p1', draft: [] },
        },
      ]) as never,
    )
    vi.spyOn(api.workouts, 'applyPlanChange').mockRejectedValue(
      new ApiError(409, 'That draft is out of date.'),
    )
    const pendingSpy = vi.spyOn(api.workouts, 'pendingChange')
      .mockResolvedValue({ proposed_change: pending } as never)

    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('move my pull day') })
    await act(async () => {
      await result.current.confirmProposal().catch(() => {})
    })

    await waitFor(() => expect(pendingSpy).toHaveBeenCalled())
    await waitFor(() =>
      expect(result.current.proposedChange).toEqual(pending),
    )
  })

  it('keeps the draft when the failure is only transient', async () => {
    const draft = { change_id: 'c1', plan_id: 'p1', draft: [] }
    vi.spyOn(stream, 'streamChatMessage').mockImplementation(
      streamOf([{ chunk: 'ok' }, { done: true, message_id: 'm1', proposed_change: draft }]) as never,
    )
    vi.spyOn(api.workouts, 'applyPlanChange').mockRejectedValue(
      new ApiError(503, 'Service unavailable'),
    )
    const pendingSpy = vi.spyOn(api.workouts, 'pendingChange')

    const { result } = renderHook(() => useChatSession('s1'))
    await act(async () => { await result.current.sendMessage('move my pull day') })
    await act(async () => {
      await result.current.confirmProposal().catch(() => {})
    })

    expect(result.current.proposedChange).toEqual(draft)
    expect(pendingSpy).not.toHaveBeenCalled()
  })
})
