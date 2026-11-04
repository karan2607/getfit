import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import NotificationBell from './NotificationBell'
import { api, type Feedback } from '../lib/api'

const mockUser = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockUser() }))

const item = (over: Partial<Feedback> = {}): Feedback => ({
  id: 'f1', body: 'Add a bell for superusers', page: '/profile',
  status: 'done' as const, result_notes: 'Shipped the bell.', commit_sha: 'abcdef1234567890',
  submitted_by: 'admin@test.local', created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(), seen_at: null, ...over,
})

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockUser.mockReturnValue({ user: { is_superuser: true } })
  })

  it('renders nothing for an ordinary user', () => {
    mockUser.mockReturnValue({ user: { is_superuser: false } })
    const spy = vi.spyOn(api.feedback, 'notifications')
    const { container } = render(<NotificationBell />)
    expect(container).toBeEmptyDOMElement()
    // And does not even ask -- the endpoint is superuser-only.
    expect(spy).not.toHaveBeenCalled()
  })

  it('shows a badge with the unread count', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 2, items: [item(), item({ id: 'f2' })],
    })
    render(<NotificationBell />)
    expect(await screen.findByLabelText('Notifications, 2 unread')).toBeInTheDocument()
  })

  it('shows no badge when there is nothing new', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 0, items: [] })
    render(<NotificationBell />)
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })

  it('lists what finished, with the result and commit', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    render(<NotificationBell />)
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    expect(screen.getByText('Add a bell for superusers')).toBeInTheDocument()
    expect(screen.getByText('Shipped the bell.')).toBeInTheDocument()
    expect(screen.getByText('abcdef1234')).toBeInTheDocument()
  })

  it('surfaces a failure rather than hiding it', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item({ status: 'failed', result_notes: 'Tests did not pass.' })],
    })
    render(<NotificationBell />)
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.getByText('Tests did not pass.')).toBeInTheDocument()
  })

  it('clears the badge only when explicitly dismissed', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const mark = vi.spyOn(api.feedback, 'markSeen').mockResolvedValue({ marked: 1 })
    render(<NotificationBell />)
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    // Opening alone must not mark them read -- a glance is not an ack.
    expect(mark).not.toHaveBeenCalled()
    await userEvent.click(screen.getByText('Mark all read'))
    await waitFor(() => expect(mark).toHaveBeenCalledWith(['f1']))
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })

  it('stays quiet when a poll fails', async () => {
    vi.spyOn(api.feedback, 'notifications').mockRejectedValue(new Error('offline'))
    render(<NotificationBell />)
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })
})
