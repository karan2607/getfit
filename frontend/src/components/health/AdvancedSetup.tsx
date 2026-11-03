import { useState, type ReactNode } from 'react'

/**
 * The API mechanics, behind a disclosure.
 *
 * The Health page led with a raw sync URL, a "Reveal my token" button and an
 * expected-JSON block, on a screen in the main navigation. That is a
 * developer reference presented as a consumer page: most people have nothing
 * to do with any of it, and the one item that genuinely matters -- the token
 * -- is a credential sitting a single tap from a shoulder-surfer.
 *
 * None of it is removed. It moves behind a deliberate tap, and the token
 * carries a warning before it is shown.
 */
export default function AdvancedSetup({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-4 min-h-[44px] text-left"
      >
        <span className="text-sm font-medium text-ink-600">Advanced / manual setup</span>
        <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 pt-3 space-y-3">
          <p className="text-xs text-gray-500">
            Endpoint, token and payload details. You only need these if you are
            building the Shortcut by hand.
          </p>
          {children}
        </div>
      )}
    </div>
  )
}

/**
 * A credential behind an explicit tap, with the consequence stated first.
 *
 * It was previously one unlabelled button away. A token that syncs health
 * data deserves the same care as a password: the warning is shown BEFORE the
 * reveal, not after.
 */
export function TokenReveal({
  token,
  loading,
  onReveal,
  children,
}: {
  token: string | null
  loading: boolean
  onReveal: () => void
  children?: ReactNode
}) {
  const [acknowledged, setAcknowledged] = useState(false)

  if (token) return <>{children}</>

  if (!acknowledged) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3">
        <p className="text-xs text-amber-800">
          This token can write health data to your account. Do not share it or
          show it on a screen other people can see.
        </p>
        <button
          type="button"
          onClick={() => setAcknowledged(true)}
          className="mt-2 min-h-[44px] px-4 rounded-xl border border-amber-300 bg-white text-sm font-medium text-amber-900"
        >
          I understand — show it
        </button>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={onReveal}
      disabled={loading}
      className="w-full min-h-[44px] bg-accent-500 hover:bg-accent-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition-colors"
    >
      {loading ? 'Loading…' : 'Reveal my token'}
    </button>
  )
}
