import type { Feedback } from './api'

/**
 * How a feedback item's state reads to the person who filed it.
 *
 * The panel used to show the raw status word -- "done", "skipped" -- next to
 * the agent's internal notes, which told you a database value rather than
 * whether your problem was fixed. "skipped" in particular means the agent
 * refused and is waiting on you, which is close to the opposite of what the
 * word suggests.
 */

export interface StatusLine {
  label: string
  /**
   * Tailwind classes for the pill.
   *
   * Never emerald -- the palette is navy and orange and the guard enforces
   * it. But 'Fixed' on accent-50 and "Couldn't fix" on red-50 are adjacent
   * hues on a 60px pill, and at a glance they read as the same state. The
   * one state you most want to pick out of a list was the hardest to.
   *
   * So Fixed is the only SOLID pill: deep navy against four pale ones. The
   * distinction is filled-vs-outlined before it is hue at all, which also
   * survives being looked at in sunlight or by someone red-green colour
   * blind.
   */
  tone: string
  /** One line under the issue, when the agent left no summary of its own. */
  fallback: string
}

const LINES: Record<string, StatusLine> = {
  done: {
    label: 'Fixed',
    tone: 'text-white bg-ink-600 border-ink-600',
    fallback: 'Fixed and shipped.',
  },
  failed: {
    label: "Couldn't fix",
    tone: 'text-red-700 bg-red-50 border-red-200',
    fallback: 'The change did not hold. Nothing was shipped.',
  },
  skipped: {
    label: 'Needs you',
    tone: 'text-amber-700 bg-amber-50 border-amber-200',
    fallback: 'Stopped and left this one for you to decide.',
  },
  in_progress: {
    label: 'Working on it',
    tone: 'text-blue-700 bg-blue-50 border-blue-200',
    fallback: 'Being worked on now.',
  },
  new: {
    label: 'Queued',
    tone: 'text-gray-600 bg-gray-50 border-gray-200',
    fallback: 'Waiting to be picked up.',
  },
}

export function statusLine(item: Pick<Feedback, 'status'>): StatusLine {
  return LINES[item.status] ?? LINES.new
}
