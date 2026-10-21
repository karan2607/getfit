import { useState, useEffect } from 'react'
import { api, type ApiKey } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'

/**
 * Integration keys — long-lived credentials for things that are not a browser.
 *
 * Why this exists: the app token is a DRF `Token`, which is one row per user
 * and is DELETED by logout. The web app auto-logs-out after 30 minutes of
 * inactivity, so leaving a tab open silently revoked the credential the user
 * had pasted into their phone agent. It read as "my token expired" — DRF
 * tokens have no expiry; the row was being deleted and a different one minted.
 *
 * An integration key is independent of sessions and scoped, so one living on a
 * phone all day cannot change the account password or reach admin endpoints.
 *
 * `last_used_at` is shown prominently on purpose: a key that quietly stopped
 * being used is exactly the failure this is meant to make obvious.
 */

function relativeTime(iso: string | null): string {
  if (!iso) return 'never used'
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'yesterday' : `${days}d ago`
}

export default function ApiKeyCard() {
  const { showToast } = useToast()

  const [keys, setKeys] = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [label, setLabel] = useState('')
  const [creating, setCreating] = useState(false)
  // The full secret, held in component state only, shown once.
  const [revealed, setRevealed] = useState<ApiKey | null>(null)
  const [copied, setCopied] = useState(false)

  function load() {
    setLoading(true)
    api.profile.listApiKeys()
      .then(setKeys)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleCreate() {
    const name = label.trim()
    if (!name) return
    setCreating(true)
    try {
      const created = await api.profile.createApiKey(name)
      setRevealed(created)
      setLabel('')
      setCopied(false)
      load()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setCreating(false)
    }
  }

  async function handleRevoke(key: ApiKey) {
    if (!window.confirm(
      `Revoke "${key.label}"? Anything using this key stops working immediately.`
    )) return
    try {
      await api.profile.revokeApiKey(key.id)
      showToast('Key revoked')
      load()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  function copy(value: string) {
    navigator.clipboard.writeText(value)
      .then(() => { setCopied(true); showToast('Copied') })
      .catch(() => showToast('Could not copy', 'error'))
  }

  const active = keys.filter((k) => k.is_active)

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5">
      <h2 className="font-bold text-gray-900 mb-1">Integration keys</h2>
      <p className="text-xs text-gray-400 mb-4">
        For agents and scripts that talk to GetFit on their own. Unlike signing
        in, these are not tied to a browser session — logging out of the web app
        will not break them.
      </p>

      {/* The secret, shown exactly once. */}
      {revealed?.key && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3">
          <p className="text-xs font-semibold text-amber-800 mb-1">
            Copy this now — it will not be shown again.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-[11px] bg-white rounded-lg px-2 py-2 break-all border border-amber-200">
              {revealed.key}
            </code>
            <button
              onClick={() => copy(revealed.key!)}
              className="text-xs font-semibold px-3 py-2 rounded-lg bg-amber-600 text-white hover:bg-amber-700 transition-colors flex-shrink-0"
            >
              {copied ? '✓' : 'Copy'}
            </button>
          </div>
          <p className="text-[11px] text-amber-700 mt-2">
            Send it as <code>Authorization: Bearer &lt;key&gt;</code>. Scope:{' '}
            {revealed.scope_display.toLowerCase()} — it cannot change your
            password or reach admin endpoints.
          </p>
          <button
            onClick={() => setRevealed(null)}
            className="text-[11px] text-amber-700 underline mt-1"
          >
            I've saved it
          </button>
        </div>
      )}

      {/* Create */}
      <div className="flex gap-2 mb-4">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleCreate() }}
          placeholder="What will use this key? e.g. phone agent"
          maxLength={64}
          className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400"
        />
        <button
          onClick={handleCreate}
          disabled={creating || !label.trim()}
          className="text-sm font-semibold px-4 py-2 rounded-xl bg-brand-500 text-white hover:bg-brand-600 disabled:opacity-40 transition-colors flex-shrink-0"
        >
          {creating ? '…' : 'Create'}
        </button>
      </div>

      {/* List */}
      {loading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : active.length === 0 ? (
        <p className="text-sm text-gray-400">
          No keys yet. Create one and paste it into your agent.
        </p>
      ) : (
        <div className="divide-y divide-gray-50">
          {active.map((key) => (
            <div key={key.id} className="flex items-center justify-between py-2.5 gap-3">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-gray-900 truncate">
                  {key.label}
                </p>
                <p className="text-[11px] text-gray-400">
                  <code>{key.prefix}…</code>
                  {' · '}
                  <span className={key.last_used_at ? '' : 'text-amber-600 font-semibold'}>
                    {relativeTime(key.last_used_at)}
                  </span>
                  {' · '}{key.scope_display.toLowerCase()}
                </p>
              </div>
              <button
                onClick={() => handleRevoke(key)}
                className="text-xs font-semibold text-red-600 hover:text-red-700 transition-colors flex-shrink-0"
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
