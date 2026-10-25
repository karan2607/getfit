import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, type Feedback } from '../lib/api'
import { getErrorMessage } from '../lib/errors'
import { useToast } from './Toast'
import { useAuth } from '../hooks/useAuth'

/**
 * Floating feedback button, rendered only for superusers.
 *
 * Items queued here are picked up by an automated loop that implements and
 * ships the change. Submission is restricted to superusers *server-side* —
 * hiding this button is only a convenience, and the endpoint returns 403 for
 * anyone else. That restriction is what makes the automation safe: the queue
 * can only ever contain content written by a trusted operator.
 */
/**
 * Bottom-LEFT on purpose.
 *
 * This is global (mounted in App), while the AI chat FABs are per-page and
 * bottom-right. On the right it collided with them -- reported as "Feedback
 * button is over ai chat button on workouts page". Picking the opposite corner
 * removes the whole class of collision rather than tuning offsets every time a
 * page adds a FAB.
 */
export default function FeedbackButton() {
  const { user } = useAuth()
  const { showToast } = useToast()
  const location = useLocation()

  const [open, setOpen] = useState(false)
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [queue, setQueue] = useState<Feedback[]>([])
  const [loadingQueue, setLoadingQueue] = useState(false)
  const [tab, setTab] = useState<'new' | 'queue'>('new')

  // Don't render anything at all for ordinary users.
  if (!user?.is_superuser) return null

  function loadQueue() {
    setLoadingQueue(true)
    api.feedback.list()
      .then(setQueue)
      .catch(() => {})
      .finally(() => setLoadingQueue(false))
  }

  async function handleSubmit() {
    const text = body.trim()
    if (!text) return
    setSending(true)
    try {
      await api.feedback.submit({ body: text, page: location.pathname })
      setBody('')
      showToast('Feedback queued — it\'ll be picked up automatically')
      setOpen(false)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSending(false)
    }
  }

  const statusStyle: Record<string, string> = {
    new: 'bg-gray-100 text-gray-600',
    in_progress: 'bg-amber-100 text-amber-700',
    done: 'bg-emerald-100 text-emerald-700',
    failed: 'bg-red-100 text-red-700',
    skipped: 'bg-gray-100 text-gray-400',
  }

  return (
    <>
      {/* sits above the mobile bottom nav */}
      <button
        onClick={() => setOpen(true)}
        aria-label="Send feedback"
        title="Send feedback"
        className="fixed left-4 bottom-20 sm:bottom-6 z-30 w-12 h-12 rounded-full bg-gray-900 hover:bg-gray-800 text-white shadow-lg flex items-center justify-center transition-colors active:scale-95"
      >
        <span className="text-lg leading-none">✱</span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4 pb-20 sm:pb-4 bg-black/50"
          onClick={() => setOpen(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-lg flex flex-col max-h-[80vh] overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
              <div className="flex gap-1">
                <button
                  onClick={() => setTab('new')}
                  className={`text-sm font-semibold px-3 py-1 rounded-lg transition-colors ${tab === 'new' ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'}`}
                >
                  New
                </button>
                <button
                  onClick={() => { setTab('queue'); loadQueue() }}
                  className={`text-sm font-semibold px-3 py-1 rounded-lg transition-colors ${tab === 'queue' ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'}`}
                >
                  Queue
                </button>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                ✕
              </button>
            </div>

            {tab === 'new' ? (
              <div className="p-5">
                <p className="text-xs text-gray-400 mb-2">
                  From <span className="font-mono">{location.pathname}</span> · queued for
                  automated implementation
                </p>
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit()
                  }}
                  autoFocus
                  rows={6}
                  maxLength={5000}
                  placeholder="What should change? Be specific — this gets implemented as written."
                  className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-brand-400"
                />
                <div className="flex items-center justify-between mt-3">
                  <span className="text-xs text-gray-400">{body.length}/5000 · ⌘↵ to send</span>
                  <button
                    onClick={handleSubmit}
                    disabled={sending || !body.trim()}
                    className="bg-gray-900 hover:bg-gray-800 disabled:opacity-40 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors"
                  >
                    {sending ? 'Queueing…' : 'Queue it'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="overflow-y-auto flex-1 p-5">
                {loadingQueue ? (
                  <p className="text-sm text-gray-400 text-center py-8">Loading…</p>
                ) : queue.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">
                    Nothing queued.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {queue.map((item) => (
                      <div key={item.id} className="border border-gray-100 rounded-xl px-3 py-2.5">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm text-gray-800 flex-1">{item.body}</p>
                          <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full flex-shrink-0 ${statusStyle[item.status] ?? statusStyle.new}`}>
                            {item.status.replace('_', ' ')}
                          </span>
                        </div>
                        {item.result_notes && (
                          <p className="text-xs text-gray-500 mt-1.5 border-l-2 border-gray-200 pl-2">
                            {item.result_notes}
                          </p>
                        )}
                        <div className="flex items-center gap-2 mt-1.5">
                          <span className="text-[11px] text-gray-400">
                            {new Date(item.created_at).toLocaleDateString('en-US', {
                              month: 'short', day: 'numeric',
                            })}
                            {item.page ? ` · ${item.page}` : ''}
                          </span>
                          {item.commit_sha && (
                            <span className="text-[11px] font-mono text-gray-400">
                              {item.commit_sha.slice(0, 7)}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
