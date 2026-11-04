import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type Feedback } from '../lib/api'
import { statusLine } from '../lib/feedbackStatus'
import { useAuth } from '../hooks/useAuth'
import FeedbackConversation from '../components/feedback/FeedbackConversation'

/**
 * One feedback thread on its own page, for a direct link.
 *
 * The bell reads threads in place -- sliding panes on one surface -- so this
 * is not the normal way in and is deliberately thin. It shares
 * FeedbackConversation with the panel so the two cannot drift.
 */
export default function FeedbackThread() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuth()
  const isSuperuser = !!user?.is_superuser
  const [item, setItem] = useState<Feedback | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

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

  // The agent answers in its own time, so poll while it is in flight and
  // stop once it has finished -- a settled thread polling forever would be
  // a request a minute for nothing.
  useEffect(() => {
    if (!item) return
    if (item.status !== 'new' && item.status !== 'in_progress') return
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [item, load])

  const reply = useCallback(
    async (body: string) => {
      if (!id) return
      setSending(true)
      try {
        setItem(await api.feedback.reply(id, body))
        setError('')
      } catch {
        setError('Could not send that. Try again.')
      } finally {
        setSending(false)
      }
    },
    [id],
  )

  // Ordinary users have no business here, and the server refuses them
  // regardless -- this just avoids showing them a broken page.
  if (!isSuperuser) {
    return <div className="p-6 text-sm text-gray-500">Not available.</div>
  }

  const line = item ? statusLine(item) : null

  return (
    /* z-[60] clears the bottom nav, which is `fixed ... z-50`. Without it
       the nav painted over the composer and the reply box could not be
       typed into at all -- the same mistake the workout drawers already
       fixed by sitting above it. */
    <div className="fixed inset-0 z-[60] md:relative md:inset-auto md:z-auto flex flex-col h-full bg-white">
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

      {loading && <p className="p-4 text-sm text-gray-400">Loading…</p>}
      {error && <p className="px-4 py-2 text-sm text-red-600 bg-red-50">{error}</p>}

      {item && <FeedbackConversation item={item} sending={sending} onReply={reply} />}
    </div>
  )
}
