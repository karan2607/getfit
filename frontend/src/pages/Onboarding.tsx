import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { api, type UserProfile } from '../lib/api'
import { getErrorMessage } from '../lib/errors'
import { useToast } from '../components/Toast'

type Step = 'body' | 'goal' | 'diet'

const STEPS: Step[] = ['body', 'goal', 'diet']

export default function Onboarding() {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()
  const { showToast } = useToast()
  const [stepIndex, setStepIndex] = useState(0)
  const [saving, setSaving] = useState(false)
  const [data, setData] = useState<Partial<UserProfile>>({})

  const step = STEPS[stepIndex]
  const isLast = stepIndex === STEPS.length - 1

  function update(fields: Partial<UserProfile>) {
    setData((prev) => ({ ...prev, ...fields }))
  }

  async function handleNext() {
    if (!isLast) {
      setStepIndex((i) => i + 1)
      return
    }
    setSaving(true)
    try {
      await api.profile.update(data)
      const updatedUser = await api.auth.me()
      setUser(updatedUser)
      navigate('/dashboard', { replace: true })
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-50 px-4">
      <div className="w-full max-w-md bg-white rounded-2xl shadow-lg p-8">
        {/* Progress dots */}
        <div className="flex gap-2 mb-8">
          {STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1.5 flex-1 rounded-full transition-colors ${i <= stepIndex ? 'bg-emerald-500' : 'bg-gray-200'}`}
            />
          ))}
        </div>

        <p className="text-sm text-emerald-600 font-medium mb-1">
          Step {stepIndex + 1} of {STEPS.length}
        </p>

        {step === 'body' && (
          <BodyStep data={data} onChange={update} />
        )}
        {step === 'goal' && (
          <GoalStep data={data} onChange={update} />
        )}
        {step === 'diet' && (
          <DietStep data={data} onChange={update} userName={user?.name ?? ''} />
        )}

        <div className="flex gap-3 mt-8">
          {stepIndex > 0 && (
            <button
              onClick={() => setStepIndex((i) => i - 1)}
              className="flex-1 border border-gray-300 text-gray-700 rounded-xl py-2.5 text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              Back
            </button>
          )}
          <button
            onClick={handleNext}
            disabled={saving}
            className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium rounded-xl py-2.5 text-sm transition-colors"
          >
            {saving ? 'Saving...' : isLast ? 'Get started' : 'Continue'}
          </button>
        </div>
      </div>
    </div>
  )
}

function BodyStep({ data, onChange }: { data: Partial<UserProfile>; onChange: (d: Partial<UserProfile>) => void }) {
  return (
    <>
      <h2 className="text-xl font-bold text-gray-900 mb-1">Tell us about yourself</h2>
      <p className="text-sm text-gray-500 mb-6">This helps us personalise your plans.</p>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Age</label>
            <input
              type="number"
              min={10}
              max={100}
              placeholder="25"
              value={data.age ?? ''}
              onChange={(e) => onChange({ age: e.target.value ? Number(e.target.value) : undefined })}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Gender</label>
            <select
              value={data.gender ?? ''}
              onChange={(e) => onChange({ gender: e.target.value as UserProfile['gender'] || null })}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-white"
            >
              <option value="">Select</option>
              <option value="male">Male</option>
              <option value="female">Female</option>
              <option value="other">Other</option>
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Height (cm)</label>
            <input
              type="number"
              min={100}
              max={250}
              placeholder="175"
              value={data.height_cm ?? ''}
              onChange={(e) => onChange({ height_cm: e.target.value ? Number(e.target.value) : undefined })}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Weight (kg)</label>
            <input
              type="number"
              min={30}
              max={300}
              placeholder="75"
              value={data.weight_kg ?? ''}
              onChange={(e) => onChange({ weight_kg: e.target.value ? Number(e.target.value) : undefined })}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Activity level</label>
          <select
            value={data.activity_level ?? ''}
            onChange={(e) => onChange({ activity_level: e.target.value as UserProfile['activity_level'] || null })}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-white"
          >
            <option value="">Select</option>
            <option value="sedentary">Sedentary (desk job, no exercise)</option>
            <option value="lightly_active">Lightly Active (1-3 days/week)</option>
            <option value="moderately_active">Moderately Active (3-5 days/week)</option>
            <option value="very_active">Very Active (6-7 days/week)</option>
          </select>
        </div>
      </div>
    </>
  )
}

function GoalStep({ data, onChange }: { data: Partial<UserProfile>; onChange: (d: Partial<UserProfile>) => void }) {
  const goals = [
    { value: 'lose_fat', label: 'Lose Fat', desc: 'Reduce body fat while preserving muscle', icon: '🔥' },
    { value: 'build_muscle', label: 'Build Muscle', desc: 'Gain strength and muscle mass', icon: '💪' },
    { value: 'maintain', label: 'Maintain', desc: 'Stay at current weight and fitness', icon: '⚖️' },
  ]
  const levels = [
    { value: 'beginner', label: 'Beginner', desc: 'New to gym training' },
    { value: 'intermediate', label: 'Intermediate', desc: '1-3 years of training' },
    { value: 'advanced', label: 'Advanced', desc: '3+ years of training' },
  ]
  return (
    <>
      <h2 className="text-xl font-bold text-gray-900 mb-1">Your fitness goal</h2>
      <p className="text-sm text-gray-500 mb-5">We'll tailor your workouts and diet around this.</p>
      <div className="space-y-2 mb-6">
        {goals.map((g) => (
          <button
            key={g.value}
            type="button"
            onClick={() => onChange({ fitness_goal: g.value as UserProfile['fitness_goal'] })}
            className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-left transition-colors ${
              data.fitness_goal === g.value
                ? 'border-emerald-500 bg-emerald-50'
                : 'border-gray-200 hover:border-gray-300'
            }`}
          >
            <span className="text-xl">{g.icon}</span>
            <div>
              <p className="text-sm font-semibold text-gray-900">{g.label}</p>
              <p className="text-xs text-gray-500">{g.desc}</p>
            </div>
          </button>
        ))}
      </div>
      <p className="text-sm font-medium text-gray-700 mb-2">Experience level</p>
      <div className="grid grid-cols-3 gap-2">
        {levels.map((l) => (
          <button
            key={l.value}
            type="button"
            onClick={() => onChange({ experience_level: l.value as UserProfile['experience_level'] })}
            className={`p-2 rounded-xl border-2 text-center transition-colors ${
              data.experience_level === l.value
                ? 'border-emerald-500 bg-emerald-50'
                : 'border-gray-200 hover:border-gray-300'
            }`}
          >
            <p className="text-xs font-semibold text-gray-900">{l.label}</p>
            <p className="text-xs text-gray-400 mt-0.5">{l.desc}</p>
          </button>
        ))}
      </div>
    </>
  )
}

