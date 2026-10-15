import { useState, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from 'recharts'
import { api, type HealthRange, type HealthSeries, type HealthBucket } from '../../lib/api'
import { SkeletonText } from '../Skeleton'

/**
 * Health trends with a daily / weekly / 6-month toggle.
 *
 * Bucketing is done server-side (api/health_series.py) and shared with the
 * `get_health_series` MCP tool, so a chart and an agent can never disagree
 * about what a range means.
 *
 * Every value is a PER-DAY AVERAGE within its bucket rather than a bucket
 * total. A weekly step total is ~7x a daily one, so totals would make the
 * y-axis jump an order of magnitude on every toggle and bury the actual trend.
 */

const RANGES: { key: HealthRange; label: string; hint: string }[] = [
  { key: 'daily', label: 'Daily', hint: 'last 14 days' },
  { key: 'weekly', label: 'Weekly', hint: 'last 12 weeks' },
  { key: 'monthly', label: '6 months', hint: 'monthly average' },
]

type MetricKey = 'steps' | 'active_calories' | 'resting_calories'
  | 'resting_heart_rate' | 'sleep_hours'

const METRICS: {
  key: MetricKey
  title: string
  unit: string
  color: string
  chart: 'bar' | 'line'
  decimals: number
  /** Lower is better, so a downward change should read as positive. */
  lowerIsBetter?: boolean
}[] = [
  { key: 'steps', title: 'Steps', unit: '', color: '#0ea5e9', chart: 'bar', decimals: 0 },
  { key: 'active_calories', title: 'Active calories', unit: 'kcal', color: '#f97316', chart: 'bar', decimals: 0 },
  { key: 'sleep_hours', title: 'Sleep', unit: 'h', color: '#8b5cf6', chart: 'line', decimals: 1 },
  { key: 'resting_heart_rate', title: 'Resting HR', unit: 'bpm', color: '#ef4444', chart: 'line', decimals: 0, lowerIsBetter: true },
  { key: 'resting_calories', title: 'Resting calories', unit: 'kcal', color: '#64748b', chart: 'line', decimals: 0 },
]

const TOOLTIP_STYLE = {
  fontSize: 12, borderRadius: 8, border: 'none',
  boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
}

function fmt(value: number | null, decimals: number) {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  })
}

