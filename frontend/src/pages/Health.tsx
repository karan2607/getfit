import { useState, useEffect, useRef } from 'react'
import { api, type HealthDailySummary, type HealthWorkout } from '../lib/api'
import { useToast } from '../components/Toast'
import { getErrorMessage } from '../lib/errors'
import PageHeader from '../components/PageHeader'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
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

// Fill last 7 days so chart always has 7 bars even with gaps
function fillLast7(summaries: HealthDailySummary[]): HealthDailySummary[] {
  const byDate: Record<string, HealthDailySummary> = {}
  summaries.forEach((s) => { byDate[s.date] = s })
  const result: HealthDailySummary[] = []
  for (let i = 6; i >= 0; i--) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    const key = d.toISOString().split('T')[0]
    result.push(byDate[key] ?? { date: key, steps: null, active_calories: null, resting_heart_rate: null, sleep_hours: null })
  }
  return result
}

const CHART_TOOLTIP_STYLE = { fontSize: 12, borderRadius: 8, border: 'none', boxShadow: '0 2px 8px rgba(0,0,0,0.12)' }

// ── Metric Card ───────────────────────────────────────────────────────────────

function MetricCard({ title, unit, color, data, dataKey, chartType }: {
  title: string
  unit: string
  color: string
  data: Array<{ date: string } & Record<string, number | null>>
  dataKey: string
  chartType: 'bar' | 'line'
}) {
  const latest = [...data].reverse().find((d) => d[dataKey] != null)
  const latestVal = latest ? (d: typeof latest) => d[dataKey] : null
  const displayVal = latestVal ? latestVal(latest!) : null

  const chartData = data.map((d) => ({ ...d, label: fmtDate(d.date) }))

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4">
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{title}</p>
        {displayVal != null && (
          <p className="text-sm font-bold text-gray-800">
            {typeof displayVal === 'number' ? displayVal.toLocaleString('en-US', { maximumFractionDigits: 1 }) : displayVal}
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
            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v: number) => [v?.toLocaleString() ?? '—', title]} />
            <Bar dataKey={dataKey} fill={color} radius={[3, 3, 0, 0]} maxBarSize={24} />
          </BarChart>
        ) : (
          <LineChart data={chartData} margin={{ top: 4, right: 0, left: -28, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v: number) => [v?.toLocaleString() ?? '—', title]} />
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
      <div className="w-9 h-9 rounded-xl bg-brand-50 flex items-center justify-center text-lg flex-shrink-0">🏃</div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900 truncate">{workout.activity_type}</p>
        <p className="text-xs text-gray-400">{fmtDate(workout.start_time)}</p>
      </div>
      <div className="text-right flex-shrink-0 space-y-0.5">
        <p className="text-xs font-medium text-gray-700">{fmtDuration(workout.duration_seconds)}</p>
        <div className="flex items-center gap-2 justify-end">
          {workout.calories != null && (
            <span className="text-xs text-amber-600">{Math.round(workout.calories)} kcal</span>
          )}
          {workout.avg_heart_rate != null && (
            <span className="text-xs text-red-500">{Math.round(workout.avg_heart_rate)} bpm</span>
          )}
          {dist && <span className="text-xs text-blue-500">{dist}</span>}
        </div>
      </div>
    </div>
  )
}

// ── Connect Screen ────────────────────────────────────────────────────────────

