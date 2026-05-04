import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Exercise } from '../lib/api'

interface ExerciseDrawerProps {
  exercise: Exercise | null
  onClose: () => void
}

interface GuideData {
  steps: string[]
  muscles: string[]
  tips: string[]
  category: string
  images: string[]
}

export default function ExerciseDrawer({ exercise, onClose }: ExerciseDrawerProps) {
  const [guide, setGuide] = useState<GuideData | null>(null)
  const [guideLoading, setGuideLoading] = useState(false)

  useEffect(() => {
    document.body.style.overflow = exercise ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [exercise])

  useEffect(() => {
    if (!exercise) { setGuide(null); return }
    setGuide(null)
    setGuideLoading(true)
    api.workouts.getExerciseGuide(exercise.name)
      .then(setGuide)
      .catch(() => {})
      .finally(() => setGuideLoading(false))
  }, [exercise?.name])

  if (!exercise) return null

  const ytUrl = `https://www.youtube.com/search?q=${encodeURIComponent(exercise.name + ' exercise form tutorial')}`
  const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(exercise.name + ' exercise how to')}`

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40 animate-fade-in" onClick={onClose} />
      <div className="fixed right-0 top-0 h-full w-80 z-50 bg-white shadow-2xl flex flex-col animate-slide-in">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-100">
          <h2 className="text-lg font-bold text-gray-900 leading-tight">{exercise.name}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-xl leading-none flex-shrink-0 transition-colors mt-0.5"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* Stats */}
          <div className="flex gap-2 mb-5">
            <div className="flex-1 bg-brand-50 rounded-xl px-3 py-2.5 text-center">
              <p className="text-base font-bold text-brand-500">{exercise.sets}</p>
              <p className="text-xs text-gray-500 mt-0.5">Sets</p>
            </div>
            <div className="flex-1 bg-brand-50 rounded-xl px-3 py-2.5 text-center">
              <p className="text-base font-bold text-brand-500">{exercise.reps}</p>
              <p className="text-xs text-gray-500 mt-0.5">Reps</p>
            </div>
            {exercise.rest_seconds != null && (
              <div className="flex-1 bg-brand-50 rounded-xl px-3 py-2.5 text-center">
                <p className="text-base font-bold text-brand-500">{exercise.rest_seconds}s</p>
                <p className="text-xs text-gray-500 mt-0.5">Rest</p>
              </div>
            )}
          </div>

          {/* Notes */}
          {exercise.notes && (
            <div className="mb-5">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Notes</p>
              <p className="text-sm text-gray-600 leading-relaxed bg-gray-50 rounded-xl px-4 py-3">
                {exercise.notes}
              </p>
            </div>
          )}

          {/* How To section */}
          <div className="mb-5">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">How to perform</p>

            {guideLoading ? (
              <div className="space-y-3">
                <div className="flex gap-2">
                  <div className="flex-1 h-36 bg-gray-100 rounded-xl animate-pulse" />
                  <div className="flex-1 h-36 bg-gray-100 rounded-xl animate-pulse" />
                </div>
                <div className="space-y-2 pt-1">
                  {[80, 65, 75, 55].map((w, i) => (
                    <div key={i} className="h-3 bg-gray-100 rounded-full animate-pulse" style={{ width: `${w}%` }} />
                  ))}
                </div>
              </div>
            ) : guide ? (
              <div className="space-y-4">
                {/* Exercise images from Wger */}
                {guide.images.length > 0 && (
                  <div className={`grid gap-2 ${guide.images.length === 1 ? 'grid-cols-1' : 'grid-cols-2'}`}>
                    {guide.images.map((url, i) => (
                      <div key={i} className="bg-gray-50 rounded-xl overflow-hidden border border-gray-100 flex items-center justify-center">
                        <img
                          src={url}
                          alt={`${exercise.name} demonstration ${i + 1}`}
                          className="w-full h-auto object-contain max-h-40"
                          loading="lazy"
                        />
                      </div>
                    ))}
                  </div>
                )}

                {/* Muscles */}
                {guide.muscles.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-400 mb-1.5">Muscles targeted</p>
                    <div className="flex flex-wrap gap-1.5">
                      {guide.muscles.map((m) => (
                        <span key={m} className="text-xs bg-brand-50 text-brand-600 font-medium px-2.5 py-1 rounded-full">
                          {m}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Steps */}
                <div className="space-y-2.5">
                  {guide.steps.map((step, i) => (
                    <div key={i} className="flex gap-3">
                      <span className="flex-shrink-0 w-5 h-5 rounded-full bg-brand-500 text-white text-xs font-bold flex items-center justify-center mt-0.5">
                        {i + 1}
                      </span>
                      <p className="text-sm text-gray-700 leading-relaxed">{step}</p>
                    </div>
                  ))}
                </div>

                {/* Tips */}
                {guide.tips.length > 0 && (
                  <div className="bg-amber-50 border border-amber-100 rounded-xl px-4 py-3">
                    <p className="text-xs font-semibold text-amber-700 mb-2">Pro tips</p>
                    <ul className="space-y-1.5">
                      {guide.tips.map((tip, i) => (
                        <li key={i} className="flex gap-2 text-xs text-amber-800 leading-relaxed">
                          <span className="flex-shrink-0 mt-0.5">•</span>
                          <span>{tip}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-gray-400 italic">Could not load guide.</p>
            )}
          </div>

          {/* External links */}
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Watch & learn</p>
          <a
            href={ytUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-3 bg-red-50 hover:bg-red-100 border border-red-100 rounded-xl px-4 py-3 mb-2 transition-colors"
          >
            <span className="text-red-600 text-lg">▶</span>
            <div>
              <p className="text-sm font-semibold text-gray-900">Watch on YouTube</p>
              <p className="text-xs text-gray-500">Form tutorial</p>
            </div>
          </a>
          <a
            href={googleUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-3 bg-blue-50 hover:bg-blue-100 border border-blue-100 rounded-xl px-4 py-3 transition-colors"
          >
            <span className="text-blue-600 text-lg font-bold">G</span>
            <div>
              <p className="text-sm font-semibold text-gray-900">Search on Google</p>
              <p className="text-xs text-gray-500">More examples</p>
            </div>
          </a>
        </div>
      </div>
    </>
  )
}
