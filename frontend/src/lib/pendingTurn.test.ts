import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as pendingTurn from './pendingTurn'

describe('pendingTurn', () => {
  beforeEach(() => {
    vi.restoreAllMocks()   // the clock mock below must not leak
    localStorage.clear()
  })

  it('keeps an interrupted turn and its partial reply', () => {
    pendingTurn.beginTurn('s1', 'change my bench press')
    pendingTurn.updatePartial('s1', 'Looking at week 1')
    const stored = pendingTurn.read('s1')
    expect(stored?.content).toBe('change my bench press')
    expect(stored?.partial).toBe('Looking at week 1')
  })

  it('leaves nothing behind once the turn completes', () => {
    pendingTurn.beginTurn('s1', 'hello')
    pendingTurn.endTurn('s1')
    expect(pendingTurn.read('s1')).toBeNull()
  })

  it('keeps sessions apart', () => {
    pendingTurn.beginTurn('s1', 'one')
    pendingTurn.beginTurn('s2', 'two')
    pendingTurn.endTurn('s1')
    expect(pendingTurn.read('s1')).toBeNull()
    expect(pendingTurn.read('s2')?.content).toBe('two')
  })

  it('ignores a partial for a turn that is no longer in flight', () => {
    pendingTurn.updatePartial('s1', 'orphan')
    expect(pendingTurn.read('s1')).toBeNull()
  })

  it('survives unparseable storage rather than throwing', () => {
    localStorage.setItem('getfit.pendingTurn.s1', '{not json')
    expect(pendingTurn.read('s1')).toBeNull()
  })
})

/**
 * These records used to accumulate for ever.
 *
 * One key per session, no expiry, and the drawer minted a new session on
 * every open — so an interrupted drawer turn left a key nothing could ever
 * reach again. Enough of them and `setItem` starts failing on quota, which
 * it does silently by design, and the recovery feature quietly stops
 * working on the chat page too.
 */
describe('pendingTurn housekeeping', () => {
  const DAY = 24 * 60 * 60 * 1000

  beforeEach(() => localStorage.clear())

  it('forgets a turn nobody came back for', () => {
    pendingTurn.beginTurn('old', 'a question from last week')
    pendingTurn.sweep(Date.now() + 2 * DAY)
    expect(pendingTurn.read('old')).toBeNull()
  })

  it('keeps one from an hour ago', () => {
    pendingTurn.beginTurn('recent', 'put the phone away mid-reply')
    pendingTurn.sweep(Date.now() + 60 * 60 * 1000)
    expect(pendingTurn.read('recent')?.content)
      .toBe('put the phone away mid-reply')
  })

  it('caps how many are kept, newest first', () => {
    // A distinct time per write. Without this all 25 land in the same
    // millisecond, the sort has nothing to order by, and the assertion
    // about WHICH one survives passes or fails by luck.
    let clock = 1_700_000_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => (clock += 1000))
    for (let i = 0; i < 25; i += 1) pendingTurn.beginTurn(`s${i}`, `q${i}`)
    const left = Object.keys(localStorage)
      .filter((k) => k.startsWith('getfit.pendingTurn.'))
    expect(left.length).toBeLessThanOrEqual(20)
    // The most recent survives; the first one in does not.
    expect(pendingTurn.read('s24')).not.toBeNull()
    expect(pendingTurn.read('s0')).toBeNull()
  })

  it('drops a record it cannot read rather than keeping it for ever', () => {
    localStorage.setItem('getfit.pendingTurn.broken', '{not json')
    pendingTurn.sweep()
    expect(localStorage.getItem('getfit.pendingTurn.broken')).toBeNull()
  })

  it('sweeps when a new turn starts, without being called', () => {
    pendingTurn.beginTurn('stale', 'old one')
    const raw = JSON.parse(localStorage.getItem('getfit.pendingTurn.stale')!)
    localStorage.setItem('getfit.pendingTurn.stale',
      JSON.stringify({ ...raw, at: Date.now() - 2 * DAY }))

    pendingTurn.beginTurn('fresh', 'new one')

    expect(pendingTurn.read('stale')).toBeNull()
    expect(pendingTurn.read('fresh')).not.toBeNull()
  })
})
