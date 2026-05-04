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
}

export default function WorkoutChatDrawer({
  isOpen, onClose, planId, dayId, filterDayNumber, dietPlanId, mealId, onPlanUpdated, onDietPlanUpdated, planPreview, onPreviewUpdate,
}: WorkoutChatDrawerProps) {
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [initializing, setInitializing] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { session, isLoading, isSending, streamingContent, error, loadSession, sendMessage } =
    useChatSession(sessionId)

  const handleSend = (content: string) => {
    const extra: Record<string, unknown> = {}
    if (planId) extra.plan_id = planId
    else if (planPreview) extra.plan_context = JSON.stringify(planPreview)
    if (dayId) extra.day_id = dayId
    if (dietPlanId) extra.diet_plan_id = dietPlanId
    if (mealId) extra.meal_id = mealId
    sendMessage(content, Object.keys(extra).length ? extra : undefined)
  }

  useEffect(() => {
    if (!isOpen || sessionId) return
    setInitializing(true)
    api.chat.createSession()
      .then((s) => {
        setSessionId(s.id)
        loadSession(s.id)
      })
      .catch(() => {})
      .finally(() => setInitializing(false))
  }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [session?.messages.length, streamingContent])

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
        <div className="flex-1 overflow-y-auto px-4 py-4">
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

        {/* Input */}
        <div className="px-4 pb-4 pt-2 border-t border-gray-100">
          <ChatInput onSend={handleSend} disabled={isSending || busy} />
        </div>
      </div>
    </>
  )
}
