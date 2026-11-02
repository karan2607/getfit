import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useChatSession } from './useChatSession'
import * as stream from '../lib/stream'
import * as auth from '../lib/auth'
import { api } from '../lib/api'

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
