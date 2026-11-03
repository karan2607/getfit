import type { FailedTurn } from '../hooks/useChatSession'

/*
 * The turn in flight, kept outside React.
 *
 * iOS Safari freezes and often discards a backgrounded tab. Coming back
 * reloads the page, and everything the turn knew -- the question, whatever
 * had streamed, the fact that it was interrupted at all -- lived only in
 * component state, so it was simply gone. You returned to your own message
 * with no reply and no error, and had to type it again.
 *
 * So the turn is written down when it starts, updated as text arrives, and
 * cleared when it completes. Anything still recorded on mount was, by
 * definition, interrupted: the only way to leave a record behind is to not
 * reach the end.
 *
 * This does not keep the SERVER generating -- it stops iterating the
 * generator when the client goes away, so a long turn still dies with the
 * tab. It keeps the user's words and the partial reply, which is what made
 * the interruption cost something.
 */
const KEY = 'getfit.pendingTurn.'

type Stored = Omit<FailedTurn, 'error'> & { interrupted?: true }

function key(sessionId: string) {
  return KEY + sessionId
}

export function beginTurn(sessionId: string, content: string, extra?: Record<string, unknown>) {
  write(sessionId, { content, extra, partial: '', stopped: false, interrupted: true })
}

export function updatePartial(sessionId: string, partial: string) {
  const current = read(sessionId)
  if (!current) return
  write(sessionId, { ...current, partial })
}

export function endTurn(sessionId: string) {
  try {
    localStorage.removeItem(key(sessionId))
  } catch {
    // Private mode and blocked site data both throw. Losing the record is
    // the old behaviour, not a new failure.
  }
}

export function read(sessionId: string): Stored | null {
  try {
    const raw = localStorage.getItem(key(sessionId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Stored
    return parsed && typeof parsed.content === 'string' ? parsed : null
  } catch {
    return null
  }
}

function write(sessionId: string, value: Stored) {
  try {
    localStorage.setItem(key(sessionId), JSON.stringify(value))
  } catch {
    // Quota or blocked storage: the feature degrades to what it was.
  }
}
