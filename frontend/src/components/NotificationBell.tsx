import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Feedback } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { statusLine } from '../lib/feedbackStatus'

/**
 * Superuser bell: what the automation did with the things you filed.
 *
 * Each row opens the thread. The panel is a summary, not the content -- it
 * says which issue and whether it is fixed, and the conversation lives on
 * its own page where there is room to reply.
 *
 * Renders nothing at all for ordinary users. That is presentation; the
 * server refuses them regardless, which is the actual boundary.
 */

/** Slow on purpose: this reports background work, not live events. */
const POLL_MS = 60_000

/** Past this much leftward drag, letting go clears the row. */
const SWIPE_THRESHOLD = 72
/** Under this much movement it was a tap, not a swipe. */
const TAP_SLOP = 8

function when(iso: string) {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

/** The agent's most recent turn, which is the answer to "is it fixed?". */
function latestReply(item: Feedback) {
  for (let i = item.messages.length - 1; i >= 0; i--) {
    if (item.messages[i].role === 'agent') return item.messages[i].body
  }
  return item.result_notes
}

interface RowProps {
  item: Feedback
  onOpen: (item: Feedback) => void
  onDismiss: (item: Feedback) => void
}

function Row({ item, onOpen, onDismiss }: RowProps) {
  const [dx, setDx] = useState(0)
  const startX = useRef(0)
  const dragging = useRef(false)
  const line = statusLine(item)
  const unread = item.unread > 0

  const onTouchStart = (e: React.TouchEvent) => {
    startX.current = e.touches[0].clientX
    dragging.current = true
  }
  const onTouchMove = (e: React.TouchEvent) => {
    if (!dragging.current) return
    // Leftward only. Dragging a row to the right has no meaning here, and
    // allowing it makes the list feel loose.
    setDx(Math.min(0, e.touches[0].clientX - startX.current))
  }
  const onTouchEnd = () => {
    dragging.current = false
    if (dx <= -SWIPE_THRESHOLD) {
      // Slide it the rest of the way out before the row disappears, so the
      // list does not just blink.
      setDx(-400)
      onDismiss(item)
      return
    }
    // A swipe that did not commit is still a swipe, not a tap -- opening the
    // thread on an abandoned drag is the thing that makes swipe lists
    // infuriating.
    if (Math.abs(dx) <= TAP_SLOP) onOpen(item)
    setDx(0)
  }

  return (
    <li className="relative overflow-hidden">
      {/* Revealed behind the row as it slides. */}
      <div
        aria-hidden="true"
        className="absolute inset-y-0 right-0 w-28 flex items-center justify-end pr-5 bg-red-500 text-white text-xs font-bold"
      >
        Clear
      </div>

      <div
        style={{
          transform: `translateX(${dx}px)`,
          transition: dragging.current ? 'none' : 'transform 160ms ease-out',
        }}
        className="relative bg-white"
      >
        <button
          type="button"
          onClick={() => onOpen(item)}
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
          // Unread is tinted, read is plain white. The tint is the only
          // thing distinguishing them, so it carries an explicit label too.
          className={`w-full text-left px-4 py-3 border-b border-gray-100 last:border-0 transition-colors ${
            unread ? 'bg-gray-50 hover:bg-gray-100' : 'bg-white hover:bg-gray-50'
          }`}
        >
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-[11px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${line.tone}`}>
              {line.label}
            </span>
            <span className="text-[11px] text-gray-400">{when(item.updated_at)}</span>
            {unread && (
              <span
                className="ml-auto min-w-[18px] h-[18px] px-1.5 rounded-full bg-accent-500 text-white text-[11px] font-bold flex items-center justify-center"
                aria-label={`${item.unread} new ${item.unread === 1 ? 'message' : 'messages'}`}
              >
                {item.unread}
              </span>
            )}
          </div>

          {/* The issue you raised, first and plainly. The old panel led with
              the agent's notes, so you had to work out what it was replying
              to before you could tell whether you cared. */}
          <p className={`text-sm leading-snug line-clamp-2 ${unread ? 'text-gray-900 font-semibold' : 'text-gray-700 font-medium'}`}>
            {item.body}
          </p>

          {latestReply(item) && (
            <p className="text-xs text-gray-500 mt-1 leading-relaxed line-clamp-2">
              {latestReply(item)}
            </p>
          )}

          <span className="inline-block mt-1.5 text-[11px] font-semibold text-accent-600">
            Open conversation →
          </span>
        </button>
      </div>
    </li>
  )
}

export default function NotificationBell() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const isSuperuser = !!user?.is_superuser
  const [items, setItems] = useState<Feedback[]>([])
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    if (!isSuperuser) return
    try {
      setItems((await api.feedback.notifications()).items)
    } catch {
      // A failed poll is not worth a visible error: the bell is ambient.
      // Showing a banner because a background poll missed would be louder
      // than the thing it is reporting.
    }
  }, [isSuperuser])

  useEffect(() => {
    if (!isSuperuser) return
    refresh()
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [isSuperuser, refresh])

  // Re-poll when the panel is opened, so a badge that went stale between
  // the minute-long polls does not show a thread that is already answered.
  useEffect(() => {
    if (open) refresh()
  }, [open, refresh])

  const openThread = useCallback(
    (item: Feedback) => {
      setOpen(false)
      // Reading is what marks it read -- the thread endpoint does that. The
      // count drops locally at once so the badge does not lag a whole poll
      // behind the navigation.
      if (item.unread > 0) {
        setItems((prev) =>
          prev.map((i) => (i.id === item.id ? { ...i, unread: 0 } : i)),
        )
      }
      navigate(`/feedback/${item.id}`)
    },
    [navigate],
  )

  const markAllRead = useCallback(async () => {
    const unreadIds = items.filter((i) => i.unread > 0).map((i) => i.id)
    if (!unreadIds.length) return
    setItems((prev) => prev.map((i) => ({ ...i, unread: 0 })))
    try {
      await api.feedback.markSeen(unreadIds)
    } catch {
      refresh()
    }
  }, [items, refresh])

  const dismiss = useCallback(
    async (item: Feedback) => {
      setItems((prev) => prev.filter((i) => i.id !== item.id))
      try {
        await api.feedback.dismiss([item.id])
      } catch {
        refresh()
      }
    },
    [refresh],
  )

  const clearAll = useCallback(async () => {
    const ids = items.map((i) => i.id)
    if (!ids.length) return
    setItems([])
    try {
      await api.feedback.dismiss(ids)
    } catch {
      refresh()
    }
  }, [items, refresh])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!panelRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  if (!isSuperuser) return null

  const count = items.reduce((n, i) => n + i.unread, 0)

  return (
    <div className="relative" ref={panelRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={count ? `Notifications, ${count} unread` : 'Notifications'}
        className="relative min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl text-white/85 hover:text-white hover:bg-white/15 transition-colors"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8.5a6 6 0 1 0-12 0c0 5-2 6.5-2 6.5h16s-2-1.5-2-6.5Z" />
          <path d="M10.5 19a1.8 1.8 0 0 0 3 0" />
        </svg>
        {count > 0 && (
          <span className="absolute top-1.5 right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-accent-500 text-white text-[11px] font-bold flex items-center justify-center">
            {count > 9 ? '9+' : count}
          </span>
        )}
      </button>

      {open && (
        /* Anchored to the right edge of the HEADER, not of this button.
           `right-0` on a bell that is not the rightmost element put the
           panel's right edge under the button beside it, so on every page
           with a header action it opened half off its anchor. The bell is
           now last in the header, which is what makes this correct -- see
           PageHeader. */
        <div className="absolute right-0 mt-2 w-[min(22rem,calc(100vw-2rem))] max-h-[70vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-gray-100 z-50">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 sticky top-0 bg-white">
            <p className="text-sm font-bold text-gray-900">Feedback updates</p>
            <div className="flex items-center gap-3">
              {count > 0 && (
                <button onClick={markAllRead} className="text-xs font-semibold text-accent-600 hover:underline">
                  Mark all read
                </button>
              )}
              {items.length > 0 && (
                <button onClick={clearAll} className="text-xs font-semibold text-gray-400 hover:text-gray-600 hover:underline">
                  Clear all
                </button>
              )}
            </div>
          </div>

          {items.length === 0 ? (
            <p className="px-4 py-6 text-sm text-gray-400 text-center">
              Nothing here. You'll see items as the automation picks them up.
            </p>
          ) : (
            <>
              <ul>
                {items.map((item) => (
                  <Row key={item.id} item={item} onOpen={openThread} onDismiss={dismiss} />
                ))}
              </ul>
              <p className="px-4 py-2 text-[11px] text-gray-400 text-center border-t border-gray-100">
                Swipe a row left to clear it.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
