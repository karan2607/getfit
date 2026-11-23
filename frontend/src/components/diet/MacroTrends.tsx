import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine,
} from 'recharts'
import { api, type MealLogDay } from '../../lib/api'

/**
 * Daily protein, carbs and fiber from the meal log, last 14 days.
 *
 * Totals are summed server-side (diet/meal-logs/daily/). A day with no meals
 * is a gap, not a 0g bar, and fiber is a gap on any day where no meal recorded
 * it -- meals logged before fiber was tracked would otherwise read as 0g.
 */

type MacroKey = 'protein_g' | 'carbs_g' | 'fiber_g'

const MACROS: { key: MacroKey; title: string; color: string }[] = [
  { key: 'protein_g', title: 'Protein', color: '#10b981' },
  { key: 'carbs_g', title: 'Carbs', color: '#60a5fa' },
  { key: 'fiber_g', title: 'Fiber', color: '#a3a635' },
]

const TOOLTIP_STYLE = {
  fontSize: 12, borderRadius: 8, border: 'none',
  boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
}

function dayLabel(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** The day's value, or null when there is nothing to plot for it. */
function dayValue(day: MealLogDay, key: MacroKey): number | null {
  if (day.meals === 0) return null
  const v = day[key]
  return v === null || v === undefined ? null : Math.round(v)
}

function dailyAverage(days: MealLogDay[], key: MacroKey): number | null {
  const values = days.map((d) => dayValue(d, key)).filter((v): v is number => v !== null)
  if (!values.length) return null
  return Math.round(values.reduce((a, b) => a + b, 0) / values.length)
}

function MacroChart({ macro, days, target }: {
  macro: typeof MACROS[number]; days: MealLogDay[]; target?: number
}) {
  const data = days.map((d) => ({ label: dayLabel(d.date), value: dayValue(d, macro.key) }))
  const avg = dailyAverage(days, macro.key)

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{macro.title}</p>
      <p className="text-xl font-bold text-gray-900 mt-0.5">
        {avg === null ? '—' : avg}
        <span className="text-xs font-normal text-gray-400 ml-1">g / day avg</span>
      </p>
      {target ? <p className="text-[11px] text-gray-400 mt-0.5">target {target}g</p> : null}

      {avg === null ? (
        <div className="h-[120px] flex items-center justify-center">
          <p className="text-xs text-gray-400">No {macro.title.toLowerCase()} logged in the last {days.length} days</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={data} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }}
                   axisLine={false} tickLine={false} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false}
                   tickLine={false} width={30} />
            <Tooltip contentStyle={TOOLTIP_STYLE}
                     formatter={(v) => [`${v}g`, macro.title]} />
            {target ? <ReferenceLine y={target} stroke="#cbd5e1" strokeDasharray="4 4" /> : null}
            <Bar dataKey="value" fill={macro.color} radius={[3, 3, 0, 0]} maxBarSize={22} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

export default function MacroTrends({ refreshKey, targets }: {
  /** Bump to refetch, e.g. after a meal is added or deleted. */
  refreshKey?: unknown
  targets?: Partial<Record<MacroKey, number>>
}) {
  const [days, setDays] = useState<MealLogDay[] | null>(null)

  useEffect(() => {
    api.diet.getMealLogDaily(14)
      .then(setDays)
      .catch(() => setDays(null))
  }, [refreshKey])

  if (!days || !days.some((d) => d.meals > 0)) return null

  return (
    <div className="mb-5">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Last 14 days</p>
      <div className="space-y-3">
        {MACROS.map((m) => (
          <MacroChart key={m.key} macro={m} days={days}
                      target={targets?.[m.key]} />
        ))}
      </div>
    </div>
  )
}
