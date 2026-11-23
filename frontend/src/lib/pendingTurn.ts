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

type Stored = Omit<FailedTurn, 'error'> & {
  interrupted?: true
  /** When the turn began, so stale records can be swept. */
  at?: number
}

function key(sessionId: string) {
  return KEY + sessionId
}

/**
 * How long an interrupted turn is worth offering back.
 *
 * Long enough to survive a phone being put away mid-reply and picked up
 * later; short enough that it is still recognisably the conversation you
 * were having, rather than a question from last month restored without
 * explanation.
 */
const MAX_AGE_MS = 24 * 60 * 60 * 1000

export function beginTurn(sessionId: string, content: string, extra?: Record<string, unknown>) {
  sweep()
  write(sessionId, {
    content, extra, partial: '', stopped: false, interrupted: true,
    at: Date.now(),
  })
}

/**
 * Drop records that are too old, or too many.
 *
 * Nothing ever removed these. One key per session with no expiry, and the
 * drawer created a NEW session every time it opened, so an interrupted
 * drawer turn left a key that nothing could ever reach again. Enough of
 * them and `setItem` starts throwing on quota -- silently, by design -- at
 * which point the recovery feature stops working everywhere, including the
 * chat page where it does get used.
 */
export function sweep(now: number = Date.now()): void {
  try {
    const mine: Array<{ key: string; at: number }> = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i)
      if (!k || !k.startsWith(KEY)) continue
      let at = 0
      try {
        at = (JSON.parse(localStorage.getItem(k) || '{}') as Stored).at ?? 0
      } catch {
        at = 0            // unparseable: treat as ancient and drop it
      }
      mine.push({ key: k, at })
    }
    const doomed = mine.filter((r) => now - r.at > MAX_AGE_MS)
    // A hard ceiling as well as an age limit: a burst inside the window
    // should not be able to fill storage either.
    const survivors = mine
      .filter((r) => !doomed.includes(r))
      .sort((a, b) => b.at - a.at)
      .slice(MAX_RECORDS)
    for (const r of [...doomed, ...survivors]) {
      localStorage.removeItem(r.key)
    }
  } catch {
    // Blocked storage. Nothing to sweep and nothing to report.
  }
}

/** At most this many interrupted turns are remembered at once. */
const MAX_RECORDS = 20

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
