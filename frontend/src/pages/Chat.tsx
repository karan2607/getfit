import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type ChatSession, type VerificationDiff } from '../lib/api'
import { useChatSession } from '../hooks/useChatSession'
import { InFlight, TurnFailure } from '../components/chat/TurnStatus'
import { PlanChangeDraft, PlanChangeResult } from '../components/chat/PlanChangeCard'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'
import ChatBubble from '../components/chat/ChatBubble'
import ChatInput from '../components/chat/ChatInput'
import { SkeletonText } from '../components/Skeleton'
import PageHeader from '../components/PageHeader'
import ConfirmModal from '../components/ConfirmModal'

// ── Session list sidebar ──────────────────────────────────────────────────

function SessionList({
  sessions,
  activeId,
  onDelete,
  loading,
  onSelect,
}: {
  sessions: ChatSession[]
  activeId?: string
  onDelete: (id: string) => void
  loading: boolean
  onSelect?: () => void
}) {
  const navigate = useNavigate()
  const [confirmId, setConfirmId] = useState<string | null>(null)

  return (
    <div className="w-full bg-gray-50 flex flex-col h-full overflow-hidden">
      {confirmId && (
        <ConfirmModal
          title="Delete chat?"
          message="This conversation will be permanently deleted."
          confirmLabel="Delete"
          onConfirm={() => { onDelete(confirmId); setConfirmId(null) }}
          onClose={() => setConfirmId(null)}
        />
      )}
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5 pt-2">
        {loading ? (
          <div className="p-3 space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-9 bg-gray-200 rounded-lg animate-pulse" />
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-6 px-3">No chats yet.</p>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-center gap-1 px-2 py-2 rounded-lg cursor-pointer transition-colors ${
                s.id === activeId ? 'bg-accent-100 text-accent-700' : 'hover:bg-gray-200 text-gray-700'
              }`}
              onClick={() => { navigate(`/chat/${s.id}`); onSelect?.() }}
            >
              <span className="text-xs flex-1 truncate">{s.title}</span>
              <button
                onClick={(e) => { e.stopPropagation(); setConfirmId(s.id) }}
                className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 text-[10px] px-1 flex-shrink-0 transition-opacity"
                title="Delete chat"
              >
                ✕
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ── Chat detail pane ──────────────────────────────────────────────────────

function ChatPane({ sessionId }: { sessionId: string }) {
  const {
    session, isLoading, isSending, streamingContent, error,
    failedTurn, elapsed, proposedChange,
    loadSession, sendMessage, retry, stop, dismissFailure, clearProposal,
  } = useChatSession(sessionId)
  const [applying, setApplying] = useState(false)
  const [appliedDiff, setAppliedDiff] = useState<VerificationDiff | null>(null)
  const { showToast } = useToast()
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinnedToBottom, setPinnedToBottom] = useState(true)

  useEffect(() => {
    loadSession(sessionId)
  }, [sessionId, loadSession])

  // Autoscroll ONLY when the user is already at the bottom. The old effect
  // scrolled on every change, so reading back through a reply yanked you to
  // the end on each chunk -- the longer the answer, the harder it was to read.
  useEffect(() => {
    const el = scrollRef.current
    if (el && pinnedToBottom) el.scrollTop = el.scrollHeight
  }, [session?.messages.length, streamingContent, pinnedToBottom])

  /**
   * Apply the draft the user just confirmed.
   *
   * No plan payload is sent. The server BUILDS it from the recorded draft and
   * the plan it already has, so the model never re-emits the plan -- which
   * was the original failure, where one dropped line in a 56-day reply
   * deleted an exercise and its history.
   */
  async function handleConfirmChange() {
    if (!proposedChange?.plan_id) return
    setApplying(true)
    try {
      const res = await api.workouts.applyPlanChange(
        proposedChange.plan_id, proposedChange.change_id,
      )
      setAppliedDiff(res.verification_diff)
      if (!res.clean) {
        showToast('Applied, but not everything matched the draft', 'error')
      }
      clearProposal()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setApplying(false)
    }
  }

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    // A small tolerance: "near the bottom" is what a reader means by "at the
    // bottom", and exact equality fails on fractional scroll heights.
    setPinnedToBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex flex-col p-6">
        <SkeletonText lines={5} />
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Mobile back button */}
      <div className="md:hidden px-4 pt-3 pb-1">
        <button onClick={() => navigate('/chat')} className="text-sm text-gray-500 hover:text-gray-700">
          ← All chats
        </button>
      </div>
      {/* Messages */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-4 md:px-6 py-4"
      >
        {session?.messages.length === 0 && !streamingContent && (
          <div className="flex flex-col items-center justify-center h-full text-center py-16">
            <div className="text-4xl mb-3">💬</div>
            <p className="text-gray-500 text-sm">Ask me anything about fitness, workouts, or diet.</p>
          </div>
        )}

        {session?.messages.map((msg) => (
          <ChatBubble key={msg.id} role={msg.role} content={msg.content} />
        ))}

        {streamingContent && (
          <ChatBubble role="assistant" content={streamingContent} isStreaming />
        )}

        {!isSending && failedTurn?.partial && (
          <ChatBubble role="assistant" content={failedTurn.partial} />
        )}

        {isSending && <InFlight elapsed={elapsed} onStop={stop} />}

        {proposedChange && (
          <PlanChangeDraft
            change={proposedChange}
            busy={applying}
            onConfirm={handleConfirmChange}
            onCancel={clearProposal}
          />
        )}

        {appliedDiff && (
          <PlanChangeResult
            diff={appliedDiff}
            untouched={undefined}
          />
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
      </div>

      {!pinnedToBottom && (
        <button
          type="button"
          onClick={() => {
            const el = scrollRef.current
            if (el) el.scrollTop = el.scrollHeight
            setPinnedToBottom(true)
          }}
          className="self-center -mt-12 mb-2 min-h-[44px] px-4 rounded-full bg-ink-600 text-white text-sm shadow-lg"
        >
          New messages ↓
        </button>
      )}

      {/* Input */}
      <div
        className="px-6 pb-6 pt-2"
        style={{ paddingBottom: 'calc(1.5rem + env(safe-area-inset-bottom, 0px))' }}
      >
        <ChatInput onSend={sendMessage} disabled={isSending} />
        <p className="text-xs text-gray-400 text-center mt-2">
          AI can make mistakes. Consult a professional for medical advice.
        </p>
      </div>
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────

function ChatEmpty() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center py-24 px-6">
      <div className="text-5xl mb-4">🤖</div>
      <h2 className="text-lg font-semibold text-gray-900 mb-2">AI Personal Trainer</h2>
      <p className="text-sm text-gray-500 max-w-xs">
        Ask questions about workouts, form, nutrition, or get a personalised plan.
        Start a new chat to begin.
      </p>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────

export default function Chat() {
  const { sessionId } = useParams<{ sessionId?: string }>()
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [listLoading, setListLoading] = useState(true)

  useEffect(() => {
    api.chat.listSessions()
      .then(setSessions)
      .catch(() => {})
      .finally(() => setListLoading(false))
  }, [])

  async function handleNew() {
    try {
      const session = await api.chat.createSession()
      setSessions((prev) => [session, ...prev])
      navigate(`/chat/${session.id}`)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.chat.deleteSession(id)
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (sessionId === id) navigate('/chat', { replace: true })
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden min-h-0">
      <PageHeader
        title="AI Trainer"
        subtitle="Your personal fitness coach"
        action={
          <button
            onClick={handleNew}
            className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 transition-colors"
          >
            + New chat
          </button>
        }
      />

      <div className="flex flex-1 overflow-hidden">

        {/* Desktop: always-visible sidebar */}
        <div className="hidden md:flex w-64 flex-shrink-0 border-r border-gray-200">
          <SessionList
            sessions={sessions}
            activeId={sessionId}
            onDelete={handleDelete}
            loading={listLoading}
          />
        </div>

        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

          {/* Mobile: dropdown selector at the top */}
          <div className="md:hidden flex items-center gap-2 px-3 py-2.5 border-b border-gray-100 bg-white">
            <select
              value={sessionId ?? ''}
              onChange={(e) => e.target.value ? navigate(`/chat/${e.target.value}`) : navigate('/chat')}
              className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-brand-400 truncate"
            >
              <option value="">Select a chat…</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>{s.title}</option>
              ))}
            </select>
          </div>

          {sessionId ? <ChatPane sessionId={sessionId} /> : <ChatEmpty />}
        </div>

      </div>
    </div>
  )
}
