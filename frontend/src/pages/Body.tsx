import { useState, useEffect, useRef } from 'react'
import { api, type BodyScanResult } from '../lib/api'
import { useToast } from '../components/Toast'
import { getErrorMessage } from '../lib/errors'

function BodyScanCard({ scan }: { scan: BodyScanResult }) {
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <p className="font-bold text-gray-900">{scan.physique_category}</p>
          <p className="text-xs text-gray-400">{new Date(scan.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</p>
        </div>
        {scan.body_fat_pct != null && (
          <div className="text-right">
            <p className="text-2xl font-bold text-emerald-600">{scan.body_fat_pct}%</p>
            <p className="text-xs text-gray-400">body fat</p>
          </div>
        )}
      </div>

      {scan.muscle_mass_note && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Muscle Mass</p>
          <p className="text-sm text-gray-700">{scan.muscle_mass_note}</p>
        </div>
      )}

      {scan.posture_notes && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Posture</p>
          <p className="text-sm text-gray-700">{scan.posture_notes}</p>
        </div>
      )}

      {scan.recommendations && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Recommendations</p>
          <p className="text-sm text-gray-700">{scan.recommendations}</p>
        </div>
      )}

      {scan.disclaimer && (
        <p className="text-xs text-gray-400 border-t border-gray-100 pt-3 mt-3 italic">{scan.disclaimer}</p>
      )}
    </div>
  )
}

export default function Body() {
  const { showToast } = useToast()
  const [scanning, setScanning] = useState(false)
  const [history, setHistory] = useState<BodyScanResult[]>([])
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [showDisclaimer, setShowDisclaimer] = useState(true)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.body.history()
      .then(setHistory)
      .finally(() => setLoadingHistory(false))
  }, [])

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setScanning(true)
    try {
      const result = await api.body.scan(file)
      setHistory((h) => [result, ...h])
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    } finally {
      setScanning(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Body Scanner</h1>
      <p className="text-sm text-gray-500 mb-6">Upload a photo for an AI-powered physique assessment</p>

      {showDisclaimer && (
        <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-5 flex items-start gap-3">
          <span className="text-xl flex-shrink-0">⚠️</span>
          <div className="flex-1">
            <p className="text-sm font-semibold text-amber-800 mb-1">AI Estimate Only</p>
            <p className="text-xs text-amber-700">
              This tool provides an AI-based estimate for informational purposes only.
              It is not a medical assessment. Consult a healthcare professional for accurate body composition analysis.
            </p>
          </div>
          <button
            onClick={() => setShowDisclaimer(false)}
            className="text-amber-400 hover:text-amber-600 flex-shrink-0 text-lg leading-none"
          >
            ×
          </button>
        </div>
      )}

      <div
        onClick={() => !scanning && fileRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-8 text-center cursor-pointer transition-colors mb-6 ${
          scanning ? 'border-emerald-300 bg-emerald-50 cursor-wait' : 'border-gray-200 hover:border-emerald-300 hover:bg-emerald-50/50'
        }`}
      >
        {scanning ? (
          <>
            <div className="text-3xl mb-2 animate-pulse">🔬</div>
            <p className="text-sm font-medium text-emerald-600">Analyzing your physique...</p>
            <p className="text-xs text-gray-400 mt-1">This may take a moment</p>
          </>
        ) : (
          <>
            <div className="text-3xl mb-2">📷</div>
            <p className="text-sm font-medium text-gray-700 mb-1">Upload a full-body photo</p>
            <p className="text-xs text-gray-400">Front or side view works best · Click to select</p>
          </>
        )}
        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleFileChange} />
      </div>

      {loadingHistory ? (
        <div className="text-sm text-gray-400">Loading history...</div>
      ) : history.length === 0 ? (
        <div className="text-center py-10 text-gray-400">
          <div className="text-3xl mb-2">💪</div>
          <p className="text-sm text-gray-500">No scans yet. Upload your first photo above.</p>
        </div>
      ) : (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Scan History</p>
          <div className="space-y-4">
            {history.map((scan) => (
              <BodyScanCard key={scan.id} scan={scan} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
