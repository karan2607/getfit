import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type ChatSession } from '../lib/api'
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
            /* Two real buttons, not a clickable div with a button in it.
               The row had an onClick and no role, so a keyboard could not
               reach it, and the ✕ appeared only on hover -- unreachable by
               touch and unannounced to a screen reader. */
            <div
              key={s.id}
              className={`group flex items-center gap-1 rounded-lg transition-colors ${
                s.id === activeId ? 'bg-accent-100 text-accent-700' : 'hover:bg-gray-200 text-gray-700'
              }`}
            >
              <button
                type="button"
                onClick={() => { navigate(`/chat/${s.id}`); onSelect?.() }}
                aria-current={s.id === activeId}
                className="flex-1 min-w-0 text-left text-xs px-2 py-2 truncate"
              >
                {s.title}
              </button>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setConfirmId(s.id) }}
                className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-gray-400 hover:text-danger-500 text-[10px] px-2 py-2 flex-shrink-0 transition-opacity"
                aria-label={`Delete chat: ${s.title}`}
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
    failedTurn, elapsed, proposedChange, status, applying, appliedDiff, notice,
    loadSession, sendMessage, retry, stop, dismissFailure, clearProposal,
    confirmProposal, adoptProposal,
  } = useChatSession(sessionId)
  const { showToast } = useToast()
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinnedToBottom, setPinnedToBottom] = useState(true)

  useEffect(() => {
    loadSession(sessionId)
  }, [sessionId, loadSession])

  /**
   * Pick up a draft the server is still holding.
   *
   * Without this the Confirm button existed only for the render that
   * proposed the change: reload, switch chats, or reopen the app and the
   * draft was unreachable from the page that made it -- which is the defect
   * users reported as "no update plan button", twice. The drawer already
   * does this; the chat page could not, because it has no plan id, so the
   * lookup is plan-agnostic. A draft the plan has moved past is withheld
   * server-side, so anything this adopts can actually be applied.
   */
  useEffect(() => {
    let cancelled = false
    api.workouts.pendingChange()
      .then((res) => { if (!cancelled) adoptProposal(res.proposed_change) })
      .catch(() => { /* a missing draft is the normal case, not an error */ })
    return () => { cancelled = true }
  }, [sessionId, adoptProposal])

  // Autoscroll ONLY when the user is already at the bottom. The old effect
  // scrolled on every change, so reading back through a reply yanked you to
  // the end on each chunk -- the longer the answer, the harder it was to read.
  useEffect(() => {
    const el = scrollRef.current
    if (el && pinnedToBottom) el.scrollTop = el.scrollHeight
  }, [session?.messages.length, streamingContent, pinnedToBottom])

  /** Confirm the draft. The apply itself lives in the hook, shared with the
   *  workout drawer so one surface cannot quietly lose the feature. */
  async function handleConfirmChange() {
    try {
      const res = await confirmProposal()
      if (res && !res.clean) {
        showToast('Applied, but not everything matched the draft', 'error')
      }
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
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

        {isSending && <InFlight elapsed={elapsed} onStop={stop} status={status} />}

        {proposedChange && (
          <PlanChangeDraft
            change={proposedChange}
            busy={applying}
            onConfirm={handleConfirmChange}
            onCancel={clearProposal}
          />
        )}

        {notice && (
          <p className="mb-4 text-xs text-ink-500 bg-accent-50 border border-accent-200 rounded-xl px-3 py-2">
            {notice}
          </p>
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
          <div className="text-sm text-danger-600 bg-danger-50 border border-danger-200 rounded-xl px-4 py-3 mb-4">
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
          {/* It said "New messages" whenever you had scrolled up, with or
              without anything new -- so re-reading an old answer in a quiet
              conversation claimed messages had arrived. It only knows you
              are not at the bottom, so that is all it says. */}
          {isSending ? 'New messages ↓' : 'Jump to latest ↓'}
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
  // Deleting a chat is irreversible, and the desktop path already confirms.
  // A mobile button that skipped the guard would be a worse version of the
  // same action.
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  useEffect(() => {
    api.chat.listSessions()
      .then(setSessions)
      .catch(() => {})
      .finally(() => setListLoading(false))
  }, [])

  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)

  async function handleNew() {
    // A second tap before the first request returns creates a second empty
    // conversation, and the list then shows two "New chat" rows the user
    // has to tell apart and delete. A ref rather than state: two taps in
    // one frame both read the same stale `false`.
    if (creatingRef.current) return
    creatingRef.current = true
    setCreating(true)
    try {
      const session = await api.chat.createSession()
      setSessions((prev) => [session, ...prev])
      navigate(`/chat/${session.id}`)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      creatingRef.current = false
      setCreating(false)
    }
  }

  async function handleDelete(id: string) {
    // Callers confirm first; this does the deletion.
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
      {pendingDelete && (
        <ConfirmModal
          title="Delete this chat?"
          message="The conversation and its history will be removed. This cannot be undone."
          confirmLabel="Delete"
          onConfirm={() => {
            const id = pendingDelete
            setPendingDelete(null)
            handleDelete(id)
          }}
          onClose={() => setPendingDelete(null)}
        />
      )}

      <PageHeader
        title="AI Trainer"
        subtitle="Your personal fitness coach"
        action={
          <button
            onClick={handleNew}
            disabled={creating}
            className="bg-white text-brand-500 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-brand-50 disabled:opacity-60 transition-colors"
          >
            {creating ? 'Starting...' : '+ New chat'}
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

            {/* Delete on MOBILE. The desktop list's delete is
                `opacity-0 group-hover:opacity-100`, which no touch device can
                ever reveal -- so on a phone there was no way to remove a chat
                at all, only to accumulate them. */}
            {sessionId && (
              <button
                type="button"
                onClick={() => setPendingDelete(sessionId)}
                aria-label="Delete this chat"
                className="flex-shrink-0 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl text-gray-400 hover:text-danger-500 hover:bg-danger-50 transition-colors"
              >
                🗑
              </button>
            )}
          </div>

          {/* `key` matters: without it, switching chats reuses the same hook
              instance and the in-flight turn keeps writing into it. */}
          {sessionId ? <ChatPane key={sessionId} sessionId={sessionId} /> : <ChatEmpty />}
        </div>

      </div>
    </div>
  )
}
