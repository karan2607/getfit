import { describe, expect, it } from 'vitest'
import { StreamHttpError } from './stream'
import { toFriendlyError } from './errors'

/**
 * The app showed `AI stream failed (402)` to the user: an HTTP status for a
 * billing condition on OUR side, rendered as if it were theirs, with nothing
 * to do about it. A raw code is never an error message.
 */
describe('toFriendlyError', () => {
  it('never returns a raw status code', () => {
    for (const status of [401, 402, 429, 500, 503]) {
      const friendly = toFriendlyError(new StreamHttpError(status))
      expect(friendly.message).not.toMatch(/\b\d{3}\b/)
    }
  })

  it('says a 402 is our problem, not the user’s', () => {
    const friendly = toFriendlyError(new StreamHttpError(402))
    expect(friendly.message).toMatch(/our side/i)
    expect(friendly.message).toMatch(/saved/i)
    expect(friendly.retryable).toBe(true)
  })

  it('reads the status back out of the server’s SSE error string', () => {
    // Until the backend sends a structured code, the only signal is the text.
    const friendly = toFriendlyError(new Error('AI stream failed (402)'))
    expect(friendly.kind).toBe('unavailable')
    expect(friendly.message).toMatch(/our side/i)
  })

  it('does not treat an expired session as retryable', () => {
    const friendly = toFriendlyError(new StreamHttpError(401))
    expect(friendly.kind).toBe('auth')
    expect(friendly.retryable).toBe(false)
  })

  it('reports an abort as stopped, not failed', () => {
    const friendly = toFriendlyError(
      new DOMException('aborted', 'AbortError'),
    )
    expect(friendly.message).toBe('Stopped.')
    expect(friendly.kind).toBe('timeout')
  })

  it('falls back without leaking raw text', () => {
    const friendly = toFriendlyError(
      new Error('TypeError: Cannot read properties of undefined'),
    )
    expect(friendly.message).not.toMatch(/TypeError/)
    expect(friendly.message).toMatch(/saved/i)
  })

  it('does not mistake a rep count for a status code', () => {
    const friendly = toFriendlyError(new Error('logged 100 reps'))
    expect(friendly.kind).toBe('unknown')
  })
})
