import type { FailedTurn } from '../../hooks/useChatSession'

/**
 * What is happening to the turn in flight, or what went wrong with the last
 * one.
 *
 * Replaces a dots-only indicator and a transient red banner. Both were
 * dishonest in their own way: the dots said nothing about a request that had
 * been going for forty seconds, and the banner showed `AI stream failed (402)`
 * then vanished on reload, so a failure left no trace at all.
 */

/** Elapsed-time copy, staged so a long wait explains itself. */
function waitingCopy(seconds: number) {
  if (seconds < 10) return `Thinking… ${seconds}s`
  if (seconds < 25) return `Still working… ${seconds}s`
  // Render's free tier cold-starts in ~45s. Saying so beats looking frozen.
  return `Waking up the server — this can take up to ~45s on the free tier. ${seconds}s`
}

export function InFlight({ elapsed, onStop }: { elapsed: number; onStop: () => void }) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-3">
        <span className="inline-flex gap-1" aria-hidden>
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-pulse"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </span>
        <span className="text-sm text-gray-500" role="status" aria-live="polite">
          {waitingCopy(elapsed)}
        </span>
        <button
          type="button"
          onClick={onStop}
          className="ml-auto min-h-[44px] px-4 text-sm font-medium text-red-600 hover:text-red-700"
        >
          Stop
        </button>
      </div>
      <p className="text-xs text-gray-400 mt-1">
        Replying — input returns when done, or tap Stop.
      </p>
    </div>
  )
}

export function TurnFailure({
  turn,
  onRetry,
  onDismiss,
}: {
  turn: FailedTurn
  onRetry: () => void
  onDismiss: () => void
}) {
  return (
    <div className="mb-4 rounded-xl border border-accent-200 bg-accent-50 px-4 py-3">
      <p className="text-sm font-medium text-ink-600">
        {turn.stopped ? 'Reply stopped' : 'AI is unavailable'}
      </p>
      <p className="text-sm text-ink-500 mt-1">{turn.error.message}</p>

      {/* The server persisted this text; throwing it away client-side was the
          old behaviour and left the user with nothing to read. */}
      {turn.partial && (
        <p className="text-xs text-gray-500 mt-2">
          Showing what arrived before it {turn.stopped ? 'stopped' : 'failed'}.
        </p>
      )}

      <div className="flex gap-2 mt-3">
        {turn.error.retryable && (
          <button
            type="button"
            onClick={onRetry}
            className="min-h-[44px] px-4 rounded-lg bg-accent-500 hover:bg-accent-600 text-white text-sm font-medium"
          >
            Retry
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="min-h-[44px] px-4 rounded-lg border border-gray-300 text-sm font-medium text-gray-700"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
