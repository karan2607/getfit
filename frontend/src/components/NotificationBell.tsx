import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Feedback } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { statusLine } from '../lib/feedbackStatus'
import FeedbackConversation from './feedback/FeedbackConversation'

/**
 * Superuser bell: what the automation did with the things you filed.
 *
 * Two panes on ONE surface. Opening a thread slides the list left and the
 * conversation in from the right; back slides it home. It used to navigate
 * to /feedback/:id, which tore down the page you were on, remounted it on
 * the way back, and refetched everything that page needed -- all to read
 * three sentences and type one. The panel already had the thread in hand,
 * so none of that bought anything.
 *
 * It also put the composer at the bottom of a full page, underneath the
 * fixed bottom nav, where it could not be typed into. In the panel the
 * composer is the panel's own bottom edge, well clear of the nav.
 *
 * Renders nothing at all for ordinary users. That is presentation; the
 * server refuses them regardless, which is the actual boundary.
 */

/** Slow on purpose: this reports background work, not live events. */
const POLL_MS = 60_000

/** Past this much leftward drag, letting go clears the row. */
const SWIPE_THRESHOLD = 72
/**
 * A gesture steeper than this is a scroll, and the row must not move at all.
 *
 * Without it, dragging down the list dragged the rows sideways by whatever
 * horizontal wobble a thumb has.
 */
