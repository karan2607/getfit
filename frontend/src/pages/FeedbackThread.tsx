import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type Feedback, type FeedbackMessage } from '../lib/api'
import { statusLine } from '../lib/feedbackStatus'
import { useAuth } from '../hooks/useAuth'
import ChatInput from '../components/chat/ChatInput'
import { useToast } from '../components/Toast'

/**
 * One feedback item, as a conversation with the automation.
 *
 * Filing something used to be a one-shot: it implemented whatever it read,
 * and the only way to say "not quite" was to file a second item that knew
 * nothing about the first. Replying here reopens this same item, so the
 * agent comes back to it with the whole thread -- it is the one queue,
 * talked to twice, not a second pipeline.
 */

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function Bubble({ message }: { message: FeedbackMessage }) {
  const mine = message.role === 'user'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div className="max-w-[85%]">
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words ${
            mine
              ? 'bg-brand-500 text-white rounded-br-md'
              : 'bg-white text-gray-800 border border-gray-100 rounded-bl-md'
          }`}
        >
          {message.body}
        </div>
        <p className={`text-[11px] text-gray-400 mt-1 ${mine ? 'text-right' : ''}`}>
          {mine ? 'You' : 'Agent'} · {when(message.created_at)}
        </p>
      </div>
    </div>
  )
}

export default function FeedbackThread() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { showToast } = useToast()
  const [item, setItem] = useState<Feedback | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const isSuperuser = !!user?.is_superuser

  const load = useCallback(async () => {
    // Guarded here, not only at render: the effect fires regardless of what
    // the component returns, so without this an ordinary user's visit still
    // sent a request the server would refuse.
    if (!id || !isSuperuser) return
    try {
      setItem(await api.feedback.thread(id))
      setError('')
    } catch {
      setError('Could not load this conversation.')
    } finally {
      setLoading(false)
    }
  }, [id, isSuperuser])

  useEffect(() => { load() }, [load])

  // The agent answers in its own time, so the thread polls while it is in
  // flight and stops once it has finished -- a settled thread polling
  // forever would be a request a minute for nothing.
  useEffect(() => {
    if (!item) return
    if (item.status !== 'new' && item.status !== 'in_progress') return
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [item, load])

  useEffect(() => {
    // Not present in jsdom, and absent on some older WebViews -- failing to
    // scroll is cosmetic, so it must not take the page down with it.
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [item?.messages.length])

  const send = useCallback(
    async (body: string) => {
      if (!id || sending) return
      setSending(true)
      try {
        setItem(await api.feedback.reply(id, body))
        showToast('Sent — it will pick this up on its next pass.')
      } catch {
        showToast('Could not send that. Try again.', 'error')
      } finally {
        setSending(false)
      }
    },
    [id, sending, showToast],
  )

  // Ordinary users have no business here at all, and the server refuses them
  // regardless -- this just avoids showing them a broken page.
  if (!isSuperuser) {
    return (
      <div className="p-6 text-sm text-gray-500">Not available.</div>
    )
  }

  const line = item ? statusLine(item) : null
  const working = item?.status === 'new' || item?.status === 'in_progress'

  return (
    <div className="fixed inset-0 md:relative md:inset-auto flex flex-col h-full bg-gray-50">
      <div
        className="flex-shrink-0 bg-brand-500 px-4 md:px-6 py-4 flex items-start gap-3"
        style={{ paddingTop: 'max(1rem, env(safe-area-inset-top))' }}
      >
        <button
          type="button"
          onClick={() => navigate(-1)}
          aria-label="Back"
          className="min-w-[44px] min-h-[44px] -ml-2 flex items-center justify-center rounded-xl text-white/85 hover:text-white hover:bg-white/15 transition-colors"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-extrabold tracking-tight text-white leading-tight truncate">
            Feedback
          </h1>
          {line && (
            <p className="text-sm text-white/75 mt-0.5">
              {line.label}
              {item?.page ? ` · ${item.page}` : ''}
            </p>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 md:px-6 py-4 space-y-4">
        {loading && <p className="text-sm text-gray-400">Loading…</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}

        {item && (
          <>
            {/* The original request is the first turn, not a header. The
                whole page reads as one conversation that way. */}
            <Bubble
              message={{
                id: 'head', role: 'user', body: item.body,
                seen_at: null, created_at: item.created_at,
              }}
            />
            {item.messages.map((m) => <Bubble key={m.id} message={m} />)}

            {/* An item that finished with no summary still has to say
                something, or the thread ends on your own question. */}
            {!working && !item.messages.some((m) => m.role === 'agent') && line && (
              <Bubble
                message={{
                  id: 'fallback', role: 'agent', body: line.fallback,
                  seen_at: null, created_at: item.updated_at,
                }}
              />
            )}

            {working && (
              <div className="flex justify-start">
                <div className="bg-white border border-gray-100 rounded-2xl rounded-bl-md px-4 py-2.5 text-sm text-gray-500">
                  {item.status === 'new'
                    ? 'Queued — it polls every few minutes.'
                    : 'Working on it now…'}
                </div>
              </div>
            )}

            {item.commit_sha && (
              <p className="text-[11px] text-gray-400 font-mono text-center pt-2">
                shipped in {item.commit_sha.slice(0, 10)}
              </p>
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex-shrink-0 border-t border-gray-100 bg-white">
        <ChatInput onSend={send} disabled={!item || sending} />
        <p className="px-4 pb-2 text-[11px] text-gray-400">
          Replies go to the agent and reopen this item.
        </p>
      </div>
    </div>
  )
}
