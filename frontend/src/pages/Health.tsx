import { useState, useEffect, useRef } from 'react'
import { api, type HealthDailySummary, type HealthWorkout, type HealthRecovery, type HealthCalorieBalance, type HealthActivitySuggestion } from '../lib/api'
import { useToast } from '../components/Toast'
import { getErrorMessage } from '../lib/errors'
import PageHeader from '../components/PageHeader'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function fmtDuration(secs: number | null) {
  if (!secs) return '—'
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function fmtDistance(m: number | null) {
  if (!m) return null
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`
}

function fillLast7(summaries: HealthDailySummary[]): HealthDailySummary[] {
  const byDate: Record<string, HealthDailySummary> = {}
  summaries.forEach((s) => { byDate[s.date] = s })
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date()
    d.setDate(d.getDate() - (6 - i))
    const key = d.toISOString().split('T')[0]
    return byDate[key] ?? { date: key, steps: null, active_calories: null, resting_calories: null, resting_heart_rate: null, sleep_hours: null }
  })
}

const TOOLTIP_STYLE = { fontSize: 12, borderRadius: 8, border: 'none', boxShadow: '0 2px 8px rgba(0,0,0,0.12)' }

// ── Metric Card ───────────────────────────────────────────────────────────────

function MetricCard({ title, unit, color, data, dataKey, chartType }: {
  title: string; unit: string; color: string
  data: HealthDailySummary[]
  dataKey: keyof Omit<HealthDailySummary, 'date'>; chartType: 'bar' | 'line'
}) {
  const latest = [...data].reverse().find((d) => d[dataKey] != null)
  const displayVal = latest ? (latest[dataKey] as number | null) : null
  const chartData = data.map((d) => ({ ...d, label: fmtDate(d.date) }))

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4">
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{title}</p>
        {displayVal != null && (
          <p className="text-sm font-bold text-gray-800">
            {(displayVal as number).toLocaleString('en-US', { maximumFractionDigits: 1 })}
            <span className="text-xs font-normal text-gray-400 ml-1">{unit}</span>
          </p>
        )}
      </div>
      <ResponsiveContainer width="100%" height={80}>
        {chartType === 'bar' ? (
          <BarChart data={chartData} margin={{ top: 4, right: 0, left: -28, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [v != null ? Number(v).toLocaleString() : '—', title]} />
            <Bar dataKey={dataKey} fill={color} radius={[3, 3, 0, 0]} maxBarSize={24} />
          </BarChart>
        ) : (
          <LineChart data={chartData} margin={{ top: 4, right: 0, left: -28, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [v != null ? Number(v).toLocaleString() : '—', title]} />
            <Line dataKey={dataKey} stroke={color} strokeWidth={2} dot={{ r: 3, fill: color }} connectNulls />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}

// ── Workout Row ───────────────────────────────────────────────────────────────

function WorkoutRow({ workout }: { workout: HealthWorkout }) {
  const dist = fmtDistance(workout.distance_meters)
  return (
    <div className="flex items-center gap-3 bg-white rounded-xl border border-gray-100 px-4 py-3">
      <div className="w-9 h-9 rounded-xl bg-red-50 flex items-center justify-center text-lg flex-shrink-0">🏃</div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900 truncate">{workout.activity_type}</p>
        <p className="text-xs text-gray-400">{fmtDate(workout.start_time)}</p>
      </div>
      <div className="text-right flex-shrink-0 space-y-0.5">
        <p className="text-xs font-medium text-gray-700">{fmtDuration(workout.duration_seconds)}</p>
        <div className="flex items-center gap-2 justify-end">
          {workout.calories != null && <span className="text-xs text-amber-600">{Math.round(workout.calories)} kcal</span>}
          {workout.avg_heart_rate != null && <span className="text-xs text-red-500">{Math.round(workout.avg_heart_rate)} bpm</span>}
          {dist && <span className="text-xs text-blue-500">{dist}</span>}
        </div>
      </div>
    </div>
  )
}

// ── Setup Screen ──────────────────────────────────────────────────────────────

const STEPS = [
  {
    n: 1,
    title: 'Open Shortcuts on your iPhone',
    desc: 'Tap + to create a new shortcut. Name it "Sync Health to GetFit".',
  },
  {
    n: 2,
    title: 'Add Health actions',
    desc: 'Search "Health" in actions. Add: Find Health Samples → Steps (last 1 day), Active Energy (last 1 day), Resting Heart Rate (last 1 day), Sleep Analysis (last 1 day), Workouts (last 1 day).',
  },
  {
    n: 3,
    title: 'Add a "Get Contents of URL" action',
    desc: 'Set Method to POST. URL to the endpoint below. Add header Authorization: Token <your token>. Set body to JSON with the fields: date, steps, active_calories, resting_heart_rate, sleep_hours, workouts.',
  },
  {
    n: 4,
    title: 'Set up an Automation',
    desc: 'In the Automation tab, create a Personal Automation → Time of Day (e.g. 8 AM daily) → Run the shortcut. This syncs your health data every morning automatically.',
  },
]

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={copy}
      className="ml-2 text-xs text-brand-500 hover:text-brand-600 font-semibold transition-colors flex-shrink-0"
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  )
}

function SetupScreen({ onFirstSync }: { onFirstSync: () => void }) {
  const { showToast } = useToast()
  const [token, setToken] = useState<string | null>(null)
  const [loadingToken, setLoadingToken] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const syncUrl = `${API_BASE}/api/health/shortcuts/sync/`

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }
  useEffect(() => () => stopPolling(), [])

  async function handleReveal() {
    setLoadingToken(true)
    try {
      const { token: t } = await api.health.getSetupToken()
      setToken(t)

      // Poll until shortcut posts its first sync
      stopPolling()
      let tries = 0
      pollRef.current = setInterval(async () => {
        tries++
        try {
          const s = await api.health.getStatus()
          if (s.connected) { stopPolling(); onFirstSync() }
        } catch { /* ignore */ }
        if (tries >= 45) stopPolling() // 3 min max
      }, 4000)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setLoadingToken(false)
    }
  }

  return (
    <div className="p-6 max-w-xl">
      <div className="text-center mb-8">
        <div className="text-5xl mb-3">🍎</div>
        <h2 className="text-xl font-bold text-gray-900 mb-1">Connect Apple Health</h2>
        <p className="text-sm text-gray-500">
          Use the free built-in Shortcuts app on your iPhone to sync health data automatically every day — no third-party apps or subscriptions needed.
        </p>
      </div>

      {/* API details */}
      <div className="bg-gray-50 rounded-2xl p-4 mb-6 space-y-3">
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Sync endpoint (POST)</p>
          <div className="flex items-center bg-white border border-gray-200 rounded-xl px-3 py-2">
            <span className="text-xs font-mono text-gray-700 flex-1 truncate">{syncUrl}</span>
            <CopyButton text={syncUrl} />
          </div>
        </div>
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Your API token</p>
          {token ? (
            <div className="flex items-center bg-white border border-gray-200 rounded-xl px-3 py-2">
              <span className="text-xs font-mono text-gray-700 flex-1 truncate">{token}</span>
              <CopyButton text={token} />
            </div>
          ) : (
            <button
              onClick={handleReveal}
              disabled={loadingToken}
              className="w-full bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-semibold py-2.5 rounded-xl transition-colors"
            >
              {loadingToken ? 'Loading…' : 'Reveal my token'}
            </button>
          )}
          {token && (
            <p className="text-xs text-gray-400 mt-1.5">Keep this private. Paste it as the token value in your Shortcut.</p>
          )}
        </div>
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Expected JSON body</p>
          <pre className="text-xs font-mono bg-white border border-gray-200 rounded-xl px-3 py-2 text-gray-700 overflow-x-auto whitespace-pre-wrap">{`{
  "date": "2026-05-04",
  "steps": 9241,
  "active_calories": 380,
  "resting_heart_rate": 58,
  "sleep_hours": 7.5,
  "workouts": [
    {
      "activity_type": "Running",
      "start_time": "2026-05-04T07:00:00",
      "duration_seconds": 1800,
      "calories": 290,
      "avg_heart_rate": 148,
      "distance_meters": 4200
    }
  ]
}`}</pre>
        </div>
      </div>

      {/* Steps */}
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Setup steps</p>
      <div className="space-y-3 mb-6">
        {STEPS.map((s) => (
          <div key={s.n} className="flex gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-brand-500 text-white text-xs font-bold flex items-center justify-center mt-0.5">
              {s.n}
            </span>
            <div>
              <p className="text-sm font-semibold text-gray-800">{s.title}</p>
              <p className="text-xs text-gray-500 leading-relaxed mt-0.5">{s.desc}</p>
            </div>
          </div>
        ))}
      </div>

      {token && (
        <div className="bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-3 text-xs text-emerald-700">
          Waiting for your first sync… Run the shortcut on your iPhone and this page will update automatically.
        </div>
      )}
    </div>
  )
}

// ── Recovery Card ─────────────────────────────────────────────────────────────

const RECOVERY_COLORS: Record<string, string> = {
  'Optimal': '#10b981',
  'Good': '#3b82f6',
  'Take it easy': '#f59e0b',
  'Rest day': '#ef4444',
  'No data yet': '#9ca3af',
}

function RecoveryCard({ recovery }: { recovery: HealthRecovery | null }) {
  if (!recovery) return <div className="bg-white rounded-2xl border border-gray-100 h-28 animate-pulse mb-4" />
  const color = RECOVERY_COLORS[recovery.label] ?? '#9ca3af'
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4 mb-4 flex items-center gap-5">
      <div
        className="flex-shrink-0 w-16 h-16 rounded-full flex items-center justify-center text-white font-bold text-xl"
        style={{ backgroundColor: color }}
      >
        {recovery.score ?? '—'}
      </div>
      <div>
        <p className="text-base font-semibold text-gray-800" style={{ color }}>{recovery.label}</p>
        <p className="text-xs text-gray-500 mt-0.5">Recovery score based on resting heart rate</p>
        {recovery.today_rhr != null && recovery.baseline_rhr != null && (
          <p className="text-xs text-gray-400 mt-1">
            Today {recovery.today_rhr} bpm · 7-day avg {recovery.baseline_rhr} bpm
          </p>
        )}
      </div>
    </div>
  )
}

// ── Calorie Balance Card ──────────────────────────────────────────────────────

function CalStatCol({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div className="text-center">
      <p className={`text-lg font-bold ${color ?? 'text-gray-800'}`}>{value}</p>
      <p className="text-xs text-gray-400 mt-0.5 whitespace-nowrap">{label}</p>
    </div>
  )
}

function CalorieBalanceCard({ balance }: { balance: HealthCalorieBalance | null }) {
  if (!balance) return <div className="bg-white rounded-2xl border border-gray-100 h-24 animate-pulse mb-4" />

  const net = balance.net
  const netDisplay = net != null ? (net >= 0 ? `+${net.toLocaleString()}` : net.toLocaleString()) : '—'

  const rem = balance.remaining
  const remDisplay = rem != null ? (rem >= 0 ? rem.toLocaleString() : rem.toLocaleString()) : '—'
  const remColor = rem == null ? 'text-gray-400' : rem >= 0 ? 'text-emerald-500' : 'text-red-500'

  const divider = <div className="w-px bg-gray-100 self-stretch" />

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4 mb-4">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Today's Calories</p>
      <div className="flex justify-around items-center gap-1">
        <CalStatCol value={balance.target != null ? balance.target.toLocaleString() : '—'} label="Target" />
        {divider}
        <CalStatCol value={balance.calories_in.toLocaleString()} label="Eaten" />
        {divider}
        <CalStatCol value={balance.burned != null ? balance.burned.toLocaleString() : '—'} label="Burned" />
        {divider}
        <CalStatCol value={netDisplay} label="Net" color="text-gray-600" />
        {divider}
        <CalStatCol value={remDisplay} label="Remaining" color={remColor} />
      </div>
    </div>
  )
}

// ── Activity Suggestion Banner ────────────────────────────────────────────────

const ACTIVITY_LABELS: Record<string, string> = {
  sedentary: 'Sedentary',
  lightly_active: 'Lightly Active',
  moderately_active: 'Moderately Active',
  very_active: 'Very Active',
}

function ActivitySuggestionBanner({ suggestion, onUpdate, onDismiss }: {
  suggestion: HealthActivitySuggestion
  onUpdate: () => void
  onDismiss: () => void
}) {
  const [updating, setUpdating] = useState(false)
  const { showToast } = useToast()

  async function handleUpdate() {
    setUpdating(true)
    try {
      await api.profile.update({ activity_level: suggestion.suggested as Parameters<typeof api.profile.update>[0]['activity_level'] })
      showToast('Activity level updated!')
      onUpdate()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setUpdating(false)
    }
  }

  return (
    <div className="bg-blue-50 border border-blue-100 rounded-2xl p-4 mb-4 flex items-start gap-3">
      <span className="text-blue-400 text-lg mt-0.5">💡</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-blue-800">
          Based on your activity
          {suggestion.avg_calories != null && ` (avg ${suggestion.avg_calories} kcal/day`}
          {suggestion.weekly_workouts != null && `, ${suggestion.weekly_workouts} workouts this week)`},
          you look <strong>{ACTIVITY_LABELS[suggestion.suggested!] ?? suggestion.suggested}</strong>
          {suggestion.current && ` — your profile says ${ACTIVITY_LABELS[suggestion.current] ?? suggestion.current}`}.
        </p>
      </div>
      <div className="flex gap-2 flex-shrink-0">
        <button
          onClick={handleUpdate}
          disabled={updating}
          className="text-xs font-semibold text-white bg-blue-500 hover:bg-blue-600 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
        >
          {updating ? 'Updating…' : 'Update'}
        </button>
        <button onClick={onDismiss} className="text-xs text-blue-400 hover:text-blue-600 font-medium px-2">
          Dismiss
        </button>
      </div>
    </div>
  )
}

// ── Connected Dashboard ───────────────────────────────────────────────────────

function HealthDashboard({ provider, connectedAt, onDisconnected }: {
  provider: string | null; connectedAt: string | null; onDisconnected: () => void
}) {
  const { showToast } = useToast()
  const [summaries, setSummaries] = useState<HealthDailySummary[]>([])
  const [workouts, setWorkouts] = useState<HealthWorkout[]>([])
  const [recovery, setRecovery] = useState<HealthRecovery | null>(null)
  const [balance, setBalance] = useState<HealthCalorieBalance | null>(null)
  const [suggestion, setSuggestion] = useState<HealthActivitySuggestion | null>(null)
  const [suggestionDismissed, setSuggestionDismissed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [disconnecting, setDisconnecting] = useState(false)

  useEffect(() => {
    Promise.all([
      api.health.getSummary(),
      api.health.getWorkouts(),
      api.health.getRecovery(),
      api.health.getCalorieBalance(),
      api.health.getActivitySuggestion(),
    ])
      .then(([s, w, r, b, a]) => {
        setSummaries(s)
        setWorkouts(w)
        setRecovery(r)
        setBalance(b)
        setSuggestion(a)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function handleDisconnect() {
    if (!confirm('Disconnect and delete all synced health data?')) return
    setDisconnecting(true)
    try {
      await api.health.disconnect()
      showToast('Health sync disconnected.')
      onDisconnected()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setDisconnecting(false)
    }
  }

  const chartData = fillLast7(summaries)
  const providerLabel = provider ? provider.charAt(0) + provider.slice(1).toLowerCase() : 'Health'

  return (
    <div className="p-6 max-w-2xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          <span className="text-sm font-semibold text-gray-700">🍎 {providerLabel} syncing via Shortcuts</span>
          {connectedAt && <span className="text-xs text-gray-400">since {fmtDate(connectedAt)}</span>}
        </div>
        <button
          onClick={handleDisconnect}
          disabled={disconnecting}
          className="text-xs text-red-400 hover:text-red-600 font-medium transition-colors disabled:opacity-50"
        >
          {disconnecting ? 'Disconnecting…' : 'Disconnect'}
        </button>
      </div>

      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-white rounded-2xl border border-gray-100 h-36 animate-pulse" />
          ))}
        </div>
      ) : (
        <>
          {suggestion?.suggested && !suggestionDismissed && (
            <ActivitySuggestionBanner
              suggestion={suggestion}
              onUpdate={() => setSuggestion(null)}
              onDismiss={() => setSuggestionDismissed(true)}
            />
          )}

          <RecoveryCard recovery={recovery} />
          <CalorieBalanceCard balance={balance} />

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
            <MetricCard title="Steps" unit="steps" color="#3b82f6" data={chartData} dataKey="steps" chartType="bar" />
            <MetricCard title="Active Calories" unit="kcal" color="#f59e0b" data={chartData} dataKey="active_calories" chartType="bar" />
            <MetricCard title="Resting Calories" unit="kcal" color="#10b981" data={chartData} dataKey="resting_calories" chartType="bar" />
            <MetricCard title="Resting Heart Rate" unit="bpm" color="#ef4444" data={chartData} dataKey="resting_heart_rate" chartType="line" />
          </div>

          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
            Recent Workouts from {providerLabel}
          </p>
          {workouts.length === 0 ? (
            <div className="bg-white rounded-2xl border border-gray-100 p-8 text-center text-sm text-gray-400">
              No workouts synced yet. Complete a workout and run your Shortcut — it will appear here.
            </div>
          ) : (
            <div className="space-y-2">
              {workouts.map((w) => <WorkoutRow key={w.id} workout={w} />)}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Health Page ───────────────────────────────────────────────────────────────

export default function Health() {
  const [healthStatus, setHealthStatus] = useState<{ connected: boolean; provider: string | null; connected_at: string | null } | null>(null)
  const [loading, setLoading] = useState(true)

  function loadStatus() {
    api.health.getStatus()
      .then(setHealthStatus)
      .catch(() => setHealthStatus({ connected: false, provider: null, connected_at: null }))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadStatus() }, [])

  return (
    <div>
      <PageHeader title="Health Sync" subtitle="Apple Health via Shortcuts — free, no subscriptions" />
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-5 h-5 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : healthStatus?.connected ? (
        <HealthDashboard
          provider={healthStatus.provider}
          connectedAt={healthStatus.connected_at}
          onDisconnected={() => { setLoading(true); loadStatus() }}
        />
      ) : (
        <SetupScreen onFirstSync={() => { setLoading(true); loadStatus() }} />
      )}
    </div>
  )
}
