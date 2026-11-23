import { useState, useEffect, useRef } from 'react'
import { api, type WorkoutPlanPreview } from '../lib/api'
import { useChatSession } from '../hooks/useChatSession'
import ChatBubble from './chat/ChatBubble'
import ChatInput from './chat/ChatInput'
import { InFlight, TurnFailure } from './chat/TurnStatus'
import { PlanChangeDraft, PlanChangeResult } from './chat/PlanChangeCard'
import { getErrorMessage } from '../lib/errors'
import { useToast } from './Toast'

interface WorkoutChatDrawerProps {
  isOpen: boolean
  onClose: () => void
  planId?: string
  dayId?: string
  filterDayNumber?: number
  dietPlanId?: string
  mealId?: string
  onPlanUpdated?: () => void
  onDietPlanUpdated?: () => void
  planPreview?: WorkoutPlanPreview
  onPreviewUpdate?: (plan: WorkoutPlanPreview) => void
  /** True when the caller has unsaved set edits that a plan update would destroy. */
  hasUnsavedSets?: boolean
  /** Awaited before a plan edit is applied, so pending sets can be flushed. */
  onBeforePlanUpdate?: () => Promise<void>
}

export default function WorkoutChatDrawer({
  isOpen, onClose, planId, dayId, filterDayNumber, dietPlanId, mealId, onPlanUpdated, onDietPlanUpdated, planPreview, onPreviewUpdate,
  hasUnsavedSets, onBeforePlanUpdate,
}: WorkoutChatDrawerProps) {
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [initializing, setInitializing] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Whether the view is pinned to the bottom. Autoscroll only applies while it
  // is, so scrolling up to re-read something is not fought on every token.
  const stickToBottomRef = useRef(true)
  // Messages typed while a reply is still streaming. Flushed as a single
  // follow-up once the in-flight reply lands, so the AI answers both.
  const queuedRef = useRef<string[]>([])
  const [queuedCount, setQueuedCount] = useState(0)

  /*
   * Every one of these is here because the drawer rendered NONE of them.
   *
   * It took `error`, which only `loadSession` ever sets -- so a stream that
   * 402s, 500s or drops left the partial reply on screen with a blinking
   * cursor and said nothing at all. No failure, no Retry, no Stop. This is
   * the surface plan changes are most naturally asked for, which made it the
   * surface where "it won't update my plan" was most often simply true and
   * invisible.
   */
  const {
    session, isLoading, isSending, streamingContent, error, loadSession,
    sendMessage, proposedChange, applying, appliedDiff, clearProposal,
    confirmProposal, adoptProposal,
    failedTurn, retry, stop, dismissFailure, elapsed, status,
  } = useChatSession(sessionId)
  const { showToast } = useToast()

  /*
   * A draft the server is already holding, picked up when the drawer opens.
   *
   * Without this a proposal is confirmable only during the turn that made it:
   * reload, reopen, or ask one more question and the button is gone while the
   * draft stays applicable. It also picks up a draft proposed from outside
   * the app entirely -- an agent on the user's own API key -- which otherwise
   * has no surface at all. The server withholds a draft the plan has moved
   * past, so anything offered here can actually be applied.
   */
  useEffect(() => {
    if (!isOpen || !planId) return
    let cancelled = false
    api.workouts.pendingPlanChange(planId)
      .then((res) => {
        if (!cancelled) adoptProposal(res.proposed_change)
      })
      .catch(() => {
        // A plan with no pending draft is the normal case, not an error to
        // put in front of the user.
      })
    return () => { cancelled = true }
  }, [isOpen, planId, adoptProposal])

  /**
   * Confirm the draft the coach proposed.
   *
   * This drawer shipped WITHOUT any confirm control: the coach would propose
   * a plan change from here, the server would record it, and no button ever
   * appeared -- not in that turn, not afterwards. Asking for a plan change
   * while looking at the plan is the most natural place to ask, so this was
   * the surface where "it won't update my plan" was most likely to be true.
   */
  async function handleConfirmChange() {
    try {
      await confirmProposal()
      onPlanUpdated?.()
    } catch (err) {
      // This was `catch {}` with a comment. A 409 stale_draft or a 400
      // bad_draft went nowhere: the button flicked from "Applying..." back
      // to "Confirm" and the user pressed it again, and again. The hook
      // keeps the draft so the button stays available -- but only now is
      // there any way to learn why it did not work.
      showToast(getErrorMessage(err), 'error')
    }
  }

  const buildExtra = () => {
    const extra: Record<string, unknown> = {}
    if (planId) extra.plan_id = planId
    else if (planPreview) extra.plan_context = JSON.stringify(planPreview)
    if (dayId) extra.day_id = dayId
    if (dietPlanId) extra.diet_plan_id = dietPlanId
    if (mealId) extra.meal_id = mealId
    return Object.keys(extra).length ? extra : undefined
  }

  // `isSending` as a ref as well as state.
  //
  // The queue read it from the render closure, so a message sent in the same
  // frame the reply landed was queued against a turn that had already
  // finished -- and the drain effect, which only runs on a transition, had
  // already run with an empty queue. The composer cleared, "1 message
  // queued" appeared, and it sat there until the NEXT turn ended.
  const isSendingRef = useRef(false)
  useEffect(() => { isSendingRef.current = isSending }, [isSending])

  const handleSend = (content: string) => {
    if (isSendingRef.current) {
      // Don't drop it — hold it until the current reply finishes.
      queuedRef.current.push(content)
      setQueuedCount(queuedRef.current.length)
      return
    }
    sendMessage(content, buildExtra())
  }

  // Drain the queue when the in-flight reply completes.
  useEffect(() => {
    if (isSending || queuedRef.current.length === 0) return
    const pending = queuedRef.current
    queuedRef.current = []
    setQueuedCount(0)
    const combined = pending.length === 1
      ? pending[0]
      : `${pending.join('\n\n')}\n\n(Please address each of the points above.)`
    sendMessage(combined, buildExtra())
  }, [isSending]) // eslint-disable-line react-hooks/exhaustive-deps

  /*
   * Open the conversation.
   *
   * The `.catch(() => {})` here used to leave `sessionId` undefined with a
   * perfectly usable-looking composer above it: `busy` went false, the input
   * enabled, and every message typed into it was dropped on the floor by the
   * hook's `if (!sessionId) return`. Offline, or one 500 from this endpoint,
   * and the drawer silently ate everything you said to it.
   */
  const [startFailed, setStartFailed] = useState(false)
  const [startAttempt, setStartAttempt] = useState(0)
  useEffect(() => {
    if (!isOpen || sessionId) return
    setInitializing(true)
    setStartFailed(false)
    api.chat.createSession({ source: 'embedded' })
      .then((s) => {
        setSessionId(s.id)
        loadSession(s.id)
      })
      .catch(() => setStartFailed(true))
      .finally(() => setInitializing(false))
  }, [isOpen, startAttempt]) // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Keep the newest message in view -- but only when the user has not scrolled
   * away.
   *
   * This effect runs on every streamed token. With an unconditional
   * scrollIntoView it re-pinned the view continuously, so any attempt to
   * scroll up while the AI was typing was immediately yanked back: reported as
   * "When ai is typing on workout page, I cannot scroll".
   *
   * 'auto' rather than 'smooth' during streaming: a smooth scroll per token
   * queues animations faster than they finish, which is the other half of why
   * the panel felt stuck.
   */
  useEffect(() => {
    if (!stickToBottomRef.current) return
    bottomRef.current?.scrollIntoView({
      behavior: streamingContent ? 'auto' : 'smooth',
    })
  }, [session?.messages.length, streamingContent])

  /**
   * Recover the reply when the browser backgrounds the tab mid-stream.
   *
   * Reported as "if ai is responding and I minimize the browser, the network
   * call always fails". The browser aborts the SSE connection when the tab is
   * hidden and there is no way to stop it from the page.
   *
   * But the server persists the assistant message in a `finally` block the
   * moment the generator ends (api/views.py chat_message_stream), so the reply
   * is NOT lost -- only the client's view of it. Reloading the session on
   * re-focus surfaces whatever landed instead of leaving a dead error state.
   */
  useEffect(() => {
    if (!isOpen || !sessionId) return
    function onVisible() {
      // Not while a turn is in flight. `loadSession` replaces the whole
      // session object, so a reload resolving just after `done` appended the
      // reply hands back a snapshot taken before the server saved it -- and
      // the reply vanishes in front of the user.
      if (document.visibilityState !== 'visible') return
      if (isSendingRef.current) return
      loadSession(sessionId!)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [isOpen, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    // 48px of slack, so a near-bottom position still counts as pinned.
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    stickToBottomRef.current = distance < 48
  }

  // Sending a message is an explicit "take me to the bottom".
  useEffect(() => {
    if (isSending) stickToBottomRef.current = true
  }, [isSending])

  if (!isOpen) return null

  const busy = initializing || isLoading
  const isPreviewMode = !planId && !!planPreview
  const isEditDayMode = !!planId && !!dayId
  const isDietMode = !!dietPlanId && !planId

  return (
    <>
      {/*
        A side panel on desktop, a full screen on a phone.

        It used to be a 400px panel everywhere. On a 390px screen that is the
        whole width bar a 32px sliver of the page behind it, so it read as a
        broken layout rather than a panel -- and at z-40 it sat UNDER the
        bottom nav (z-50), which drew six tabs straight across it. There is
        no room for a drawer at this width: the thing being revealed is
        already the size of the screen, so it may as well own the screen.
      */}
      <div
        className="fixed inset-0 z-[55] bg-black/30 animate-fade-in hidden md:block"
        onClick={onClose}
      />
      <div className="fixed inset-0 z-[60] bg-white flex flex-col animate-slide-in
                      md:inset-y-0 md:left-auto md:right-0 md:w-[400px]
                      md:max-w-[calc(100vw-2rem)] md:shadow-2xl">
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4 bg-brand-500"
          style={{ paddingTop: 'max(1rem, env(safe-area-inset-top))' }}
        >
          <div>
            <p className="text-base font-bold text-white">AI Trainer</p>
            <p className="text-xs text-white/75">
              {isPreviewMode
                ? 'Describe changes to refine this plan'
                : isEditDayMode
                  ? 'Describe changes to update this day'
                  : isDietMode
                    ? 'Ask me to modify your diet plan'
                    : 'Ask anything about your workout'}
            </p>
          </div>
          <button onClick={onClose} className="text-white/75 hover:text-white text-xl leading-none transition-colors">
            ✕
          </button>
        </div>

        {/* Messages */}
        <div ref={scrollRef} onScroll={handleScroll}
             className="flex-1 overflow-y-auto overscroll-contain px-4 py-4">
          {busy ? (
            <div className="flex items-center justify-center h-full">
              <div className="w-5 h-5 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : !session || session.messages.length === 0 && !streamingContent ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-6 py-8">
              <div className="text-4xl mb-3">{isPreviewMode || isEditDayMode ? '✏️' : isDietMode ? '🥗' : '🏋️'}</div>
              <p className="text-sm font-semibold text-gray-800 mb-1">
                {isPreviewMode ? 'Refine your plan' : isEditDayMode ? 'Edit this day' : isDietMode ? 'Your AI Nutritionist' : 'Your AI Trainer'}
              </p>
              <p className="text-sm text-gray-500">
                {isPreviewMode
                  ? 'Tell me what to change — swap exercises, adjust volume, change focus, or anything else.'
                  : isEditDayMode
                    ? 'Describe what to change for this day — swap exercises, add sets, change the focus, or anything else.'
                    : isDietMode
                      ? 'Ask me to adjust meals, swap foods, change macros, add a day, or anything else about your diet plan.'
                      : 'Ask me to modify this plan, swap exercises, adjust difficulty, or create a whole new plan.'}
              </p>
            </div>
          ) : (
            <>
              {session.messages.map((msg) => (
                <ChatBubble
                  key={msg.id}
                  role={msg.role}
                  content={msg.content}
                  planId={msg.role === 'assistant' ? planId : undefined}
                  dietPlanId={msg.role === 'assistant' ? dietPlanId : undefined}
                  filterDayNumber={filterDayNumber}
                  onSaved={onPlanUpdated}
                  onDietSaved={onDietPlanUpdated}
                  onBeforeSave={onBeforePlanUpdate}
                  onPreviewUpdate={msg.role === 'assistant' ? onPreviewUpdate : undefined}
                />
              ))}

              {streamingContent && (
                <ChatBubble
                  role="assistant"
                  content={streamingContent}
                  isStreaming
                  planId={planId}
                  dietPlanId={dietPlanId}
                  filterDayNumber={filterDayNumber}
                  onSaved={onPlanUpdated}
                  onDietSaved={onDietPlanUpdated}
                  onBeforeSave={onBeforePlanUpdate}
                  onPreviewUpdate={onPreviewUpdate}
                />
              )}

              {/* Whatever arrived before the turn died. Rendered only once
                  the turn is over, so it never doubles the streaming bubble. */}
              {!isSending && failedTurn?.partial && (
                <ChatBubble role="assistant" content={failedTurn.partial} />
              )}

              {/* InFlight, not the bare dots: it carries the elapsed time, the
                  step the agent is on, and a Stop button. The drawer had no
                  way to stop a turn at all. */}
              {isSending && (
                <InFlight elapsed={elapsed} onStop={stop} status={status} />
              )}

              {failedTurn && (
                <TurnFailure
                  turn={failedTurn}
                  onRetry={retry}
                  onDismiss={dismissFailure}
                />
              )}

              {error && (
                <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-4">
                  {error}
                </div>
              )}
              <div ref={bottomRef} />
            </>
          )}

          {/* OUTSIDE the transcript branch. A draft picked up when the drawer
              opens can arrive before this session has a single message, and
              the empty state would otherwise hide the only control that can
              accept it. */}
          {proposedChange && (
            <PlanChangeDraft
              change={proposedChange}
              busy={applying}
              onConfirm={handleConfirmChange}
              onCancel={clearProposal}
            />
          )}

          {appliedDiff && <PlanChangeResult diff={appliedDiff} />}
        </div>

        {/* Input — pb accounts for mobile bottom nav bar (~64px) */}
        {/* The 4rem was clearance for the bottom nav. The panel now covers
            the nav, so that was 64px of dead space between the composer and
            the keyboard. Safe-area inset instead, for the home indicator. */}
        <div
          className="px-4 pt-2 border-t border-gray-100"
          style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom))' }}
        >
          {hasUnsavedSets && isEditDayMode && (
            <p className="text-[11px] text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5 mb-2">
              You have unticked sets. They'll be saved automatically before any
              change is applied.
            </p>
          )}
          {startFailed && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl px-3 py-2 mb-2 flex items-center justify-between gap-3">
              <span>Couldn't start this chat.</span>
              <button
                type="button"
                onClick={() => setStartAttempt((n) => n + 1)}
                className="min-h-[44px] px-3 rounded-full border border-red-300 text-sm font-medium text-red-700"
              >
                Try again
              </button>
            </div>
          )}
          {queuedCount > 0 && (
            <p className="text-[11px] text-gray-400 px-2 pb-1">
              {queuedCount} message{queuedCount > 1 ? 's' : ''} queued
            </p>
          )}
          {/* `disabled` is only the hard block (session not ready). While a reply
              streams the input stays usable and messages queue instead. */}
          <ChatInput
            onSend={handleSend}
            disabled={busy || startFailed}
            isReplying={isSending}
          />
        </div>
      </div>
    </>
  )
}
