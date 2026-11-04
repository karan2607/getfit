import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import NotificationBell from './NotificationBell'
import { api, type Feedback, type FeedbackMessage } from '../lib/api'

const mockUser = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockUser() }))

const navigate = vi.fn()
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<typeof import('react-router-dom')>('react-router-dom')),
  useNavigate: () => navigate,
}))

const reply = (over: Partial<FeedbackMessage> = {}): FeedbackMessage => ({
  id: 'm1', role: 'agent', body: 'Anchored the panel to the header edge.',
  seen_at: null, created_at: new Date().toISOString(), ...over,
})

const item = (over: Partial<Feedback> = {}): Feedback => ({
  id: 'f1', body: 'Add a bell for superusers', page: '/profile',
  status: 'done' as const, result_notes: 'Shipped the bell.', commit_sha: 'abcdef1234567890',
  submitted_by: 'admin@test.local', created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(), seen_at: null, dismissed_at: null,
  messages: [], unread: 1, ...over,
})

const show = () => render(<MemoryRouter><NotificationBell /></MemoryRouter>)

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    navigate.mockClear()
    mockUser.mockReturnValue({ user: { is_superuser: true } })
  })

  it('renders nothing for an ordinary user', () => {
    mockUser.mockReturnValue({ user: { is_superuser: false } })
    const spy = vi.spyOn(api.feedback, 'notifications')
    const { container } = show()
    expect(container).toBeEmptyDOMElement()
    // And does not even ask -- the endpoint is superuser-only.
    expect(spy).not.toHaveBeenCalled()
  })

  it('counts unread replies, not threads', async () => {
    // Two waiting messages in one thread is "2", not "1". The badge is about
    // how much there is to read.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 3, items: [item({ unread: 2 }), item({ id: 'f2', unread: 1 })],
    })
    show()
    expect(await screen.findByLabelText('Notifications, 3 unread')).toBeInTheDocument()
  })

  it('shows no badge when nothing is waiting', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 0, items: [item({ unread: 0 })],
    })
    show()
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })

  it('leads with the issue you raised, then what it did', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item({ messages: [reply()] })],
    })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    expect(screen.getByText('Add a bell for superusers')).toBeInTheDocument()
    expect(screen.getByText('Anchored the panel to the header edge.')).toBeInTheDocument()
  })

  it('shows the latest reply, not the first', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1,
      items: [item({
        messages: [
          reply({ id: 'm1', body: 'First attempt.' }),
          reply({ id: 'm2', role: 'user', body: 'still broken' }),
          reply({ id: 'm3', body: 'Second attempt, properly this time.' }),
        ],
      })],
    })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    expect(screen.getByText('Second attempt, properly this time.')).toBeInTheDocument()
    expect(screen.queryByText('First attempt.')).not.toBeInTheDocument()
  })

  it('says what the state means, not the database word', async () => {
    // 'skipped' means the agent stopped and is waiting on you, which is
    // close to the opposite of what the word suggests.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 2,
      items: [item({ status: 'skipped' }), item({ id: 'f2', status: 'failed' })],
    })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 2 unread'))
    expect(screen.getByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText("Couldn't fix")).toBeInTheDocument()
    expect(screen.queryByText('skipped')).not.toBeInTheDocument()
  })

  it('tags a thread with how many messages are waiting', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 2, items: [item({ unread: 2 })],
    })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 2 unread'))
    expect(screen.getByLabelText('2 new messages')).toBeInTheDocument()
  })

  it('opens the thread and drops that thread from the count', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 2,
      items: [item({ unread: 1 }),
              item({ id: 'f2', body: 'Fix the treadmill card', unread: 1 })],
    })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 2 unread'))
    await userEvent.click(screen.getByText('Add a bell for superusers'))
    expect(navigate).toHaveBeenCalledWith('/feedback/f1')
    // Decremented, not cleared: the other thread is still waiting.
    expect(await screen.findByLabelText('Notifications, 1 unread')).toBeInTheDocument()
  })

  it('keeps a read thread in the list', async () => {
    // The panel is the only record of what the automation did to the code.
    // Clearing on read would hide it the moment you glanced at the badge.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item()] })
    const mark = vi.spyOn(api.feedback, 'markSeen').mockResolvedValue({ marked: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    await userEvent.click(screen.getByText('Mark all read'))
    await waitFor(() => expect(mark).toHaveBeenCalledWith(['f1']))
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
    expect(screen.getByText('Add a bell for superusers')).toBeInTheDocument()
  })

  it('does not mark anything read just for being opened', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const mark = vi.spyOn(api.feedback, 'markSeen').mockResolvedValue({ marked: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    expect(mark).not.toHaveBeenCalled()
  })

  it('clears the list without deleting the record', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const dismiss = vi.spyOn(api.feedback, 'dismiss').mockResolvedValue({ dismissed: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    await userEvent.click(screen.getByText('Clear all'))
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith(['f1']))
    expect(screen.getByText(/Nothing here/)).toBeInTheDocument()
  })

  it('clears one row on a left swipe', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const dismiss = vi.spyOn(api.feedback, 'dismiss').mockResolvedValue({ dismissed: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = screen.getByText('Add a bell for superusers').closest('button')!
    fireEvent.touchStart(row, { touches: [{ clientX: 200 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 100 }] })
    fireEvent.touchEnd(row)
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith(['f1']))
    expect(navigate).not.toHaveBeenCalled()
  })

  it('does not open the thread on a swipe that was abandoned', async () => {
    // Opening on a half-finished drag is the thing that makes swipe lists
    // infuriating.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const dismiss = vi.spyOn(api.feedback, 'dismiss').mockResolvedValue({ dismissed: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = screen.getByText('Add a bell for superusers').closest('button')!
    fireEvent.touchStart(row, { touches: [{ clientX: 200 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 170 }] })
    fireEvent.touchEnd(row)
    expect(dismiss).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('opens the thread on a tap that barely moved', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = screen.getByText('Add a bell for superusers').closest('button')!
    fireEvent.touchStart(row, { touches: [{ clientX: 200 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 197 }] })
    fireEvent.touchEnd(row)
    expect(navigate).toHaveBeenCalledWith('/feedback/f1')
  })

  it('stays quiet when a poll fails', async () => {
    vi.spyOn(api.feedback, 'notifications').mockRejectedValue(new Error('offline'))
    show()
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })
})
