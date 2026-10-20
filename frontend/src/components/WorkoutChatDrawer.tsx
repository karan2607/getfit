import { useState, useEffect, useRef } from 'react'
import { api, type WorkoutPlanPreview } from '../lib/api'
import { useChatSession } from '../hooks/useChatSession'
import ChatBubble from './chat/ChatBubble'
import ChatInput from './chat/ChatInput'
import StreamingIndicator from './chat/StreamingIndicator'

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

  const { session, isLoading, isSending, streamingContent, error, loadSession, sendMessage } =
    useChatSession(sessionId)

  const buildExtra = () => {
    const extra: Record<string, unknown> = {}
    if (planId) extra.plan_id = planId
    else if (planPreview) extra.plan_context = JSON.stringify(planPreview)
    if (dayId) extra.day_id = dayId
    if (dietPlanId) extra.diet_plan_id = dietPlanId
    if (mealId) extra.meal_id = mealId
    return Object.keys(extra).length ? extra : undefined
  }

  const handleSend = (content: string) => {
    if (isSending) {
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

  useEffect(() => {
    if (!isOpen || sessionId) return
    setInitializing(true)
    api.chat.createSession({ source: 'embedded' })
      .then((s) => {
        setSessionId(s.id)
        loadSession(s.id)
      })
      .catch(() => {})
      .finally(() => setInitializing(false))
  }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps

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
      if (document.visibilityState === 'visible') loadSession(sessionId!)
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
      <div className="fixed inset-0 z-30 bg-black/30 animate-fade-in" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-[400px] max-w-[calc(100vw-2rem)] z-40 bg-white shadow-2xl flex flex-col animate-slide-in">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 bg-brand-500">
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

              {isSending && !streamingContent && <StreamingIndicator />}

              {error && (
                <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-4">
                  {error}
                </div>
              )}
              <div ref={bottomRef} />
            </>
          )}
        </div>

        {/* Input — pb accounts for mobile bottom nav bar (~64px) */}
        <div className="px-4 pb-[calc(1rem+4rem)] sm:pb-4 pt-2 border-t border-gray-100">
          {hasUnsavedSets && isEditDayMode && (
            <p className="text-[11px] text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5 mb-2">
              You have unticked sets. They'll be saved automatically before any
              change is applied.
            </p>
          )}
          {queuedCount > 0 && (
            <p className="text-[11px] text-gray-400 px-2 pb-1">
              {queuedCount} message{queuedCount > 1 ? 's' : ''} queued
            </p>
          )}
          {/* `disabled` is only the hard block (session not ready). While a reply
              streams the input stays usable and messages queue instead. */}
          <ChatInput onSend={handleSend} disabled={busy} isReplying={isSending} />
        </div>
      </div>
    </>
  )
}
