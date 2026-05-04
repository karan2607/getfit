import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Exercise } from '../lib/api'

interface ExerciseDrawerProps {
  exercise: Exercise | null
  onClose: () => void
}

// --- Stick-figure illustration system ---

interface Pose {
  headCy: number
  path: string
}

const STANDING: Pose = {
  headCy: 10,
  path: 'M35,18 L35,62 M20,28 L50,28 M20,28 L16,46 L17,57 M50,28 L54,46 L53,57 M24,62 L46,62 M24,62 L20,84 L21,94 M46,62 L50,84 L49,94',
}

const POSES: Record<string, [Pose, Pose]> = {
  squat: [
    STANDING,
    {
      headCy: 16,
      path: 'M35,24 L32,55 M16,34 L54,34 M16,34 L4,50 L2,60 M54,34 L68,50 L70,60 M12,57 L56,57 M12,57 L6,75 L12,93 M56,57 L62,75 L56,93',
    },
  ],
  press: [
    {
      headCy: 10,
      path: 'M35,18 L35,62 M20,28 L50,28 M20,28 L8,38 L14,22 M50,28 L62,38 L56,22 M11,20 L59,20 M24,62 L46,62 M24,62 L20,84 L21,94 M46,62 L50,84 L49,94',
    },
    {
      headCy: 10,
      path: 'M35,18 L35,62 M20,28 L50,28 M20,28 L14,2 M50,28 L56,2 M10,0 L60,0 M24,62 L46,62 M24,62 L20,84 L21,94 M46,62 L50,84 L49,94',
    },
  ],
  pull: [
    {
      headCy: 22,
      path: 'M35,30 L35,70 M18,40 L52,40 M18,40 L14,8 M52,40 L56,8 M10,5 L60,5 M26,70 L44,70 M26,70 L22,88 L23,94 M44,70 L48,88 L47,94',
    },
    {
      headCy: 12,
      path: 'M35,20 L35,64 M18,30 L52,30 M18,30 L14,12 L18,4 M52,30 L56,12 L52,4 M12,2 L58,2 M26,64 L44,64 M26,64 L24,80 L25,93 M44,64 L46,80 L47,93',
    },
  ],
  curl: [
    {
      headCy: 10,
      path: 'M35,18 L35,62 M20,28 L50,28 M20,28 L18,50 L18,62 M50,28 L52,50 L52,62 M24,62 L46,62 M24,62 L20,84 L21,94 M46,62 L50,84 L49,94',
    },
    {
      headCy: 10,
      path: 'M35,18 L35,62 M20,28 L50,28 M20,28 L18,50 L24,36 M50,28 L52,50 L46,36 M24,62 L46,62 M24,62 L20,84 L21,94 M46,62 L50,84 L49,94',
    },
  ],
  push: [
    {
      headCy: 10,
      path: 'M35,18 L35,60 M14,28 L56,28 M14,28 L4,40 L2,52 M56,28 L66,40 L68,52 M24,60 L46,60 M24,60 L20,82 L21,93 M46,60 L50,82 L49,93',
    },
    {
      headCy: 10,
      path: 'M35,18 L35,60 M14,28 L56,28 M14,28 L8,44 L22,44 M56,28 L62,44 L48,44 M22,44 L48,44 M24,60 L46,60 M24,60 L20,82 L21,93 M46,60 L50,82 L49,93',
    },
  ],
  row: [
    {
      headCy: 10,
      path: 'M35,18 L35,60 M20,28 L50,28 M20,28 L16,46 L4,52 M50,28 L54,46 L66,52 M24,60 L46,60 M24,60 L20,82 L21,93 M46,60 L50,82 L49,93',
    },
    {
      headCy: 10,
      path: 'M35,18 L35,60 M20,28 L50,28 M20,28 L8,38 L22,48 M50,28 L62,38 L48,48 M24,60 L46,60 M24,60 L20,82 L21,93 M46,60 L50,82 L49,93',
    },
  ],
  hinge: [
    STANDING,
    {
      headCy: 20,
      path: 'M35,28 L30,60 M18,38 L52,36 M18,38 L10,54 L14,64 M52,36 L56,52 L52,62 M22,60 L44,62 M22,60 L18,82 L19,93 M44,62 L48,82 L47,93',
    },
  ],
  lunge: [
    STANDING,
    {
      headCy: 10,
      path: 'M35,18 L35,60 M20,28 L50,28 M20,28 L16,46 L17,57 M50,28 L54,46 L53,57 M26,60 L44,60 M26,60 L22,76 L24,93 M44,60 L52,76 L62,90',
    },
  ],
  core: [
    STANDING,
    {
      headCy: 32,
      path: 'M35,40 L30,60 M18,48 L52,46 M18,48 L12,60 L24,58 M52,46 L58,58 L46,60 M22,62 L46,64 M22,62 L18,80 L26,92 M46,64 L50,80 L42,92',
    },
  ],
  cardio: [
    STANDING,
    {
      headCy: 10,
      path: 'M35,18 L36,60 M22,28 L50,28 M22,28 L10,42 L14,54 M50,28 L58,44 L54,56 M26,62 L44,60 M26,62 L16,80 L20,93 M44,60 L54,76 L56,90',
    },
  ],
}

const DEFAULT_POSES = [STANDING, { headCy: 10, path: 'M35,18 L35,62 M20,28 L50,28 M20,28 L10,44 L14,56 M50,28 L60,44 L56,56 M24,62 L46,62 M24,62 L20,84 L21,94 M46,62 L50,84 L49,94' }] as [Pose, Pose]

function StickFigure({ pose, offsetX = 0 }: { pose: Pose; offsetX?: number }) {
  return (
    <g transform={`translate(${offsetX}, 0)`} stroke="#374151" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none">
      <circle cx={35} cy={pose.headCy} r={8} fill="#f3f4f6" stroke="#374151" strokeWidth="2.5" />
      <path d={pose.path} />
    </g>
  )
}

function ExerciseIllustration({ category }: { category: string }) {
  const [start, end] = POSES[category] ?? DEFAULT_POSES
  return (
    <svg viewBox="0 0 175 100" className="w-full h-24" aria-hidden="true">
      {/* left pose */}
      <StickFigure pose={start} offsetX={0} />
      {/* arrow */}
      <text x="87" y="54" textAnchor="middle" fontSize="18" fill="#9ca3af">→</text>
      {/* right pose */}
      <StickFigure pose={end} offsetX={100} />
      {/* labels */}
      <text x="35" y="100" textAnchor="middle" fontSize="9" fill="#9ca3af" fontFamily="system-ui">Start</text>
      <text x="135" y="100" textAnchor="middle" fontSize="9" fill="#9ca3af" fontFamily="system-ui">End</text>
    </svg>
  )
}

// --- Main component ---

interface GuideData {
  steps: string[]
  muscles: string[]
  tips: string[]
  category: string
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
                <div className="h-20 bg-gray-100 rounded-xl animate-pulse" />
                <div className="space-y-2">
                  {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="h-3 bg-gray-100 rounded-full animate-pulse" style={{ width: `${70 + i * 5}%` }} />
                  ))}
                </div>
              </div>
            ) : guide ? (
              <div className="space-y-4">
                {/* Illustration */}
                <div className="bg-gray-50 rounded-xl px-3 py-3">
                  <ExerciseIllustration category={guide.category} />
                </div>

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
                <div className="space-y-2">
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
                          <span className="flex-shrink-0">•</span>
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