const SCROLL_SLOP = 10

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
  const start = useRef({ x: 0, y: 0 })
  /** null until the first move decides: horizontal swipe, or vertical scroll. */
  const axis = useRef<'x' | 'y' | null>(null)
  const dragging = useRef(false)
  const line = statusLine(item)
  const unread = item.unread > 0

  const onTouchStart = (e: React.TouchEvent) => {
    start.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    axis.current = null
    dragging.current = true
  }

  const onTouchMove = (e: React.TouchEvent) => {
    if (!dragging.current) return
    const moveX = e.touches[0].clientX - start.current.x
    const moveY = e.touches[0].clientY - start.current.y

    // Decide once, on the first meaningful movement, and stick to it. A
    // gesture that starts as a scroll stays a scroll even if the thumb
    // drifts sideways halfway down the list.
    if (axis.current === null) {
      if (Math.abs(moveX) < SCROLL_SLOP && Math.abs(moveY) < SCROLL_SLOP) return
      axis.current = Math.abs(moveX) > Math.abs(moveY) ? 'x' : 'y'
    }
    if (axis.current === 'y') return

    // Leftward only. Dragging a row to the right has no meaning here, and
    // allowing it makes the list feel loose.
    setDx(Math.min(0, moveX))
  }

  const onTouchEnd = () => {
    dragging.current = false
    if (axis.current === 'x' && dx <= -SWIPE_THRESHOLD) {
      // Slide it the rest of the way out before the row disappears, so the
      // list does not just blink.
      setDx(-400)
      onDismiss(item)
      return
    }
    setDx(0)
    // Nothing else happens here, and that is the point. This used to treat
    // "barely moved horizontally" as a tap -- which a vertical scroll is,
    // so lifting your finger after scrolling the list opened whatever row
    // it ended under. Opening is the explicit button below, only.
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
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          transform: `translateX(${dx}px)`,
          transition: dragging.current ? 'none' : 'transform 160ms ease-out',
        }}
        // Unread is tinted, read is plain white. The tint is the only thing
        // distinguishing them, so the row carries an explicit count chip too.
        className={`relative px-4 py-3 border-b border-gray-100 last:border-0 ${
          unread ? 'bg-gray-50' : 'bg-white'
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

        {/* The only way in. The whole row used to be the button, which is
            what made scrolling the list open things. */}
        <button
          type="button"
          onClick={() => onOpen(item)}
          className="mt-1 -ml-1 px-1 min-h-[36px] inline-flex items-center gap-1 text-[12px] font-bold text-accent-600 rounded-lg hover:bg-accent-50 active:bg-accent-100 transition-colors"
        >
          Open conversation
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12h13M12 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </li>
  )
}

export default function NotificationBell() {
  const { user } = useAuth()
  const isSuperuser = !!user?.is_superuser
  const [items, setItems] = useState<Feedback[]>([])
  const [open, setOpen] = useState(false)
  /** The thread being read, or null for the list. Drives the slide. */
  const [active, setActive] = useState<Feedback | null>(null)
  const [sending, setSending] = useState(false)
  const [threadError, setThreadError] = useState('')
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

  const openThread = useCallback(async (item: Feedback) => {
    // Shown from what the list already holds, so the pane slides in with
    // content rather than a spinner. The fetch behind it only adds turns
    // that arrived since the last poll -- and marks the thread read, which
    // is what reading it means.
    setActive(item)
    setThreadError('')
    if (item.unread > 0) {
      setItems((prev) =>
        prev.map((i) => (i.id === item.id ? { ...i, unread: 0 } : i)),
      )
    }
    try {
      const fresh = await api.feedback.thread(item.id)
      setActive((cur) => (cur?.id === fresh.id ? fresh : cur))
      setItems((prev) => prev.map((i) => (i.id === fresh.id ? fresh : i)))
    } catch {
      // The pane is already readable from the list copy, so a failed
      // refresh is a note, not a blank screen.
      setThreadError('Could not load the latest messages.')
    }
  }, [])

  const reply = useCallback(
    async (body: string) => {
      if (!active) return
      setSending(true)
      try {
        const fresh = await api.feedback.reply(active.id, body)
        setActive(fresh)
        setItems((prev) => prev.map((i) => (i.id === fresh.id ? fresh : i)))
        setThreadError('')
      } catch {
        setThreadError('Could not send that. Try again.')
      } finally {
        setSending(false)
      }
    },
    [active],
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
      if (panelRef.current?.contains(e.target as Node)) return
      setOpen(false)
      // Next open starts at the list. Reopening into whatever thread you
      // last read, with no memory of having left it, is disorienting.
      setActive(null)
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
           PageHeader.

           Thread view takes the SAME 70vh the list caps at, not a smaller
           fixed height. At `min(70vh,30rem)` a full list stood at 70vh and
           then shrank on open, which is the wrong direction -- the pane you
           just opened is the one that needs the room. A short list still
           grows into it, and growing to read a conversation is fine. */
        <div
          className={`absolute right-0 mt-2 w-[min(22rem,calc(100vw-2rem))] bg-white rounded-2xl shadow-2xl border border-gray-100 z-50 overflow-hidden flex flex-col ${
            active ? 'h-[70vh]' : 'max-h-[70vh]'
          }`}
        >
          {/* One track, two panes, each exactly the panel's width.
              Everything here is measured against the TRACK, which is the
              panel's width -- so `translateX(-100%)` is exactly one pane.

              The track used to be `width: 200%` with `w-1/2` panes and a
              `-50%` shift. Those three percentages resolve against
              different boxes: in a 352px panel the track came out 700px and
              each pane 350px, so the shift overshot and left pane 2 almost
              entirely outside the panel -- a sliver of the list on the left
              and blank space for the rest. Reopening the panel re-laid it
              out and happened to look right, which is why it seemed
              intermittent.

              The list keeps its scroll position while the thread is open
              because it is never unmounted. */}
          <div
            className="flex-1 min-h-0 w-full flex transition-transform duration-300 ease-out"
            style={{ transform: active ? 'translateX(-100%)' : 'translateX(0)' }}
          >
            {/* ---- pane 1: the list ---- */}
            <div
              className="w-full flex-none flex flex-col min-h-0"
              aria-hidden={active ? 'true' : undefined}
            >
              <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-b border-gray-100">
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

              {/* The cap is on the scroller ITSELF, not inherited. It used to
                  rely on flex-1/min-h-0 passing the panel's max-height down
                  through the track and the pane -- but a max-height is not a
                  definite height, and WebKit (every iPhone browser) does not
                  carry it through a stretched pane. So the list grew to its
                  full length, the panel's overflow-hidden clipped it, nothing
                  scrolled, and a drag that went nowhere landed as a press on
                  the row. 3rem covers the header row above. */}
              <div className="flex-1 min-h-0 max-h-[calc(70vh-3rem)] overflow-y-auto overscroll-contain">
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
            </div>

            {/* ---- pane 2: the conversation ---- */}
            <div
              className="w-full flex-none flex flex-col min-h-0"
              aria-hidden={active ? undefined : 'true'}
            >
              {active && (
                <>
                  <div className="flex-shrink-0 flex items-center gap-1 px-2 py-2 border-b border-gray-100">
                    <button
                      type="button"
                      onClick={() => setActive(null)}
                      aria-label="Back to notifications"
                      className="min-w-[40px] min-h-[40px] flex items-center justify-center rounded-xl text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                    >
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                           strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M15 18l-6-6 6-6" />
                      </svg>
                    </button>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-bold text-gray-900 truncate">
                        {statusLine(active).label}
                      </p>
                      {active.page && (
                        <p className="text-[11px] text-gray-400 truncate">{active.page}</p>
                      )}
                    </div>
                  </div>

                  {threadError && (
                    <p className="flex-shrink-0 px-3 py-1.5 text-[11px] text-red-600 bg-red-50">
                      {threadError}
                    </p>
                  )}

                  <FeedbackConversation item={active} sending={sending} onReply={reply} />
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
