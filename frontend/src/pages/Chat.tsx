import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type ChatSession } from '../lib/api'
import { useChatSession } from '../hooks/useChatSession'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'
import ChatBubble from '../components/chat/ChatBubble'
import ChatInput from '../components/chat/ChatInput'
import StreamingIndicator from '../components/chat/StreamingIndicator'
import { SkeletonText } from '../components/Skeleton'
import PageHeader from '../components/PageHeader'

// ── Session list sidebar ──────────────────────────────────────────────────

function SessionList({
  sessions,
  activeId,
  onDelete,
  loading,
}: {
  sessions: ChatSession[]
  activeId?: string
  onDelete: (id: string) => void
  loading: boolean
}) {
  const navigate = useNavigate()

  return (
    <div className="w-64 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5 pt-3">
        {loading ? (
          <div className="p-3 space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-9 bg-gray-100 rounded-lg animate-pulse" />
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-6 px-3">No chats yet. Start one above.</p>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                s.id === activeId ? 'bg-emerald-50 text-emerald-700' : 'hover:bg-gray-100 text-gray-700'
              }`}
              onClick={() => navigate(`/chat/${s.id}`)}
            >
              <span className="text-sm flex-1 truncate">{s.title}</span>
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(s.id) }}
                className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 text-xs transition-opacity"
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
  const { session, isLoading, isSending, streamingContent, error, loadSession, sendMessage } = useChatSession(sessionId)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    loadSession(sessionId)
  }, [sessionId, loadSession])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [session?.messages.length, streamingContent])

  if (isLoading) {
    return (
      <div className="flex-1 flex flex-col p-6">
        <SkeletonText lines={5} />
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {session?.messages.length === 0 && !streamingContent && (
          <div className="flex flex-col items-center justify-center h-full text-center py-16">
            <div className="text-4xl mb-3">💬</div>
            <p className="text-gray-500 text-sm">Ask me anything about fitness, workouts, or diet.</p>
          </div>
        )}

        {session?.messages.map((msg) => (
          <ChatBubble key={msg.id} role={msg.role} content={msg.content} />
        ))}

        {/* Streaming chunk */}
        {streamingContent && (
          <ChatBubble role="assistant" content={streamingContent} isStreaming />
        )}

        {/* Thinking indicator (sent but no chunks yet) */}
        {isSending && !streamingContent && <StreamingIndicator />}

        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-4">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-6 pb-6 pt-2">
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
    <div className="flex flex-col h-screen overflow-hidden">
      <PageHeader
        title="AI Trainer"
        subtitle="Your personal fitness coach"
        action={
          <button
            onClick={handleNew}
            className="bg-white text-rose-700 text-sm font-semibold px-4 py-2 rounded-xl hover:bg-rose-50 transition-colors"
          >
            + New chat
          </button>
        }
      />
      <div className="flex flex-1 overflow-hidden">
        <SessionList
          sessions={sessions}
          activeId={sessionId}
          onDelete={handleDelete}
          loading={listLoading}
        />
        <div className="flex-1 flex flex-col min-w-0">
          {sessionId ? <ChatPane sessionId={sessionId} /> : <ChatEmpty />}
        </div>
      </div>
    </div>
  )
}