function average(buckets: HealthBucket[], key: MetricKey): number | null {
  const values = buckets.map((b) => b[key]).filter((v): v is number => v !== null)
  if (!values.length) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

function ChangeBadge({ change, pct, lowerIsBetter }: {
  change: number | null; pct: number | null; lowerIsBetter?: boolean
}) {
  if (change === null || change === 0) return null
  const improving = lowerIsBetter ? change < 0 : change > 0
  return (
    <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded ${improving ? 'text-emerald-700 bg-emerald-50' : 'text-amber-700 bg-amber-50'}`}>
      {change > 0 ? '↑' : '↓'} {Math.abs(pct ?? 0).toFixed(0)}%
    </span>
  )
}

function MetricChart({ metric, series }: {
  metric: typeof METRICS[number]; series: HealthSeries
}) {
  const stat = series.summary[metric.key] ?? {
    latest: null, previous: null, change: null, change_pct: null,
  }
  const avg = average(series.buckets, metric.key)
  const hasAny = series.buckets.some((b) => b[metric.key] !== null)

  const data = series.buckets.map((b) => ({
    label: b.label,
    value: b[metric.key],
    days: b.days_with_data,
  }))

  const bucketNoun = series.bucket === 'day' ? 'day'
    : series.bucket === 'week' ? 'week' : 'month'

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
            {metric.title}
          </p>
          <div className="flex items-baseline gap-2 mt-0.5">
            <p className="text-xl font-bold text-gray-900">
              {fmt(stat.latest, metric.decimals)}
              {metric.unit && (
                <span className="text-xs font-normal text-gray-400 ml-1">{metric.unit}</span>
              )}
            </p>
            <ChangeBadge change={stat.change} pct={stat.change_pct}
                         lowerIsBetter={metric.lowerIsBetter} />
          </div>
          {/* State the denominator, so a per-day average is never mistaken
              for a weekly total. */}
          <p className="text-[11px] text-gray-400 mt-0.5">
            {series.bucket === 'day' ? 'latest day' : `latest ${bucketNoun}, daily avg`}
            {avg !== null && ` · ${fmt(avg, metric.decimals)} avg overall`}
          </p>
        </div>
      </div>

      {!hasAny ? (
        <div className="h-[132px] flex items-center justify-center">
          <p className="text-xs text-gray-400">No {metric.title.toLowerCase()} data in this range</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={132}>
          {metric.chart === 'bar' ? (
            <BarChart data={data} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }}
                     axisLine={false} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false}
                     tickLine={false} width={44} />
              <Tooltip contentStyle={TOOLTIP_STYLE}
                       formatter={(v) => [fmt(v as number, metric.decimals) + (metric.unit ? ` ${metric.unit}` : ''), metric.title]}
                       labelFormatter={(l, p) => {
                         const days = p?.[0]?.payload?.days
                         return series.bucket === 'day' ? String(l)
                           : `${l} · ${days ?? 0} day${days === 1 ? '' : 's'} of data`
                       }} />
              {avg !== null && (
                <ReferenceLine y={avg} stroke="#cbd5e1" strokeDasharray="4 4" />
              )}
              <Bar dataKey="value" fill={metric.color} radius={[3, 3, 0, 0]} maxBarSize={28} />
            </BarChart>
          ) : (
            <LineChart data={data} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }}
                     axisLine={false} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false}
                     tickLine={false} width={44}
                     domain={['dataMin - 2', 'dataMax + 2']} />
              <Tooltip contentStyle={TOOLTIP_STYLE}
                       formatter={(v) => [fmt(v as number, metric.decimals) + (metric.unit ? ` ${metric.unit}` : ''), metric.title]}
                       labelFormatter={(l, p) => {
                         const days = p?.[0]?.payload?.days
                         return series.bucket === 'day' ? String(l)
                           : `${l} · ${days ?? 0} day${days === 1 ? '' : 's'} of data`
                       }} />
              {avg !== null && (
                <ReferenceLine y={avg} stroke="#cbd5e1" strokeDasharray="4 4" />
              )}
              {/* connectNulls false: a sync gap should read as a gap. */}
              <Line dataKey="value" stroke={metric.color} strokeWidth={2}
                    dot={{ r: 2.5, fill: metric.color }} connectNulls={false} />
            </LineChart>
          )}
        </ResponsiveContainer>
      )}
    </div>
  )
}

export default function HealthTrends({ latestSummaryDate }: {
  latestSummaryDate?: string | null
}) {
  const [range, setRange] = useState<HealthRange>('daily')
  const [series, setSeries] = useState<HealthSeries | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    api.health.getSeries(range)
      .then((s) => { if (!cancelled) setSeries(s) })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [range])

  // If the newest data is weeks old, the charts will look empty for no obvious
  // reason. Name the likely cause rather than showing a blank panel.
  const staleDays = latestSummaryDate
    ? Math.floor((Date.now() - new Date(latestSummaryDate).getTime()) / 86_400_000)
    : null

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-gray-900">Trends</h2>
        <div className="flex rounded-lg overflow-hidden border border-gray-200 text-xs font-medium">
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              title={r.hint}
              className={`px-3 py-1.5 transition-colors ${range === r.key ? 'bg-gray-900 text-white' : 'bg-white text-gray-500 hover:bg-gray-50'}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {staleDays !== null && staleDays > 7 && (
        <div className="mb-3 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Your newest health data is <span className="font-semibold">{staleDays} days old</span>.
          If your sync runs daily, it is probably sending a fixed date instead of
          today — remove the date field from the Shortcut and the server will use today.
        </div>
      )}

      {loading ? (
        <SkeletonText lines={8} />
      ) : error ? (
        <p className="text-sm text-gray-400 py-8 text-center">Couldn't load trends.</p>
      ) : !series || !series.has_data ? (
        <div className="bg-white rounded-2xl border border-gray-100 p-8 text-center">
          <p className="text-sm text-gray-500">No health data in this range.</p>
          <p className="text-xs text-gray-400 mt-1">
            {series ? `Looked at ${series.from} to ${series.to}.` : ''} Try a wider range.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {METRICS.map((m) => (
            <MetricChart key={m.key} metric={m} series={series} />
          ))}
        </div>
      )}
    </div>
  )
}
