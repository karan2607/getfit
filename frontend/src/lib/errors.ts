import { ApiError } from './api'

/**
 * The original one-liner, kept because five call sites use it for ordinary
 * REST failures where the server already sends a usable `detail`.
 *
 * `toFriendlyError` below is for the chat stream, where the failure arrives
 * as an SSE frame or a torn socket and there is no server-written message to
 * fall back on.
 */
export function getErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Something went wrong.'
}

/**
 * Turning a failure into something a person can act on.
 *
 * The app showed `AI stream failed (402)` to the user. That is an HTTP status
 * code for a billing condition on our side, rendered as if it were the user's
 * problem and with nothing to do about it. A raw code is never an error
 * message: it tells the reader nothing, and it cannot say whether retrying is
 * worth the attempt.
 *
 * So every failure resolves to three things: what happened in plain words,
 * whether a retry could help, and a short kind the UI can branch on.
 */

export type ErrorKind = 'unavailable' | 'timeout' | 'auth' | 'offline' | 'unknown'

export interface FriendlyError {
  kind: ErrorKind
  message: string
  retryable: boolean
}

const BY_STATUS: Record<number, FriendlyError> = {
  401: {
    kind: 'auth',
    message: 'Your session has expired. Sign in again to continue.',
    retryable: false,
  },
  402: {
    kind: 'unavailable',
    // Ours, not theirs. Saying so is the difference between "this app is
    // broken" and "they know, and my message is safe".
    message:
      'The AI is unavailable right now — our AI provider is out of credits, ' +
      'so this is on our side, not yours. Your message is saved.',
    retryable: true,
  },
  429: {
    kind: 'unavailable',
    message: 'The AI is busy right now. Give it a moment and try again.',
    retryable: true,
  },
  503: {
    kind: 'unavailable',
    message: 'The AI is temporarily unavailable. Your message is saved.',
    retryable: true,
  },
}

/** A status code, if one can be found anywhere in this error. */
function statusOf(error: unknown): number | null {
  if (typeof error === 'object' && error !== null) {
    const status = (error as { status?: unknown }).status
    if (typeof status === 'number') return status
  }
  // The server's SSE `error` frame is a STRING, and until the backend sends a
  // structured code the number has to be read back out of it. Narrow on
  // purpose: a bare 3-digit number anywhere would match a rep count.
  const text = String((error as { message?: string })?.message ?? error ?? '')
  const match = text.match(/\((\d{3})\)/) ?? text.match(/\b(4\d{2}|5\d{2})\b/)
  return match ? Number(match[1]) : null
}

export function toFriendlyError(error: unknown): FriendlyError {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return { kind: 'timeout', message: 'Stopped.', retryable: true }
  }
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    return {
      kind: 'offline',
      message: 'You appear to be offline. Your message is saved.',
      retryable: true,
    }
  }

  const status = statusOf(error)
  if (status && BY_STATUS[status]) return BY_STATUS[status]
  if (status && status >= 500) {
    return {
      kind: 'unavailable',
      message: 'Something went wrong on our side. Your message is saved.',
      retryable: true,
    }
  }

  const text = String((error as { message?: string })?.message ?? error ?? '')
  if (/timed? ?out|timeout/i.test(text)) {
    return {
      kind: 'timeout',
      message: "That took too long and didn't finish. Your message is saved.",
      retryable: true,
    }
  }

  return {
    kind: 'unknown',
    // Deliberately not the raw text: it is a stack-trace fragment or a status
    // line often enough that showing it is worse than not.
    message: 'Something went wrong. Your message is saved.',
    retryable: true,
  }
}
