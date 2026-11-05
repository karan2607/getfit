import { describe, it, expect } from 'vitest'
import { shouldPromptNextWeek, type PromptDay } from './nextWeekPrompt'

const done: PromptDay = { is_rest_day: false, last_completed_at: '2026-11-01T10:00:00Z' }
const todo: PromptDay = { is_rest_day: false, last_completed_at: null }
const rest: PromptDay = { is_rest_day: true, last_completed_at: null }

const base = { currentWeek: 5, maxWeek: 8, nextWeekReady: false, isPreparing: false }

describe('shouldPromptNextWeek', () => {
  it('does not offer to prepare with one workout still to go', () => {
    // The reported bug: the banner appeared with one day left, and pressing
    // it built week 6 without the last week-5 workout.
    expect(shouldPromptNextWeek({ ...base, days: [done, done, rest, todo] })).toBe(false)
  })

  it('prompts once every training day is done, ignoring rest days', () => {
    expect(shouldPromptNextWeek({ ...base, days: [done, rest, done, rest] })).toBe(true)
  })

  it('still reports a week that is already ready', () => {
    expect(shouldPromptNextWeek({ ...base, nextWeekReady: true, days: [done, todo] })).toBe(true)
  })

  it('stays hidden while preparing, in the last week, or with no training days', () => {
    expect(shouldPromptNextWeek({ ...base, isPreparing: true, days: [done] })).toBe(false)
    expect(shouldPromptNextWeek({ ...base, currentWeek: 8, days: [done] })).toBe(false)
    expect(shouldPromptNextWeek({ ...base, days: [rest, rest] })).toBe(false)
  })
})