function ConnectScreen({ onConnected }: { onConnected: () => void }) {
  const { showToast } = useToast()
  const [loading, setLoading] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function stopPolling() {
    if (pollRef.current) clearInterval(pollRef.current)
  }

  useEffect(() => () => stopPolling(), [])

  async function handleConnect() {
    setLoading(true)
    try {
      const { url } = await api.health.getConnectUrl()
      window.open(url, '_blank')

      // Poll for connection — stops after 90 s or on success
      let tries = 0
      stopPolling()
      pollRef.current = setInterval(async () => {
        tries++
        try {
          const status = await api.health.getStatus()
          if (status.connected) {
            stopPolling()
            onConnected()
          }
        } catch { /* ignore */ }
        if (tries >= 22) stopPolling() // ~90 s
      }, 4000)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] px-6 text-center">
      <div className="text-6xl mb-4">❤️</div>
      <h2 className="text-2xl font-bold text-gray-900 mb-2">Connect your Health App</h2>
      <p className="text-sm text-gray-500 max-w-sm mb-2">
        Sync workouts, steps, heart rate, calories, and sleep directly from Apple Health or Google Fit.
      </p>
      <p className="text-xs text-gray-400 max-w-xs mb-8">
        On iPhone, the connect page will open directly. On desktop, scan the QR code with your phone.
      </p>

      <div className="flex gap-4 mb-8 text-3xl">
        <span title="Apple Health">🍎</span>
        <span title="Google Fit">🟢</span>
        <span title="Garmin">⌚</span>
        <span title="Fitbit">📊</span>
      </div>

      <button
        onClick={handleConnect}
        disabled={loading}
        className="bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white font-semibold px-8 py-3 rounded-2xl text-sm transition-colors"
      >
        {loading ? 'Opening…' : 'Connect Now'}
      </button>

      {loading && (
        <p className="text-xs text-gray-400 mt-4 animate-pulse">
          Waiting for connection… (this page will update automatically)
        </p>
      )}
    </div>
  )
}

// ── Connected Dashboard ───────────────────────────────────────────────────────

function HealthDashboard({ provider, connectedAt, onDisconnected }: {
  provider: string | null
  connectedAt: string | null
  onDisconnected: () => void
}) {
  const { showToast } = useToast()
  const [summaries, setSummaries] = useState<HealthDailySummary[]>([])
  const [workouts, setWorkouts] = useState<HealthWorkout[]>([])
  const [loading, setLoading] = useState(true)
  const [disconnecting, setDisconnecting] = useState(false)

  useEffect(() => {
    Promise.all([api.health.getSummary(), api.health.getWorkouts()])
      .then(([s, w]) => { setSummaries(s); setWorkouts(w) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function handleDisconnect() {
    if (!confirm('Disconnect and delete all synced health data?')) return
    setDisconnecting(true)
    try {
      await api.health.disconnect()
      showToast('Health app disconnected.')
      onDisconnected()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
      setDisconnecting(false)
    }
  }

  const chartData = fillLast7(summaries)
  const providerLabel = provider ? provider.charAt(0) + provider.slice(1).toLowerCase() : 'Health App'

  return (
    <div className="p-6 max-w-2xl">
      {/* Connection badge */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          <span className="text-sm font-semibold text-gray-700">{providerLabel} connected</span>
          {connectedAt && (
            <span className="text-xs text-gray-400">since {fmtDate(connectedAt)}</span>
          )}
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
          {/* Metric charts */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
            <MetricCard
              title="Steps"
              unit="steps"
              color="#3b82f6"
              data={chartData}
              dataKey="steps"
              chartType="bar"
            />
            <MetricCard
              title="Active Calories"
              unit="kcal"
              color="#f59e0b"
              data={chartData}
              dataKey="active_calories"
              chartType="bar"
            />
            <MetricCard
              title="Resting Heart Rate"
              unit="bpm"
              color="#ef4444"
              data={chartData}
              dataKey="resting_heart_rate"
              chartType="line"
            />
            <MetricCard
              title="Sleep"
              unit="hrs"
              color="#8b5cf6"
              data={chartData}
              dataKey="sleep_hours"
              chartType="bar"
            />
          </div>

          {/* Recent workouts */}
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
            Recent Workouts from {providerLabel}
          </p>
          {workouts.length === 0 ? (
            <div className="bg-white rounded-2xl border border-gray-100 p-8 text-center text-sm text-gray-400">
              No workouts synced yet. Complete a workout in your Health app and it will appear here.
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
  const [status, setStatus] = useState<{ connected: boolean; provider: string | null; connected_at: string | null } | null>(null)
  const [loading, setLoading] = useState(true)

  function loadStatus() {
    api.health.getStatus()
      .then(setStatus)
      .catch(() => setStatus({ connected: false, provider: null, connected_at: null }))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadStatus() }, [])

  return (
    <div>
      <PageHeader title="Health Sync" subtitle="Apple Health, Google Fit & more" />
      {loading ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <div className="w-5 h-5 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : status?.connected ? (
        <HealthDashboard
          provider={status.provider}
          connectedAt={status.connected_at}
          onDisconnected={() => { setLoading(true); loadStatus() }}
        />
      ) : (
        <ConnectScreen onConnected={() => { setLoading(true); loadStatus() }} />
      )}
    </div>
  )
}
