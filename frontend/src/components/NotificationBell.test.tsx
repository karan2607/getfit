import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import NotificationBell from './NotificationBell'
import { api, type Feedback, type FeedbackMessage } from '../lib/api'

const mockUser = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockUser() }))

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

const show = () => render(<NotificationBell />)

/** The swipeable row: the element carrying the transform, by its text. */
const rowOf = (text: string) =>
  screen.getByText(text).closest('[style*="translateX"]') as HTMLElement

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
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

  it('opens the thread inside the panel, not on a new page', async () => {
    // It used to navigate to /feedback/:id, which tore down the page you
    // were on and refetched everything it needed to read three sentences.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 2,
      items: [item({ unread: 1 }),
              item({ id: 'f2', body: 'Fix the treadmill card', unread: 1 })],
    })
    // unread: 0 because the GET is what marks a thread read, server-side.
    const thread = vi.spyOn(api.feedback, 'thread').mockResolvedValue(
      item({ messages: [reply()], unread: 0 }),
    )
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 2 unread'))
    await userEvent.click(screen.getAllByText('Open conversation')[0])

    // Same surface: the list is still mounted behind the open thread.
    expect(await screen.findByLabelText('Reply to the agent')).toBeInTheDocument()
    expect(screen.getByText('Fix the treadmill card')).toBeInTheDocument()
    await waitFor(() => expect(thread).toHaveBeenCalledWith('f1'))
    // Decremented, not cleared: the other thread is still waiting.
    expect(await screen.findByLabelText('Notifications, 1 unread')).toBeInTheDocument()
  })

  it('shows the thread from the list copy before the fetch lands', async () => {
    // So the pane slides in with content instead of a spinner.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item({ messages: [reply()] })],
    })
    vi.spyOn(api.feedback, 'thread').mockImplementation(
      () => new Promise(() => {}) as Promise<Feedback>,
    )
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    await userEvent.click(screen.getAllByText('Open conversation')[0])
    // Twice: the list row's summary line, and the bubble in the open pane.
    // Both panes stay mounted, which is what makes the slide a slide.
    expect(screen.getAllByText('Anchored the panel to the header edge.')).toHaveLength(2)
  })

  it('goes back to the list without closing the panel', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item()] })
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(item())
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    await userEvent.click(screen.getAllByText('Open conversation')[0])
    await userEvent.click(await screen.findByLabelText('Back to notifications'))
    expect(screen.queryByLabelText('Reply to the agent')).not.toBeInTheDocument()
    expect(screen.getByText('Feedback updates')).toBeInTheDocument()
  })

  it('sends a reply from the panel and shows it reopened', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item()] })
    vi.spyOn(api.feedback, 'thread').mockResolvedValue(item())
    const send = vi.spyOn(api.feedback, 'reply').mockResolvedValue(
      item({
        status: 'new',
        messages: [reply({ id: 'm9', role: 'user', body: 'still off on Diet' })],
      }),
    )
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    await userEvent.click(screen.getAllByText('Open conversation')[0])
    const box = await screen.findByLabelText('Reply to the agent')
    await userEvent.type(box, 'still off on Diet')
    await userEvent.click(screen.getByLabelText('Send'))
    await waitFor(() => expect(send).toHaveBeenCalledWith('f1', 'still off on Diet'))
    expect(await screen.findByText(/polls every few minutes/)).toBeInTheDocument()
  })

  it('keeps the thread readable when the refresh fails', async () => {
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 1, items: [item({ messages: [reply()] })],
    })
    vi.spyOn(api.feedback, 'thread').mockRejectedValue(new Error('offline'))
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    await userEvent.click(screen.getAllByText('Open conversation')[0])
    expect(await screen.findByText(/Could not load the latest/)).toBeInTheDocument()
    // The list copy is enough to read, so a failed refresh is a note and
    // not a blank pane.
    expect(screen.getAllByText('Anchored the panel to the header edge.').length)
      .toBeGreaterThan(1)
    expect(screen.getByLabelText('Reply to the agent')).toBeInTheDocument()
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
    const row = rowOf('Add a bell for superusers')
    fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 400 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 100, clientY: 402 }] })
    fireEvent.touchEnd(row)
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith(['f1']))
    expect(screen.queryByLabelText('Reply to the agent')).not.toBeInTheDocument()
  })

  it('does not open the thread on a swipe that was abandoned', async () => {
    // Opening on a half-finished drag is the thing that makes swipe lists
    // infuriating.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const dismiss = vi.spyOn(api.feedback, 'dismiss').mockResolvedValue({ dismissed: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = rowOf('Add a bell for superusers')
    fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 400 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 170, clientY: 402 }] })
    fireEvent.touchEnd(row)
    expect(dismiss).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Reply to the agent')).not.toBeInTheDocument()
  })

  it('does not open anything when you scroll the list', async () => {
    /*
     * The reported bug. touchEnd used to treat "barely moved horizontally"
     * as a tap -- and a vertical scroll is exactly that, so lifting your
     * finger after scrolling opened whichever row it ended under. Opening
     * is now the explicit button and nothing else.
     */
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const thread = vi.spyOn(api.feedback, 'thread').mockResolvedValue(item())
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = rowOf('Add a bell for superusers')
    fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 400 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 203, clientY: 300 }] })
    fireEvent.touchEnd(row)
    expect(thread).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Reply to the agent')).not.toBeInTheDocument()
  })

  it('does not drag the row sideways while you scroll', async () => {
    // A thumb never travels straight down, so a vertical gesture carries
    // some horizontal wobble. Honouring it slid every row under the finger.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = rowOf('Add a bell for superusers')
    fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 400 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 185, clientY: 280 }] })
    expect(row.style.transform).toBe('translateX(0px)')
  })

  it('keeps a gesture that began as a scroll a scroll', async () => {
    // Even a big sideways drift later on: the axis is decided once, on the
    // first real movement, or a long scroll could dismiss a row by accident.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({ unseen: 1, items: [item()] })
    const dismiss = vi.spyOn(api.feedback, 'dismiss').mockResolvedValue({ dismissed: 1 })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 1 unread'))
    const row = rowOf('Add a bell for superusers')
    fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 400 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 198, clientY: 300 }] })
    fireEvent.touchMove(row, { touches: [{ clientX: 60, clientY: 280 }] })
    fireEvent.touchEnd(row)
    expect(dismiss).not.toHaveBeenCalled()
  })

  it('gives Fixed a pill that cannot be mistaken for failure', async () => {
    // Accent-50 and red-50 are adjacent hues on a 60px pill, so at a glance
    // "Fixed" and "Couldn't fix" read as the same state -- the one state you
    // most want to pick out of a list was the hardest to.
    vi.spyOn(api.feedback, 'notifications').mockResolvedValue({
      unseen: 2,
      items: [item(), item({ id: 'f2', body: 'other', status: 'failed' })],
    })
    show()
    await userEvent.click(await screen.findByLabelText('Notifications, 2 unread'))
    const fixed = screen.getByText('Fixed').className
    const failed = screen.getByText("Couldn't fix").className
    expect(fixed).not.toEqual(failed)
    // Filled vs outlined, which holds up before hue does.
    expect(fixed).toContain('bg-ink-600')
    expect(failed).toContain('bg-red-50')
  })

  it('stays quiet when a poll fails', async () => {
    vi.spyOn(api.feedback, 'notifications').mockRejectedValue(new Error('offline'))
    show()
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })
})
