import { useState, useEffect } from 'react'
import { useAuth } from '../hooks/useAuth'
import { api, type UserProfile } from '../lib/api'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'
import PageHeader from '../components/PageHeader'

function PersonalNotesCard({ value, onChange, onSave }: {
  value: string
  onChange: (v: string) => void
  onSave: (v: string) => Promise<void>
}) {
  const { showToast } = useToast()
  const [editing, setEditing] = useState(!value)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    setSaving(true)
    try {
      await onSave(draft)
      setEditing(false)
      showToast('Notes saved')
    } catch {
      showToast('Failed to save notes', 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    setSaving(true)
    try {
      await onSave('')
      setDraft('')
      onChange('')
      setEditing(true)
      showToast('Notes cleared')
    } catch {
      showToast('Failed to clear notes', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-3">
      <div>
        <h2 className="text-base font-semibold text-gray-900">Personal Notes</h2>
        <p className="text-xs text-gray-500 mt-0.5">
          The AI reads these notes in every conversation — use this to share injuries, preferences, schedule constraints, or anything else you want it to remember.
        </p>
      </div>
      {editing ? (
        <>
          <textarea
            rows={5}
            placeholder={`e.g. "I have a lower back issue so avoid heavy deadlifts. I can only train on weekdays. I prefer compound movements. Currently cutting for summer."`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 resize-none"
          />
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={saving || !draft.trim()}
              className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg py-2 transition-colors"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            {value && (
              <button
                onClick={() => { setDraft(value); setEditing(false) }}
                className="px-4 text-sm text-gray-500 hover:text-gray-700 font-medium"
              >
                Cancel
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="bg-gray-50 rounded-xl p-4">
          <p className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">{value}</p>
          <div className="flex gap-3 mt-3">
            <button
              onClick={() => { setDraft(value); setEditing(true) }}
              className="text-xs font-semibold text-emerald-600 hover:text-emerald-700 transition-colors"
            >
              Edit
            </button>
            <button
              onClick={handleDelete}
              disabled={saving}
              className="text-xs font-semibold text-red-400 hover:text-red-600 transition-colors disabled:opacity-50"
            >
              Delete
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

export default function Profile() {
  const { user, setUser } = useAuth()
  const { showToast } = useToast()
  const [saving, setSaving] = useState(false)
  const [changingPassword, setChangingPassword] = useState(false)
  const [form, setForm] = useState<Partial<UserProfile>>({})
  const [name, setName] = useState(user?.name ?? '')
  const [pwForm, setPwForm] = useState({ current: '', next: '', confirm: '' })

  useEffect(() => {
    api.profile.get().then((p) => setForm(p)).catch(() => {})
  }, [])

  function update(fields: Partial<UserProfile>) {
    setForm((prev) => ({ ...prev, ...fields }))
  }

  async function handleSave() {
    setSaving(true)
    try {
      await Promise.all([
        api.profile.update(form),
        name !== user?.name ? api.auth.updateMe({ name }) : Promise.resolve(),
      ])
      const updatedUser = await api.auth.me()
      setUser(updatedUser)
      showToast('Profile saved', 'success')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault()
    if (pwForm.next !== pwForm.confirm) {
      showToast('Passwords do not match', 'error')
      return
    }
    setChangingPassword(true)
    try {
      const { token } = await api.auth.changePassword(pwForm.current, pwForm.next)
      localStorage.setItem('token', token)
      setPwForm({ current: '', next: '', confirm: '' })
      showToast('Password changed', 'success')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setChangingPassword(false)
    }
  }

  const inputCls = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-white'
  const labelCls = 'block text-sm font-medium text-gray-700 mb-1'

  const goals = [
    { value: 'lose_fat', label: 'Lose Fat', icon: '🔥' },
    { value: 'build_muscle', label: 'Build Muscle', icon: '💪' },
    { value: 'maintain', label: 'Maintain', icon: '⚖️' },
  ]
  const levels = [
    { value: 'beginner', label: 'Beginner' },
    { value: 'intermediate', label: 'Intermediate' },
    { value: 'advanced', label: 'Advanced' },
  ]
  const prefs = [
    { value: 'non_veg', label: 'Non-Veg', icon: '🍗' },
    { value: 'vegetarian', label: 'Vegetarian', icon: '🥦' },
    { value: 'vegan', label: 'Vegan', icon: '🌱' },
  ]
  const activity = [
    { value: 'sedentary', label: 'Sedentary' },
    { value: 'lightly_active', label: 'Lightly Active' },
    { value: 'moderately_active', label: 'Moderately Active' },
    { value: 'very_active', label: 'Very Active' },
  ]

  return (
    <div>
      <PageHeader title="Profile" subtitle="Personal info and fitness preferences" />
      <div className="p-6 max-w-2xl mx-auto space-y-8">

      {/* Account */}
      <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4">
        <h2 className="text-base font-semibold text-gray-900">Account</h2>
        <div>
          <label className={labelCls}>Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Email</label>
          <input
            type="email"
            value={user?.email ?? ''}
            disabled
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-400 cursor-not-allowed"
          />
        </div>
      </section>

      {/* Body Stats */}
      <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4">
        <h2 className="text-base font-semibold text-gray-900">Body Stats</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Age</label>
            <input
              type="number" min={10} max={100}
              value={form.age ?? ''}
              onChange={(e) => update({ age: e.target.value ? Number(e.target.value) : null })}
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Gender</label>
            <select
              value={form.gender ?? ''}
              onChange={(e) => update({ gender: e.target.value as UserProfile['gender'] || null })}
              className={inputCls}
            >
              <option value="">Select</option>
              <option value="male">Male</option>
              <option value="female">Female</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>Height (cm)</label>
            <input
              type="number" min={100} max={250}
              value={form.height_cm ?? ''}
              onChange={(e) => update({ height_cm: e.target.value ? Number(e.target.value) : null })}
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Weight (kg)</label>
            <input
              type="number" min={30} max={300}
              value={form.weight_kg ?? ''}
              onChange={(e) => update({ weight_kg: e.target.value ? Number(e.target.value) : null })}
              className={inputCls}
            />
          </div>
        </div>
        <div>
          <label className={labelCls}>Weight unit</label>
          <div className="flex gap-2">
            {(['lb', 'kg'] as const).map((u) => (
              <button
                key={u}
                type="button"
                onClick={() => update({ preferred_unit: u })}
                className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
                  (form.preferred_unit ?? 'lb') === u
                    ? 'bg-brand-500 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {u}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className={labelCls}>Activity Level</label>
          <div className="grid grid-cols-2 gap-2">
            {activity.map((a) => (
              <button
                key={a.value}
                type="button"
                onClick={() => update({ activity_level: a.value as UserProfile['activity_level'] })}
                className={`px-3 py-2 rounded-lg border-2 text-sm font-medium transition-colors text-left ${
                  form.activity_level === a.value
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-700'
                    : 'border-gray-200 text-gray-700 hover:border-gray-300'
                }`}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Fitness Goal */}
      <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4">
        <h2 className="text-base font-semibold text-gray-900">Fitness Goal</h2>
        <div className="grid grid-cols-3 gap-2">
          {goals.map((g) => (
            <button
              key={g.value}
              type="button"
              onClick={() => update({ fitness_goal: g.value as UserProfile['fitness_goal'] })}
              className={`p-3 rounded-xl border-2 text-center transition-colors ${
                form.fitness_goal === g.value
                  ? 'border-emerald-500 bg-emerald-50'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="text-xl mb-1">{g.icon}</div>
              <p className="text-xs font-semibold text-gray-900">{g.label}</p>
            </button>
          ))}
        </div>
        <div>
          <label className={labelCls}>Experience Level</label>
          <div className="grid grid-cols-3 gap-2">
            {levels.map((l) => (
              <button
                key={l.value}
                type="button"
                onClick={() => update({ experience_level: l.value as UserProfile['experience_level'] })}
                className={`px-3 py-2 rounded-lg border-2 text-sm font-medium transition-colors ${
                  form.experience_level === l.value
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-700'
                    : 'border-gray-200 text-gray-700 hover:border-gray-300'
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Diet Preference */}
      <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4">
        <h2 className="text-base font-semibold text-gray-900">Dietary Preference</h2>
        <div className="grid grid-cols-3 gap-2">
          {prefs.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => update({ dietary_preference: p.value as UserProfile['dietary_preference'] })}
              className={`p-3 rounded-xl border-2 text-center transition-colors ${
                form.dietary_preference === p.value
                  ? 'border-emerald-500 bg-emerald-50'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="text-xl mb-1">{p.icon}</div>
              <p className="text-xs font-semibold text-gray-900">{p.label}</p>
            </button>
          ))}
        </div>
      </section>

      {/* Personal Notes (AI Memory) */}
      <PersonalNotesCard
        value={form.personal_notes ?? ''}
        onChange={(v) => update({ personal_notes: v })}
        onSave={async (v) => {
          await api.profile.update({ personal_notes: v })
          update({ personal_notes: v })
        }}
      />

      <button
        onClick={handleSave}
        disabled={saving}
        className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors"
      >
        {saving ? 'Saving...' : 'Save Profile'}
      </button>

      {/* Change Password */}
      <section className="bg-white rounded-2xl border border-gray-200 p-6">
        <h2 className="text-base font-semibold text-gray-900 mb-4">Change Password</h2>
        <form onSubmit={handleChangePassword} className="space-y-3">
          <div>
            <label className={labelCls}>Current Password</label>
            <input
              type="password"
              value={pwForm.current}
              onChange={(e) => setPwForm((p) => ({ ...p, current: e.target.value }))}
              className={inputCls}
              required
            />
          </div>
          <div>
            <label className={labelCls}>New Password</label>
            <input
              type="password"
              value={pwForm.next}
              onChange={(e) => setPwForm((p) => ({ ...p, next: e.target.value }))}
              className={inputCls}
              required
            />
          </div>
          <div>
            <label className={labelCls}>Confirm New Password</label>
            <input
              type="password"
              value={pwForm.confirm}
              onChange={(e) => setPwForm((p) => ({ ...p, confirm: e.target.value }))}
              className={inputCls}
              required
            />
          </div>
          <button
            type="submit"
            disabled={changingPassword}
            className="w-full border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 font-medium rounded-xl py-2.5 text-sm transition-colors"
          >
            {changingPassword ? 'Updating...' : 'Update Password'}
          </button>
        </form>
      </section>
      </div>
    </div>
  )
}
