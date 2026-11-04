import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Feedback } from '../lib/api'
import { useAuth } from '../hooks/useAuth'

/**
 * Superuser bell: feedback the automation has finished with.
 *
 * The queue already recorded `status`, `result_notes` and the commit it
 * shipped, and nothing ever said so -- you had to go and look to find out
 * whether something you asked for had landed. An automation that implements
 * and pushes on its own needs to report back, or you are trusting it blind.
 *
 * Renders nothing at all for ordinary users. That is presentation; the
 * server refuses them regardless, which is the actual boundary.
 */

/** Slow on purpose: this reports background work, not live events. */
const POLL_MS = 60_000

// Success is accent, not green. The rebrand moved done-states onto the
// orange token and the palette guard enforces it, so reaching for emerald
// here would have reintroduced exactly what that guard exists to stop.
const TONE: Record<string, string> = {
  done: 'text-accent-700 bg-accent-50 border-accent-200',
  failed: 'text-red-700 bg-red-50 border-red-200',
  skipped: 'text-gray-600 bg-gray-50 border-gray-200',
}

function when(iso: string) {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

export default function NotificationBell() {
  const { user } = useAuth()
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

  // Dismissing is what marks them seen, so opening the panel is not enough:
  // glancing at a badge and navigating away should not silently clear it.
  const dismiss = useCallback(async () => {
    setOpen(false)
    const ids = items.map((i) => i.id)
    if (!ids.length) return
    setItems([])
    try {
      await api.feedback.markSeen(ids)
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

  const count = items.length

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
        <div className="absolute right-0 mt-2 w-[min(22rem,calc(100vw-2rem))] max-h-[70vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-gray-100 z-50">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <p className="text-sm font-bold text-gray-900">Feedback updates</p>
            {count > 0 && (
              <button onClick={dismiss} className="text-xs font-semibold text-accent-600 hover:underline">
                Mark all read
              </button>
            )}
          </div>

          {count === 0 ? (
            <p className="px-4 py-6 text-sm text-gray-400 text-center">
              Nothing new. You'll see items here when the automation finishes one.
            </p>
          ) : (
            <ul>
              {items.map((item) => (
                <li key={item.id} className="px-4 py-3 border-b border-gray-50 last:border-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[11px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${TONE[item.status] ?? TONE.skipped}`}>
                      {item.status}
                    </span>
                    <span className="text-[11px] text-gray-400">{when(item.updated_at)}</span>
                  </div>
                  <p className="text-sm text-gray-900 font-medium leading-snug">{item.body}</p>
                  {item.result_notes && (
                    <p className="text-xs text-gray-500 mt-1 leading-relaxed">{item.result_notes}</p>
                  )}
                  {item.commit_sha && (
                    <p className="text-[11px] text-gray-400 mt-1 font-mono">
                      {item.commit_sha.slice(0, 10)}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
