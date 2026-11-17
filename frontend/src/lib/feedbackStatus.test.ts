import { describe, expect, it } from 'vitest'

import { statusLine } from './feedbackStatus'

/**
 * What a finished item says it is.
 *
 * 'done' rendered as "Fixed", which anyone would read as live. For twelve
 * days it meant "pushed to staging": production ran Nov 4 code while eleven
 * fixes sat unpromoted, all of them labelled Fixed. The same photo bugs were
 * reported three times because of it.
 */
describe('statusLine', () => {
  it('says Fixed for a fix that went live', () => {
    expect(statusLine({ status: 'done', branch: 'main' }).label).toBe('Fixed')
  })

  it('says On staging for a fix that is not live yet', () => {
    const line = statusLine({ status: 'done', branch: 'staging' })
    expect(line.label).toBe('On staging')
    expect(line.fallback).toMatch(/waiting to go live/i)
  })

  it('never calls an undeployed fix Fixed', () => {
    for (const branch of ['staging', 'fix/whatever', 'release/next']) {
      expect(statusLine({ status: 'done', branch }).label).not.toBe('Fixed')
    }
  })

  it('reserves the solid pill for work you can actually use', () => {
    // Filled-vs-outlined carries the distinction before hue does, so the one
    // state you scan a list for stays the one that is filled.
    expect(statusLine({ status: 'done', branch: 'main' }).tone)
      .toContain('bg-ink-600')
    expect(statusLine({ status: 'done', branch: 'staging' }).tone)
      .not.toContain('bg-ink-600')
  })

  it('leaves older rows alone when the branch was never recorded', () => {
    // Blank predates the automation recording it. Most of those ARE live by
    // now, and relabelling old history as pending is a different kind of lie.
    expect(statusLine({ status: 'done', branch: '' }).label).toBe('Fixed')
    expect(statusLine({ status: 'done' }).label).toBe('Fixed')
  })

  it('does not change the other states', () => {
    expect(statusLine({ status: 'failed', branch: 'staging' }).label)
      .toBe("Couldn't fix")
    expect(statusLine({ status: 'skipped' }).label).toBe('Needs you')
    expect(statusLine({ status: 'in_progress' }).label).toBe('Working on it')
    expect(statusLine({ status: 'new' }).label).toBe('Queued')
  })
})
