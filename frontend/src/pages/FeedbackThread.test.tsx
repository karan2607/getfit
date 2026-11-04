import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import FeedbackThread from './FeedbackThread'
import { api, type Feedback, type FeedbackMessage } from '../lib/api'

const mockUser = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockUser() }))

const showToast = vi.fn()
vi.mock('../components/Toast', () => ({ useToast: () => ({ showToast }) }))

const msg = (over: Partial<FeedbackMessage> = {}): FeedbackMessage => ({
  id: 'm1', role: 'agent', body: 'Anchored the panel to the header edge.',
  seen_at: null, created_at: '2026-11-03T10:00:00Z', ...over,
})

const item = (over: Partial<Feedback> = {}): Feedback => ({
  id: 'f1', body: 'The bell opens the wrong way', page: '/diet',
  status: 'done' as const, result_notes: 'Anchored the panel to the header edge.',
  commit_sha: 'abcdef1234567890', submitted_by: 'admin@test.local',
  created_at: '2026-11-03T09:00:00Z', updated_at: '2026-11-03T10:00:00Z',
  seen_at: null, dismissed_at: null, messages: [msg()], unread: 1, ...over,
})

const show = () =>
  render(
    <MemoryRouter initialEntries={['/feedback/f1']}>
      <Routes>
        <Route path="/feedback/:id" element={<FeedbackThread />} />
      </Routes>
    </MemoryRouter>,
  )

describe('FeedbackThread', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    showToast.mockClear()
    mockUser.mockReturnValue({ user: { is_superuser: true } })
  })

  it('shows nothing useful to an ordinary user', () => {
    mockUser.mockReturnValue({ user: { is_superuser: false } })
    const spy = vi.spyOn(api.feedback, 'thread')
    show()
    expect(screen.getByText('Not available.')).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalled()
  })

  it('reads as one conversation, starting with what you filed', async () => {
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(item())
    show()
    expect(await screen.findByText('The bell opens the wrong way')).toBeInTheDocument()
    expect(screen.getByText('Anchored the panel to the header edge.')).toBeInTheDocument()
  })

  it('says what the state means in plain words', async () => {
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(item({ status: 'skipped' }))
    show()
    expect(await screen.findByText(/Needs you/)).toBeInTheDocument()
  })

  it('answers even when the agent left no summary', async () => {
    // An item that finishes with nothing to say would otherwise end the
    // thread on your own question, which reads as having been ignored.
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(
      item({ status: 'failed', result_notes: '', messages: [] }),
    )
    show()
    expect(await screen.findByText(/did not hold/)).toBeInTheDocument()
  })

  it('sends a reply and shows the agent picking it back up', async () => {
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(item())
    const reply = vi.spyOn(api.feedback, 'reply').mockResolvedValue(
      item({
        status: 'new',
        messages: [msg(), msg({ id: 'm2', role: 'user', body: 'still off on Diet' })],
      }),
    )
    show()
    await screen.findByText('The bell opens the wrong way')
    await userEvent.type(
      screen.getByPlaceholderText(/./),
      'still off on Diet',
    )
    await userEvent.click(screen.getByRole('button', { name: /send/i }))
    await waitFor(() => expect(reply).toHaveBeenCalledWith('f1', 'still off on Diet'))
    expect(await screen.findByText('still off on Diet')).toBeInTheDocument()
    // Reopened: the item is back in the same queue the loop polls.
    expect(await screen.findByText(/polls every few minutes/)).toBeInTheDocument()
  })

  it('keeps your text and says so when sending fails', async () => {
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(item())
    vi.spyOn(api.feedback, 'reply').mockRejectedValue(new Error('offline'))
    show()
    await screen.findByText('The bell opens the wrong way')
    await userEvent.type(screen.getByPlaceholderText(/./), 'again please')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))
    await waitFor(() =>
      expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/Could not send/), 'error'),
    )
  })

  it('surfaces a load failure rather than an empty page', async () => {
    vi.spyOn(api.feedback, 'thread').mockRejectedValue(new Error('offline'))
    show()
    expect(await screen.findByText(/Could not load/)).toBeInTheDocument()
  })
})