function DietStep({ data, onChange, userName }: { data: Partial<UserProfile>; onChange: (d: Partial<UserProfile>) => void; userName: string }) {
  const prefs = [
    { value: 'non_veg', label: 'Non-Vegetarian', desc: 'Includes meat, fish, eggs', icon: '🍗' },
    { value: 'vegetarian', label: 'Vegetarian', desc: 'No meat or fish', icon: '🥦' },
    { value: 'vegan', label: 'Vegan', desc: 'No animal products', icon: '🌱' },
  ]
  return (
    <>
      <h2 className="text-xl font-bold text-gray-900 mb-1">Almost done, {userName.split(' ')[0]}!</h2>
      <p className="text-sm text-gray-500 mb-5">What's your dietary preference?</p>
      <div className="space-y-2">
        {prefs.map((p) => (
          <button
            key={p.value}
            type="button"
            onClick={() => onChange({ dietary_preference: p.value as UserProfile['dietary_preference'] })}
            className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-left transition-colors ${
              data.dietary_preference === p.value
                ? 'border-emerald-500 bg-emerald-50'
                : 'border-gray-200 hover:border-gray-300'
            }`}
          >
            <span className="text-xl">{p.icon}</span>
            <div>
              <p className="text-sm font-semibold text-gray-900">{p.label}</p>
              <p className="text-xs text-gray-500">{p.desc}</p>
            </div>
          </button>
        ))}
      </div>
    </>
  )
}
