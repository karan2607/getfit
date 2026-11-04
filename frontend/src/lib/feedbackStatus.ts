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
  /** Tailwind classes for the pill. Accent, never emerald: the rebrand moved
   *  done-states onto the orange token and the palette guard enforces it. */
  tone: string
  /** One line under the issue, when the agent left no summary of its own. */
  fallback: string
}

const LINES: Record<string, StatusLine> = {
  done: {
    label: 'Fixed',
    tone: 'text-accent-700 bg-accent-50 border-accent-200',
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
