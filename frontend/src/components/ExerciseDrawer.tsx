import { useEffect } from 'react'
import type { Exercise } from '../lib/api'

interface ExerciseDrawerProps {
  exercise: Exercise | null
  onClose: () => void
}

export default function ExerciseDrawer({ exercise, onClose }: ExerciseDrawerProps) {
  useEffect(() => {
    document.body.style.overflow = exercise ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [exercise])

  if (!exercise) return null

  const ytUrl = `https://www.youtube.com/search?q=${encodeURIComponent(exercise.name + ' exercise form tutorial')}`
  const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(exercise.name + ' exercise how to')}`

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/40 animate-fade-in"
        onClick={onClose}
      />
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

          {/* Links */}
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Learn this exercise</p>
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
              <p className="text-xs text-gray-500">How to perform</p>
            </div>
          </a>
        </div>
      </div>
    </>
  )
}
