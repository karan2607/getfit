import { useState, useEffect } from 'react'
import { api, type AdminUser, type AdminResetLink } from '../../lib/api'
import { getErrorMessage } from '../../lib/errors'
import { useToast } from '../Toast'
import { useAuth } from '../../hooks/useAuth'

/**
 * Superuser-only: issue a password reset link for another account.
 *
 * There is no outbound email configured (RESEND_API_KEY is unset), so the
 * self-service reset flow cannot deliver its link. Rather than removing
 * verification — a page that sets a new password from just an email address
 * would let anyone who knows a user's address take over their account — the
 * operator mints the link and delivers it out of band.
 *
 * The link is single-use, expires, and supersedes any earlier one. It does not
 * reveal the existing password; it cannot, since only hashes are stored.
 */
export default function AdminResetCard() {
  const { user } = useAuth()
  const { showToast } = useToast()

  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState('')
  const [hours, setHours] = useState(24)
  const [issuing, setIssuing] = useState(false)
  const [link, setLink] = useState<AdminResetLink | null>(null)
  const [copied, setCopied] = useState(false)

  const isSuperuser = !!user?.is_superuser

  useEffect(() => {
    if (!isSuperuser) return
    setLoading(true)
    api.auth.adminListUsers()
      .then(setUsers)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [isSuperuser])

  // Renders nothing for ordinary users. The endpoints also return 403, so this
  // is presentation only — not the access control.
  if (!isSuperuser) return null

  async function handleIssue() {
    if (!selected) return
    setIssuing(true)
    setLink(null)
    setCopied(false)
    try {
      const result = await api.auth.adminIssueResetLink(selected, hours)
      setLink(result)
      if (result.superseded_links > 0) {
        showToast('Previous link for this user was invalidated')
      }
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setIssuing(false)
    }
  }

  async function handleCopy() {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link.reset_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      showToast('Could not copy — select the link and copy manually', 'error')
    }
  }

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5">
      <div className="flex items-center gap-2 mb-1">
        <h3 className="font-bold text-gray-900 text-sm">Issue a password reset</h3>
        <span className="text-[10px] font-bold uppercase bg-gray-900 text-white px-2 py-0.5 rounded-full">
          Admin
        </span>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Generates a single-use link for someone who's locked out. Send it to them
        directly — there's no email configured. It expires, and issuing a new one
        kills any previous link.
      </p>

      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">
            Account
          </label>
          <select
            value={selected}
            onChange={(e) => { setSelected(e.target.value); setLink(null) }}
            disabled={loading}
            className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-400"
          >
            <option value="">{loading ? 'Loading…' : 'Select an account…'}</option>
            {users.map((u) => (
              <option key={u.id} value={u.email}>
                {u.email}{u.name ? ` — ${u.name}` : ''}{u.is_superuser ? ' (admin)' : ''}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">
            Valid for
          </label>
          <div className="flex gap-2">
            {[1, 24, 72].map((h) => (
              <button
                key={h}
                onClick={() => setHours(h)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${hours === h ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}
              >
                {h === 1 ? '1 hour' : h === 24 ? '24 hours' : '3 days'}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleIssue}
          disabled={issuing || !selected}
          className="w-full bg-gray-900 hover:bg-gray-800 disabled:opacity-40 text-white text-sm font-semibold py-2.5 rounded-xl transition-colors"
        >
          {issuing ? 'Generating…' : 'Generate reset link'}
        </button>
      </div>

      {link && (
        <div className="mt-4 border border-emerald-200 bg-emerald-50 rounded-xl p-3">
          <p className="text-xs font-semibold text-emerald-800 mb-1">
            Link for {link.email}
          </p>
          <p className="text-[11px] text-emerald-700 mb-2">
            Expires {new Date(link.expires_at).toLocaleString()} ·
            {' '}single use
          </p>
          <div className="flex items-center gap-2">
            <input
              readOnly
              value={link.reset_url}
              onFocus={(e) => e.currentTarget.select()}
              className="flex-1 min-w-0 text-xs font-mono bg-white border border-emerald-200 rounded-lg px-2 py-2"
            />
            <button
              onClick={handleCopy}
              className="flex-shrink-0 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded-lg transition-colors"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <p className="text-[11px] text-emerald-700 mt-2">
            Anyone with this link can set that account's password until it
            expires — send it over a private channel.
          </p>
        </div>
      )}
    </div>
  )
}
