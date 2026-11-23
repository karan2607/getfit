import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import BackToWorkout from './BackToWorkout'
import { forgetActiveWorkout, rememberActiveWorkout } from '../lib/activeWorkout'

/**
 * Leaving a workout mid-session for Health meant Workouts -> plan -> Continue
 * to get back, with a load at every step. These pin the one-tap way back.
 */

const mockAuth = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockAuth() }))

function show(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <BackToWorkout />
      <Routes>
        <Route path="/workouts/session/:id" element={<p>session page</p>} />
        <Route path="*" element={<p>other page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BackToWorkout', () => {
  beforeEach(() => {
    localStorage.clear()
    mockAuth.mockReturnValue({ user: { id: 'u1' } })
  })

  it('offers a one-tap way back to the open workout from another page', () => {
    rememberActiveWorkout({ userId: 'u1', sessionId: 's1', dayName: 'Push Day' })
    show('/health')

    fireEvent.click(screen.getByRole('link', { name: /back to workout · push day/i }))
    expect(screen.getByText('session page')).toBeInTheDocument()
    // And it gets out of the way once you are there.
    expect(screen.queryByRole('link', { name: /back to workout/i })).toBeNull()
  })

  it('shows nothing with no workout open', () => {
    show('/health')
    expect(screen.queryByRole('link', { name: /back to workout/i })).toBeNull()
  })

  it('disappears once the workout is completed', () => {
    rememberActiveWorkout({ userId: 'u1', sessionId: 's1', dayName: 'Push Day' })
    forgetActiveWorkout('s1')
    show('/health')
    expect(screen.queryByRole('link', { name: /back to workout/i })).toBeNull()
  })

  it('finishing a different session leaves the open one alone', () => {
    rememberActiveWorkout({ userId: 'u1', sessionId: 's1', dayName: 'Push Day' })
    forgetActiveWorkout('old')
    show('/health')
    expect(screen.getByRole('link', { name: /back to workout/i })).toBeInTheDocument()
  })

  it('never offers one user another user\'s workout on a shared device', () => {
    rememberActiveWorkout({ userId: 'someone-else', sessionId: 's1', dayName: 'Push Day' })
    show('/health')
    expect(screen.queryByRole('link', { name: /back to workout/i })).toBeNull()
  })
})
