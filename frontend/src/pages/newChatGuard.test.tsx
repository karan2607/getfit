import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { api } from '../lib/api'

/**
 * Two taps on "+ New chat" before the first request returns created two
 * empty conversations, and the list then showed two identical rows the
 * user had to tell apart and delete.
 */
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { profile: {} } }),
}))
vi.mock('../components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

describe('new chat', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('creates one conversation however fast you tap', async () => {
    vi.spyOn(api.chat, 'listSessions').mockResolvedValue([] as never)
    let resolve: (v: unknown) => void = () => {}
    const create = vi.spyOn(api.chat, 'createSession').mockReturnValue(
      new Promise((r) => { resolve = r }) as never)

    const Chat = (await import('./Chat')).default
    render(<MemoryRouter><Chat /></MemoryRouter>)

    const button = await screen.findByRole('button', { name: /new chat/i })
    await userEvent.click(button)
    await userEvent.click(button)
    await userEvent.click(button)

    resolve({ id: 's1', title: 'New chat', created_at: '2026-11-24T00:00:00Z' })
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
  })

  it('says it is working while the request is out', async () => {
    vi.spyOn(api.chat, 'listSessions').mockResolvedValue([] as never)
    vi.spyOn(api.chat, 'createSession').mockReturnValue(
      new Promise(() => {}) as never)

    const Chat = (await import('./Chat')).default
    render(<MemoryRouter><Chat /></MemoryRouter>)

    await userEvent.click(await screen.findByRole('button', { name: /new chat/i }))
    expect(await screen.findByRole('button', { name: /starting/i }))
      .toBeDisabled()
  })
})
