import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ChatMessage, type ChatSessionDetail } from '../lib/api'
import { streamChatMessage, type StreamEvent } from '../lib/stream'
import { getToken } from '../lib/auth'
import { getErrorMessage, toFriendlyError, type FriendlyError } from '../lib/errors'
import type { ProposedChange } from '../components/chat/PlanChangeCard'
import * as pendingTurn from '../lib/pendingTurn'

/**
 * A turn that failed or was stopped, kept so the user can act on it.
 *
 * The old hook set an `error` string and cleared `streamingContent` on both
 * the error event and the catch path, so a partial reply the SERVER had
 * already saved was thrown away on the client. The banner then vanished on
 * reload, which made failures invisible after a refresh -- you would come
 * back to your own question and no sign anything had gone wrong.
 */
export interface FailedTurn {
  /** The exact text to resend, so retrying never means retyping. */
  content: string
  extra?: Record<string, unknown>
  error: FriendlyError
  /** Whatever streamed before it died. The server kept it; so do we. */
  partial: string
  stopped: boolean
}

export function useChatSession(sessionId: string | undefined) {
  const [session, setSession] = useState<ChatSessionDetail | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [failedTurn, setFailedTurn] = useState<FailedTurn | null>(null)
  /** Seconds in flight, for honest progress copy rather than endless dots. */
  const [elapsed, setElapsed] = useState(0)
  const [lastEvent, setLastEvent] = useState<StreamEvent | null>(null)
  /**
   * The step in progress. Measured on staging, 30.7s of a 31.9s turn passed
   * before the first character arrived -- all of it in tool rounds, with
   * nothing on screen but dots. This says what is actually happening.
   */
  const [status, setStatus] = useState<string | null>(null)
  /**
   * The draft awaiting confirmation, taken from the `done` payload rather
   * than parsed out of the reply text -- the card has to show exactly what
   * the server recorded, and the change_id has to tie the confirmation to it.
   */
  const [proposedChange, setProposedChange] = useState<ProposedChange | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  /*
   * A turn still on record when we mount was interrupted -- the tab was
   * backgrounded and discarded mid-stream, which on iOS Safari is routine.
   * Restoring it as a failed turn means the question and the partial reply
   * come back with Retry attached, instead of the message vanishing and the
   * user retyping it.
   */
  useEffect(() => {
    if (!sessionId) return
    const stored = pendingTurn.read(sessionId)
    if (!stored) return
    pendingTurn.endTurn(sessionId)
    setFailedTurn({
      content: stored.content,
      extra: stored.extra,
      partial: stored.partial,
      stopped: false,
      error: toFriendlyError(new Error(
        'This reply was interrupted when the app went to the background.')),
    })
  }, [sessionId])

  // The timer lives in its own effect so the send path does not have to
  // manage an interval while it is also managing a stream.
  useEffect(() => {
    if (!isSending) {
      setElapsed(0)
      return
    }
    const started = Date.now()
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [isSending])

  const loadSession = useCallback(async (id: string) => {
    setIsLoading(true)
    setError(null)
    try {
      setSession(await api.chat.getSession(id))
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setIsLoading(false)
    }
  }, [])

  /** Cancel the turn in flight, keeping whatever has arrived. */
  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const send = useCallback(
    async (content: string, extra?: Record<string, unknown>, isRetry = false) => {
      if (!sessionId || isSending) return
      const token = getToken()
      if (!token) return

      // On a retry the user's message is already in the transcript; adding it
      // again would show their question twice.
      if (!isRetry) {
        const userMsg: ChatMessage = {
          id: `temp-${Date.now()}`,
          role: 'user',
          content,
          created_at: new Date().toISOString(),
        }
        setSession((prev) =>
          prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev,
        )
      }

      const controller = new AbortController()
      abortRef.current = controller

      setIsSending(true)
      setStreamingContent('')
      setError(null)
      setFailedTurn(null)
      setLastEvent(null)
      setStatus(null)
      setProposedChange(null)

      let accumulated = ''
      pendingTurn.beginTurn(sessionId, content, extra)
      // Throttled: one write per chunk would hit localStorage hundreds of
      // times a turn, synchronously, on the main thread.
      let lastWrite = 0
      try {
        for await (const event of streamChatMessage(
          sessionId, content, token, extra, controller.signal,
        )) {
          if (event.error) {
            // An `error` frame is never followed by `done` -- the server
            // returns immediately after sending it.
            pendingTurn.endTurn(sessionId)
            setFailedTurn({
              content, extra, partial: accumulated, stopped: false,
              error: toFriendlyError(new Error(event.error)),
            })
            return
          }
          if (event.status) {
            setStatus(event.status)
          }
          if (event.chunk) {
            // Text has started; the step line has served its purpose.
            setStatus(null)
            accumulated += event.chunk
            setStreamingContent(accumulated)
            if (Date.now() - lastWrite > 1000) {
              lastWrite = Date.now()
              pendingTurn.updatePartial(sessionId, accumulated)
            }
          }
          if (event.done && event.message_id) {
            pendingTurn.endTurn(sessionId)
            setLastEvent(event)
            setProposedChange(
              (event.proposed_change as ProposedChange | null) ?? null,
            )
            const assistantMsg: ChatMessage = {
              id: event.message_id,
              role: 'assistant',
              content: accumulated,
              created_at: new Date().toISOString(),
            }
            setSession((prev) =>
              prev ? { ...prev, messages: [...prev.messages, assistantMsg] } : prev,
            )
            setStreamingContent('')
          }
        }
      } catch (err) {
        pendingTurn.endTurn(sessionId)
        const friendly = toFriendlyError(err)
        setFailedTurn({
          content, extra, partial: accumulated,
          stopped: friendly.message === 'Stopped.',
          error: friendly,
        })
      } finally {
        abortRef.current = null
        setIsSending(false)
        setStatus(null)
      }
    },
    [sessionId, isSending],
  )

  const sendMessage = useCallback(
    (content: string, extra?: Record<string, unknown>) => send(content, extra, false),
    [send],
  )

  /** Resend the failed turn verbatim. No retyping, ever. */
  const retry = useCallback(() => {
    if (!failedTurn) return
    return send(failedTurn.content, failedTurn.extra, true)
  }, [failedTurn, send])

  const dismissFailure = useCallback(() => {
    if (sessionId) pendingTurn.endTurn(sessionId)
    setFailedTurn(null)
  }, [sessionId])
  const clearProposal = useCallback(() => setProposedChange(null), [])

  return {
    session, isLoading, isSending, streamingContent, error,
    failedTurn, elapsed, lastEvent, proposedChange, status,
    loadSession, sendMessage, retry, stop, dismissFailure, clearProposal,
    setSession,
  }
}
