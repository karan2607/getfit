import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ChatMessage, type ChatSessionDetail } from '../lib/api'
import { streamChatMessage, type StreamEvent } from '../lib/stream'
import { getToken } from '../lib/auth'
import { getErrorMessage, toFriendlyError, type FriendlyError } from '../lib/errors'
import type {
  ProposedChange, VerificationDiff,
} from '../components/chat/PlanChangeCard'
import * as pendingTurn from '../lib/pendingTurn'

/**
 * An id for an optimistic message, unique even within one millisecond.
 *
 * `temp-${Date.now()}` collided whenever two sends landed in the same tick,
 * giving React two children with the same key.
 */
function newId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    // Older Safari and any non-secure context.
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`
  }
}

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

  /** Applying the confirmed draft, so both surfaces can disable the button. */
  const [applying, setApplying] = useState(false)
  /** The verification diff of the change just applied, for the result card. */
  const [appliedDiff, setAppliedDiff] = useState<VerificationDiff | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  /*
   * In-flight as a ref, not only as state.
   *
   * The guard in `send` read the state captured in its closure, which does
   * not update within a frame -- so a double-tap on Send, or two Enters in
   * quick succession, both passed it and started two streams against the
   * same session.
   */
  const isSendingRef = useRef(false)

  /*
   * A draft belongs to the session it was proposed in. It no longer clears
   * itself on the next turn (that was the bug), so it has to be dropped
   * explicitly when the surface moves to another conversation -- otherwise
   * one session's pending change would offer itself in another.
   *
   * Only on a move BETWEEN conversations, though. The workout drawer creates
   * its session asynchronously, so `sessionId` goes undefined -> 's1' on
   * open, and treating that as a move cleared the draft the drawer had just
   * adopted -- the card appeared and then vanished a tick later. Arriving at
   * a conversation is not leaving one.
   */
  const previousSession = useRef(sessionId)
  useEffect(() => {
    const moved = previousSession.current !== undefined
      && previousSession.current !== sessionId
    previousSession.current = sessionId
    if (!moved) return
    setProposedChange(null)
    setAppliedDiff(null)
  }, [sessionId])

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

  /*
   * Abandon the turn when this surface goes away.
   *
   * Nothing did this: abort was reachable only from the user's Stop button.
   * So a reply whose turn outlived its component kept running, and on `done`
   * was appended to whatever session was mounted by then -- send in chat A,
   * tap chat B, and A's answer lands in B's transcript while B's composer
   * sits disabled for a turn it never sent.
   *
   * Unmount is also the one case where the turn genuinely cannot be
   * recovered into the UI, so `pendingTurn` keeps its record: the next mount
   * restores it rather than finding nothing.
   */
  useEffect(() => () => { abortRef.current?.abort() }, [])

  const send = useCallback(
    async (content: string, extra?: Record<string, unknown>, isRetry = false) => {
      /*
       * Every refusal here returns a reason, and the caller only clears the
       * composer on `true`.
       *
       * These were four silent `return`s. `isSending` is state captured in
       * this closure, so two Enters in the same frame BOTH passed it: two
       * user bubbles, two concurrent streams, and abortRef overwritten so
       * the first could never be stopped. A missing session or token simply
       * ate the message -- the drawer over a failed createSession swallowed
       * everything typed into it -- and ChatInput had already blanked the
       * textarea, so the words were gone too.
       */
      if (!sessionId) return false
      if (isSendingRef.current) return false
      const token = getToken()
      if (!token) {
        setError('Sign in again to send this.')
        return false
      }
      isSendingRef.current = true

      // On a retry the user's message is already in the transcript; adding it
      // again would show their question twice.
      if (!isRetry) {
        const userMsg: ChatMessage = {
          // A random id, not a timestamp: two sends in the same millisecond
          // produced duplicate React keys.
          id: `temp-${newId()}`,
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
      // The pending draft deliberately SURVIVES a new turn. Clearing it here
      // was the worse half of the bug: the Confirm button disappeared the
      // instant the user typed anything, including "what does that change?"
      // -- so asking a question about a proposal destroyed the only way to
      // accept it. A draft is cancelled by the user or replaced by a newer
      // one; sending a message is neither.
      //
      // The applied RESULT card does go: it describes the previous turn, and
      // leaving it above a new conversation reads as if it just happened.
      setAppliedDiff(null)

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
            // ONLY when this turn produced one. The old line assigned
            // `?? null`, so any later message threw a live draft away: ask
            // "what's my rest time again?" after a proposal and the Confirm
            // button vanished, while the draft sat on the server perfectly
            // applicable. Clearing a proposal is something the USER does --
            // by confirming or cancelling it -- never a side effect of
            // asking another question.
            if (event.proposed_change) {
              setProposedChange(event.proposed_change as ProposedChange)
            }
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
        isSendingRef.current = false
        setIsSending(false)
        setStatus(null)
      }
      return true
    },
    [sessionId],
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

  /**
   * Apply the draft the user just confirmed, and keep the diff.
   *
   * Lives in the hook rather than in the page because there are TWO chat
   * surfaces -- the Chat page and the workout drawer -- and the drawer shipped
   * without any confirm control at all. A copy of this in each page is how
   * that happens: one surface gets the feature and the other is forgotten.
   *
   * No plan payload is sent. The server BUILDS it from the recorded draft and
   * the plan it already has, so the model never re-emits the plan -- which was
   * the original failure, where one dropped line in a 56-day reply deleted an
   * exercise and its history.
   *
   * Returns the diff, so the caller can warn when it is not clean.
   */
  const confirmProposal = useCallback(async () => {
    if (!proposedChange?.plan_id) return null
    setApplying(true)
    try {
      const res = await api.workouts.applyPlanChange(
        proposedChange.plan_id, proposedChange.change_id,
      )
      setAppliedDiff(res.verification_diff)
      setProposedChange(null)
      return res
    } finally {
      setApplying(false)
    }
  }, [proposedChange])

  /** Offer a draft this surface did not propose, e.g. one found on load. */
  const adoptProposal = useCallback((change: ProposedChange | null) => {
    if (change?.change_id) setProposedChange(change)
  }, [])

  return {
    session, isLoading, isSending, streamingContent, error,
    failedTurn, elapsed, lastEvent, proposedChange, status,
    applying, appliedDiff,
    loadSession, sendMessage, retry, stop, dismissFailure, clearProposal,
    confirmProposal, adoptProposal, setSession,
  }
}
