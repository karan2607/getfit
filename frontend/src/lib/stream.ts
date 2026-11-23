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
  /** The agent hit its round ceiling, so the answer is partial by design. */
  stopped_early?: boolean
  /** The model ran out of tokens; the reply stops mid-thought. */
  truncated?: boolean
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
 * The connection went quiet for IDLE_TIMEOUT_MS.
 *
 * Its own error type because the idle timeout used to abort the SAME
 * controller the Stop button uses, which made a hung server indistinguishable
 * from the user cancelling -- so someone who pressed nothing was told "Reply
 * stopped", blamed for something they did not do and given no reason to
 * retry.
 */
export class StreamIdleError extends Error {
  constructor() {
    super('The reply stopped arriving.')
    this.name = 'StreamIdleError'
  }
}

/** The stream ended without a `done` or `error` frame. */
export class StreamTruncatedError extends Error {
  constructor() {
    super('The reply ended early.')
    this.name = 'StreamTruncatedError'
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

  // Which of the two aborted, so the caller can tell the user the truth.
  let idledOut = false
  let idleTimer: ReturnType<typeof setTimeout> | undefined
  const resetIdle = () => {
    clearTimeout(idleTimer)
    idleTimer = setTimeout(() => {
      idledOut = true
      controller.abort()
    }, IDLE_TIMEOUT_MS)
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

    let terminated = false
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
          if (payload.done || payload.error) terminated = true
          yield payload
          if (payload.done) return
        } catch {
          // A malformed SSE line is skipped rather than failing the turn:
          // the frames around it are still usable text.
        }
      }
    }

    // The socket closed with no terminal frame: a cut proxy, a killed
    // worker, or a `done` line that failed to parse. This used to just fall
    // out of the loop, so the reply LOOKED complete, was never appended to
    // the transcript, and vanished on reload -- while a stale pendingTurn
    // record popped a bogus "interrupted" banner on the next mount.
    if (!terminated) throw new StreamTruncatedError()
  } catch (err) {
    // An abort we caused ourselves is not the user stopping the reply.
    if (idledOut) throw new StreamIdleError()
    throw err
  } finally {
    clearTimeout(idleTimer)
    signal?.removeEventListener('abort', onAbort)
  }
}
