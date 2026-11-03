import { describe, it, expect, beforeEach } from 'vitest'
import * as pendingTurn from './pendingTurn'

describe('pendingTurn', () => {
  beforeEach(() => localStorage.clear())

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
