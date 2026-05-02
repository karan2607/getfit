import { useState, useEffect, useRef } from 'react'
import { api, type DietPlan, type DietPlanDetail, type DietPlanPreview, type FoodScanResult, type MealLog } from '../lib/api'
import { useToast } from '../components/Toast'
import { getErrorMessage } from '../lib/errors'
import PageHeader from '../components/PageHeader'

// ── Macro Donut ─────────────────────────────────────────────────────────────

function MacroDonut({ protein, carbs, fat, calories }: { protein: number; carbs: number; fat: number; calories: number }) {
  const total = protein + carbs + fat
  if (total === 0) return null

  const proteinPct = (protein / total) * 100
  const carbsPct = (carbs / total) * 100
  const fatPct = (fat / total) * 100

  const r = 36
  const circ = 2 * Math.PI * r
  const pDash = (proteinPct / 100) * circ
  const cDash = (carbsPct / 100) * circ
  const fDash = (fatPct / 100) * circ
  const pOffset = 0
  const cOffset = -(pDash)
  const fOffset = -(pDash + cDash)

  return (
    <div className="flex items-center gap-6">
      <div className="relative w-24 h-24 flex-shrink-0">
        <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
          <circle cx="50" cy="50" r={r} fill="none" stroke="#f3f4f6" strokeWidth="14" />
          <circle cx="50" cy="50" r={r} fill="none" stroke="#10b981" strokeWidth="14"
            strokeDasharray={`${pDash} ${circ}`} strokeDashoffset={pOffset} />
          <circle cx="50" cy="50" r={r} fill="none" stroke="#3b82f6" strokeWidth="14"
            strokeDasharray={`${cDash} ${circ}`} strokeDashoffset={cOffset} />
          <circle cx="50" cy="50" r={r} fill="none" stroke="#f59e0b" strokeWidth="14"
            strokeDasharray={`${fDash} ${circ}`} strokeDashoffset={fOffset} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-lg font-bold text-gray-900 leading-none">{calories}</span>
          <span className="text-[10px] text-gray-500">kcal</span>
        </div>
      </div>
      <div className="space-y-1.5 text-sm">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 flex-shrink-0" />
          <span className="text-gray-600">Protein</span>
          <span className="ml-auto font-semibold text-gray-900">{protein}g</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-blue-500 flex-shrink-0" />
          <span className="text-gray-600">Carbs</span>
          <span className="ml-auto font-semibold text-gray-900">{carbs}g</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-amber-500 flex-shrink-0" />
          <span className="text-gray-600">Fat</span>
          <span className="ml-auto font-semibold text-gray-900">{fat}g</span>
        </div>
      </div>
    </div>
  )
}

// ── Plan List ────────────────────────────────────────────────────────────────

