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
  /**
   * Done, but only on staging -- so NOT in front of you yet.
   *
   * 'done' rendered as "Fixed", which anyone would read as live. For twelve
   * days it meant "pushed to staging", and eleven fixes reported as fixed
   * were not deployed; the photo bugs were re-reported because of it. The
   * pill now says where the fix is, and deliberately does NOT get the solid
   * navy treatment -- that one is reserved for work you can actually use.
   */
  done_staging: {
    label: 'On staging',
    tone: 'text-ink-600 bg-accent-50 border-accent-200',
    fallback: 'Fixed and on staging, waiting to go live.',
  },
  failed: {
    label: "Couldn't fix",
    tone: 'text-danger-700 bg-danger-50 border-danger-200',
    fallback: 'The change did not hold. Nothing was shipped.',
  },
  skipped: {
    label: 'Needs you',
    tone: 'text-warning-700 bg-warning-50 border-warning-200',
    fallback: 'Stopped and left this one for you to decide.',
  },
  in_progress: {
    label: 'Working on it',
    tone: 'text-info-700 bg-info-50 border-info-200',
    fallback: 'Being worked on now.',
  },
  new: {
    label: 'Queued',
    tone: 'text-gray-600 bg-gray-50 border-gray-200',
    fallback: 'Waiting to be picked up.',
  },
}

/** The branch a finished fix is live on. Anything else is not in front of you. */
const LIVE_BRANCH = 'main'

export function statusLine(
  item: Pick<Feedback, 'status'> & Partial<Pick<Feedback, 'branch'>>,
): StatusLine {
  // Blank `branch` means the row predates the automation recording it. Those
  // stay "Fixed": most of them ARE live by now, and relabelling old history
  // as pending would be a different kind of lie.
  if (item.status === 'done' && item.branch && item.branch !== LIVE_BRANCH) {
    return LINES.done_staging
  }
  return LINES[item.status] ?? LINES.new
}
