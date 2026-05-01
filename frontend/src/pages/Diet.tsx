import { useState, useEffect, useRef } from 'react'
import { api, type DietPlan, type DietPlanDetail, type DietPlanPreview, type FoodScanResult } from '../lib/api'
import { useToast } from '../components/Toast'
import { getErrorMessage } from '../lib/errors'

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

// ── Diet Page ────────────────────────────────────────────────────────────────

type DietView = 'plans' | 'generate' | { type: 'detail'; id: string } | 'scanner'

export default function Diet() {
  const [view, setView] = useState<DietView>('plans')
  const [refreshKey, setRefreshKey] = useState(0)

  const TAB_ITEMS = [
    { key: 'plans' as const, label: 'My Plans' },
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
      <div className="border-b border-gray-100 px-6 pt-6 pb-0">
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
      {activeTab === 'scanner' && <FoodScannerView />}
    </div>
  )
}