function PlanList({ onGenerate, onView }: { onGenerate: () => void; onView: (id: string) => void }) {
  const { showToast } = useToast()
  const [plans, setPlans] = useState<DietPlan[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.diet.listPlans()
      .then(setPlans)
      .catch(() => showToast('Failed to load diet plans', 'error'))
      .finally(() => setLoading(false))
  }, [])

  async function handleActivate(id: string) {
    try {
      const updated = await api.diet.activatePlan(id)
      setPlans((p) => p.map((plan) => ({ ...plan, is_active: plan.id === updated.id })))
      showToast('Plan activated!')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.diet.deletePlan(id)
      setPlans((p) => p.filter((plan) => plan.id !== id))
      showToast('Plan deleted.')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  if (loading) return <div className="p-6 text-sm text-gray-400">Loading...</div>

  return (
    <div className="p-6 max-w-2xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Diet Plans</h1>
          <p className="text-sm text-gray-500 mt-0.5">AI-generated daily meal plans tailored to your goals</p>
        </div>
        <button
          onClick={onGenerate}
          className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-4 py-2 rounded-xl transition-colors"
        >
          + Generate plan
        </button>
      </div>

      {plans.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <div className="text-4xl mb-3">🥗</div>
          <p className="font-medium text-gray-600 mb-1">No diet plans yet</p>
          <p className="text-sm mb-4">Generate an AI meal plan based on your profile and calorie targets.</p>
          <button
            onClick={onGenerate}
            className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-5 py-2.5 rounded-xl transition-colors"
          >
            Generate my first plan
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className="bg-white rounded-2xl border border-gray-100 p-4 flex items-start gap-4"
            >
              <div className="flex-1 min-w-0 cursor-pointer" onClick={() => onView(plan.id)}>
                <div className="flex items-center gap-2 mb-0.5">
                  <p className="font-semibold text-gray-900 truncate">{plan.title}</p>
                  {plan.is_active && (
                    <span className="text-[10px] bg-emerald-100 text-emerald-700 font-semibold px-2 py-0.5 rounded-full flex-shrink-0">
                      Active
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-500">{plan.target_calories} kcal · {plan.protein_g}g P · {plan.carbs_g}g C · {plan.fat_g}g F</p>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {!plan.is_active && (
                  <button
                    onClick={() => handleActivate(plan.id)}
                    className="text-xs text-emerald-600 hover:text-emerald-700 font-medium px-3 py-1.5 rounded-lg hover:bg-emerald-50 transition-colors"
                  >
                    Activate
                  </button>
                )}
                <button
                  onClick={() => handleDelete(plan.id)}
                  className="text-xs text-red-400 hover:text-red-600 font-medium px-3 py-1.5 rounded-lg hover:bg-red-50 transition-colors"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Generate Flow ────────────────────────────────────────────────────────────

const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack']
const MEAL_ICONS: Record<string, string> = { breakfast: '🌅', lunch: '☀️', dinner: '🌙', snack: '🍎' }

function GeneratePlanFlow({ onBack, onSaved }: { onBack: () => void; onSaved: () => void }) {
  const { showToast } = useToast()
  const [step, setStep] = useState<'generating' | 'preview'>('generating')
  const [preview, setPreview] = useState<DietPlanPreview | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.diet.generatePlan()
      .then((data) => {
        setPreview(data)
        setStep('preview')
      })
      .catch((err) => {
        showToast(getErrorMessage(err), 'error')
        onBack()
      })
  }, [])

  async function handleSave() {
    if (!preview) return
    setSaving(true)
    try {
      await api.diet.savePlan(preview)
      showToast('Diet plan saved!')
      onSaved()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  if (step === 'generating') {
    return (
      <div className="p-6 flex flex-col items-center justify-center min-h-64 text-gray-400">
        <div className="text-3xl mb-3 animate-bounce">🥗</div>
        <p className="text-sm font-medium text-gray-600">Generating your personalized diet plan...</p>
      </div>
    )
  }

  if (!preview) return null

  const sortedMeals = [...preview.meals].sort(
    (a, b) => MEAL_ORDER.indexOf(a.meal_type) - MEAL_ORDER.indexOf(b.meal_type)
  )

  return (
    <div className="p-6 max-w-2xl">
      <button onClick={onBack} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6 transition-colors">
        ← Back to plans
      </button>

      <h1 className="text-2xl font-bold text-gray-900 mb-1">{preview.title}</h1>
      {preview.description && <p className="text-sm text-gray-500 mb-4">{preview.description}</p>}

      <div className="bg-white rounded-2xl border border-gray-100 p-5 mb-5">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-4">Daily Targets</p>
        <MacroDonut
          protein={preview.protein_g}
          carbs={preview.carbs_g}
          fat={preview.fat_g}
          calories={preview.target_calories}
        />
      </div>

      <div className="space-y-3 mb-6">
        {sortedMeals.map((meal, i) => (
          <div key={i} className="bg-white rounded-2xl border border-gray-100 p-4">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg">{MEAL_ICONS[meal.meal_type] || '🍽️'}</span>
              <div>
                <p className="font-semibold text-gray-900 text-sm">{meal.name}</p>
                <p className="text-xs text-gray-400 capitalize">{meal.meal_type}</p>
              </div>
              <span className="ml-auto text-sm font-semibold text-gray-700">{meal.calories} kcal</span>
            </div>
            {meal.description && <p className="text-xs text-gray-500 mb-2 pl-8">{meal.description}</p>}
            <div className="flex gap-3 pl-8 text-xs text-gray-500">
              <span className="text-emerald-600 font-medium">{meal.protein_g}g P</span>
              <span className="text-blue-500 font-medium">{meal.carbs_g}g C</span>
              <span className="text-amber-500 font-medium">{meal.fat_g}g F</span>
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium rounded-xl py-3 text-sm transition-colors"
      >
        {saving ? 'Saving...' : 'Save this plan'}
      </button>
    </div>
  )
}

// ── Plan Detail ──────────────────────────────────────────────────────────────

function PlanDetailView({ planId, onBack }: { planId: string; onBack: () => void }) {
  const [plan, setPlan] = useState<DietPlanDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.diet.getPlan(planId)
      .then(setPlan)
      .finally(() => setLoading(false))
  }, [planId])

  if (loading) return <div className="p-6 text-sm text-gray-400">Loading...</div>
  if (!plan) return null

  const sortedMeals = [...plan.meals].sort(
    (a, b) => MEAL_ORDER.indexOf(a.meal_type) - MEAL_ORDER.indexOf(b.meal_type)
  )

  return (
    <div className="p-6 max-w-2xl">
      <button onClick={onBack} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6 transition-colors">
        ← Back to plans
      </button>

      <div className="flex items-center gap-2 mb-1">
        <h1 className="text-2xl font-bold text-gray-900">{plan.title}</h1>
        {plan.is_active && (
          <span className="text-xs bg-emerald-100 text-emerald-700 font-semibold px-2 py-0.5 rounded-full">Active</span>
        )}
      </div>
      {plan.description && <p className="text-sm text-gray-500 mb-4">{plan.description}</p>}

      <div className="bg-white rounded-2xl border border-gray-100 p-5 mb-5">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-4">Daily Targets</p>
        <MacroDonut
          protein={plan.protein_g}
          carbs={plan.carbs_g}
          fat={plan.fat_g}
          calories={plan.target_calories}
        />
      </div>

      <div className="space-y-3">
        {sortedMeals.map((meal) => (
          <div key={meal.id} className="bg-white rounded-2xl border border-gray-100 p-4">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-lg">{MEAL_ICONS[meal.meal_type] || '🍽️'}</span>
              <div className="flex-1">
                <p className="font-semibold text-gray-900 text-sm">{meal.name}</p>
                <p className="text-xs text-gray-400 capitalize">{meal.meal_type}</p>
              </div>
              <span className="text-sm font-semibold text-gray-700">{meal.calories} kcal</span>
            </div>
            {meal.description && <p className="text-xs text-gray-500 mb-2 pl-8">{meal.description}</p>}
            <div className="flex gap-3 pl-8 text-xs text-gray-500">
              <span className="text-emerald-600 font-medium">{meal.protein_g}g P</span>
              <span className="text-blue-500 font-medium">{meal.carbs_g}g C</span>
              <span className="text-amber-500 font-medium">{meal.fat_g}g F</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Food Scanner ─────────────────────────────────────────────────────────────

function FoodScanCard({ scan }: { scan: FoodScanResult }) {
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-4">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="font-semibold text-gray-900 text-sm">{scan.food_name}</p>
          {scan.serving_size && <p className="text-xs text-gray-400">{scan.serving_size}</p>}
        </div>
        <span className="text-sm font-bold text-gray-700">{scan.calories} kcal</span>
      </div>
      <div className="flex gap-3 text-xs">
        <span className="text-emerald-600 font-medium">{scan.protein_g}g P</span>
        <span className="text-blue-500 font-medium">{scan.carbs_g}g C</span>
        <span className="text-amber-500 font-medium">{scan.fat_g}g F</span>
        {scan.fiber_g != null && <span className="text-gray-400">{scan.fiber_g}g fiber</span>}
      </div>
      {scan.notes && <p className="text-xs text-gray-400 mt-2">{scan.notes}</p>}
      <p className="text-[10px] text-gray-300 mt-1">{new Date(scan.created_at).toLocaleString()}</p>
    </div>
  )
}

function FoodScannerView() {
  const { showToast } = useToast()
  const [scanning, setScanning] = useState(false)
  const [result, setResult] = useState<FoodScanResult | null>(null)
  const [history, setHistory] = useState<FoodScanResult[]>([])
  const [loadingHistory, setLoadingHistory] = useState(true)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.diet.foodScanHistory()
      .then(setHistory)
      .finally(() => setLoadingHistory(false))
  }, [])

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setScanning(true)
    setResult(null)
    try {
      const scan = await api.diet.scanFood(file)
      setResult(scan)
      setHistory((h) => [scan, ...h])
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setScanning(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Food Scanner</h1>
      <p className="text-sm text-gray-500 mb-6">Take a photo of your meal to get instant nutritional info</p>

      <div
        onClick={() => !scanning && fileRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-8 text-center cursor-pointer transition-colors mb-6 ${
          scanning ? 'border-emerald-300 bg-emerald-50 cursor-wait' : 'border-gray-200 hover:border-emerald-300 hover:bg-emerald-50/50'
        }`}
      >
        {scanning ? (
          <>
            <div className="text-3xl mb-2 animate-bounce">🔍</div>
            <p className="text-sm font-medium text-emerald-600">Analyzing your food...</p>
          </>
        ) : (
          <>
            <div className="text-3xl mb-2">📸</div>
            <p className="text-sm font-medium text-gray-700 mb-1">Upload a food photo</p>
            <p className="text-xs text-gray-400">Click to select or drag & drop</p>
          </>
        )}
        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleFileChange} />
      </div>

      {result && (
        <div className="mb-6">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Latest result</p>
          <FoodScanCard scan={result} />
        </div>
      )}

      {!loadingHistory && history.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Scan history</p>
          <div className="space-y-3">
            {history.slice(result ? 1 : 0).map((scan) => (
              <FoodScanCard key={scan.id} scan={scan} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Meal Logger ───────────────────────────────────────────────────────────────

const MEAL_TYPE_OPTIONS = ['breakfast', 'lunch', 'dinner', 'snack'] as const

function MacroBar({ label, actual, target, color }: { label: string; actual: number; target: number; color: string }) {
  const pct = target > 0 ? Math.min((actual / target) * 100, 100) : 0
  const over = target > 0 && actual > target
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="w-12 text-gray-500 shrink-0">{label}</span>
      <div className="flex-1 bg-gray-100 rounded-full h-2">
        <div className={`h-2 rounded-full transition-all ${over ? 'bg-red-400' : color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`w-20 text-right font-medium ${over ? 'text-red-500' : 'text-gray-700'}`}>
        {actual}g / {target}g
      </span>
    </div>
  )
}

function MealLogger() {
  const { showToast } = useToast()
  const [logs, setLogs] = useState<MealLog[]>([])
  const [activePlan, setActivePlan] = useState<DietPlan | null>(null)
  const [loading, setLoading] = useState(true)
  const [date, setDate] = useState(() => new Date().toISOString().split('T')[0])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ food_name: '', meal_type: 'breakfast' as MealLog['meal_type'], calories: '', protein_g: '', carbs_g: '', fat_g: '', notes: '' })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.diet.listPlans()
      .then((plans) => setActivePlan(plans.find((p) => p.is_active) ?? null))
      .catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    api.diet.getMealLogs(date)
      .then(setLogs)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [date])

  async function handleAdd() {
    if (!form.food_name.trim()) return
    setSaving(true)
    try {
      const entry = await api.diet.addMealLog({
        date,
        meal_type: form.meal_type,
        food_name: form.food_name.trim(),
        calories: Number(form.calories) || 0,
        protein_g: Number(form.protein_g) || 0,
        carbs_g: Number(form.carbs_g) || 0,
        fat_g: Number(form.fat_g) || 0,
        notes: form.notes,
      })
      setLogs((l) => [...l, entry])
      setForm({ food_name: '', meal_type: 'breakfast', calories: '', protein_g: '', carbs_g: '', fat_g: '', notes: '' })
      setShowForm(false)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.diet.deleteMealLog(id)
      setLogs((l) => l.filter((m) => m.id !== id))
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  const totals = logs.reduce((acc, m) => ({
    calories: acc.calories + m.calories,
    protein_g: acc.protein_g + m.protein_g,
    carbs_g: acc.carbs_g + m.carbs_g,
    fat_g: acc.fat_g + m.fat_g,
  }), { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 })

  const grouped = MEAL_TYPE_OPTIONS.map((type) => ({
    type,
    entries: logs.filter((l) => l.meal_type === type),
  })).filter((g) => g.entries.length > 0)

  return (
    <div className="p-6 max-w-2xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Meal Log</h1>
          <p className="text-sm text-gray-500 mt-0.5">Track what you actually eat each day</p>
        </div>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
        />
      </div>

      {/* Macro progress vs plan target */}
      {activePlan && (
        <div className="bg-white rounded-2xl border border-gray-100 p-4 mb-5">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Today vs Target</p>
            <span className={`text-sm font-bold ${totals.calories > activePlan.target_calories ? 'text-red-500' : 'text-gray-900'}`}>
              {totals.calories} / {activePlan.target_calories} kcal
            </span>
          </div>
          <div className="space-y-2">
            <MacroBar label="Protein" actual={Math.round(totals.protein_g)} target={activePlan.protein_g} color="bg-emerald-500" />
            <MacroBar label="Carbs" actual={Math.round(totals.carbs_g)} target={activePlan.carbs_g} color="bg-blue-400" />
            <MacroBar label="Fat" actual={Math.round(totals.fat_g)} target={activePlan.fat_g} color="bg-amber-400" />
          </div>
        </div>
      )}

      {/* Log entries */}
      {loading ? (
        <div className="text-sm text-gray-400">Loading...</div>
      ) : (
        <div className="space-y-4 mb-5">
          {grouped.length === 0 && (
            <p className="text-sm text-gray-400 text-center py-8">No meals logged for this day yet.</p>
          )}
          {grouped.map(({ type, entries }) => (
            <div key={type}>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2 flex items-center gap-1">
                {MEAL_ICONS[type] ?? '🍽️'} {type}
              </p>
              <div className="space-y-2">
                {entries.map((m) => (
                  <div key={m.id} className="bg-white rounded-xl border border-gray-100 px-4 py-3 flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate">{m.food_name}</p>
                      <p className="text-xs text-gray-400">
                        {m.calories} kcal · {m.protein_g}g P · {m.carbs_g}g C · {m.fat_g}g F
                      </p>
                    </div>
                    <button onClick={() => handleDelete(m.id)} className="text-gray-300 hover:text-red-400 text-sm transition-colors flex-shrink-0">✕</button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add meal form */}
      {showForm ? (
        <div className="bg-white rounded-2xl border border-gray-100 p-4 space-y-3">
          <div className="flex gap-2">
            <input
              placeholder="Food name"
              value={form.food_name}
              onChange={(e) => setForm((f) => ({ ...f, food_name: e.target.value }))}
              className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
            <select
              value={form.meal_type}
              onChange={(e) => setForm((f) => ({ ...f, meal_type: e.target.value as MealLog['meal_type'] }))}
              className="border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              {MEAL_TYPE_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-4 gap-2">
            {(['calories', 'protein_g', 'carbs_g', 'fat_g'] as const).map((field) => (
              <div key={field}>
                <label className="text-xs text-gray-400 mb-1 block">
                  {field === 'calories' ? 'kcal' : field.replace('_g', '')}
                </label>
                <input
                  type="number"
                  placeholder="0"
                  value={form[field]}
                  onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                  className="w-full border border-gray-200 rounded-xl px-2 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
            ))}
          </div>
          <div className="flex gap-2">
            <button onClick={() => setShowForm(false)} className="flex-1 py-2 rounded-xl text-sm text-gray-500 hover:bg-gray-50 border border-gray-200 transition-colors">Cancel</button>
            <button onClick={handleAdd} disabled={saving || !form.food_name.trim()} className="flex-1 py-2 rounded-xl text-sm bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium transition-colors">
              {saving ? 'Adding...' : 'Add meal'}
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => setShowForm(true)} className="w-full py-3 rounded-2xl text-sm text-emerald-600 font-medium border-2 border-dashed border-emerald-200 hover:border-emerald-400 hover:bg-emerald-50 transition-colors">
          + Log a meal
        </button>
      )}
    </div>
  )
}

// ── Diet Page ────────────────────────────────────────────────────────────────

type DietView = 'plans' | 'generate' | { type: 'detail'; id: string } | 'scanner' | 'log'

export default function Diet() {
  const [view, setView] = useState<DietView>('plans')
  const [refreshKey, setRefreshKey] = useState(0)

  const TAB_ITEMS = [
    { key: 'plans' as const, label: 'My Plans' },
    { key: 'log' as const, label: 'Daily Log' },
    { key: 'scanner' as const, label: 'Food Scanner' },
  ]

  const activeTab = typeof view === 'string' ? (view === 'generate' ? 'plans' : view) : 'plans'

  if (view === 'generate') {
    return (
      <GeneratePlanFlow
        onBack={() => setView('plans')}
        onSaved={() => { setRefreshKey((k) => k + 1); setView('plans') }}
      />
    )
  }

  if (typeof view === 'object' && view.type === 'detail') {
    return <PlanDetailView planId={view.id} onBack={() => setView('plans')} />
  }

  return (
    <div>
      <PageHeader title="Diet" subtitle="Meal plans and nutrition tracking" />
      <div className="border-b border-gray-100 px-6 pt-4 pb-0">
        <div className="flex gap-6">
          {TAB_ITEMS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setView(tab.key)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? 'border-emerald-600 text-emerald-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === 'plans' && (
        <PlanList
          key={refreshKey}
          onGenerate={() => setView('generate')}
          onView={(id) => setView({ type: 'detail', id })}
        />
      )}
      {activeTab === 'log' && <MealLogger />}
      {activeTab === 'scanner' && <FoodScannerView />}
    </div>
  )
}
