const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

/**
 * How long to wait with no bytes arriving before giving up.
 *
 * Render's free tier cold-starts in ~45s, so a short timeout would abort a
 * request that was about to succeed. This is an IDLE timeout rather than a
 * total one: a long reply that keeps streaming is healthy, a socket that has
 * gone quiet for 90s is not.
 */
const IDLE_TIMEOUT_MS = 90_000

export interface StreamEvent {
  chunk?: string
  /**
   * What the AI is doing right now, e.g. "Reading week 4 of your plan".
   *
   * Its own frame rather than a text chunk, because progress is not part of
   * the answer and must not be saved as something the AI said.
   */
  status?: string
  done?: boolean
  message_id?: string
  error?: string
  tool_calls?: Array<{ tool: string; arguments: unknown; result_summary: string }>
  proposed_change?: unknown
  applied_change?: unknown
  verification_diff?: unknown
  needs_confirmation?: boolean
}

export class StreamHttpError extends Error {
  status: number
  constructor(status: number) {
    super(`Stream request failed (${status})`)
    this.name = 'StreamHttpError'
    this.status = status
  }
}

/**
 * Stream one chat turn.
 *
 * `signal` is how Stop works: the caller aborts, the fetch tears down, and the
 * generator throws AbortError. Before this there was NO way to cancel — the
 * input sat disabled until the server replied or the socket died, with no
 * control offered anywhere.
 */
export async function* streamChatMessage(
  sessionId: string,
  content: string,
  token: string,
  extra?: Record<string, unknown>,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  // Idle timeout and caller cancellation share one signal, so the fetch only
  // needs to know about one.
  const controller = new AbortController()
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })

  let idleTimer: ReturnType<typeof setTimeout> | undefined
  const resetIdle = () => {
    clearTimeout(idleTimer)
    idleTimer = setTimeout(() => controller.abort(), IDLE_TIMEOUT_MS)
  }

  try {
    resetIdle()
    const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}/messages/`, {
      method: 'POST',
      headers: {
        Authorization: `Token ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ content, ...extra }),
      signal: controller.signal,
    })

    if (!res.ok || !res.body) {
      // A typed error, so the mapping layer can read the status instead of
      // scraping it back out of a message string.
      throw new StreamHttpError(res.status)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      resetIdle()
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const payload = JSON.parse(line.slice(6)) as StreamEvent
          yield payload
          if (payload.done) return
        } catch {
          // A malformed SSE line is skipped rather than failing the turn:
          // the frames around it are still usable text.
        }
      }
    }
  } finally {
    clearTimeout(idleTimer)
    signal?.removeEventListener('abort', onAbort)
  }
}
