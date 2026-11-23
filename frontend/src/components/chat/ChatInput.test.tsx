import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ChatInput from './ChatInput'

/**
 * The composer must not destroy what you typed.
 *
 * It cleared the textarea the moment it handed the text over, without ever
 * asking whether it was accepted. Every silent-drop path in the hook -- no
 * session, no token, a turn already in flight -- therefore also lost the
 * words, and the user's only recourse was to type the whole thing again.
 * That is the complaint behind "I don't always want to re-type the entire
 * message".
 */

function type(text: string) {
  const box = screen.getByRole('textbox')
  fireEvent.change(box, { target: { value: text } })
  return box
}

describe('ChatInput', () => {
  it('keeps the message when the send is refused', async () => {
    const onSend = vi.fn().mockReturnValue(false)
    render(<ChatInput onSend={onSend} />)
    const box = type('a long message I do not want to lose')
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(onSend).toHaveBeenCalled())
    expect((box as HTMLTextAreaElement).value)
      .toBe('a long message I do not want to lose')
  })

  it('clears it when the send is accepted', async () => {
    const onSend = vi.fn().mockReturnValue(true)
    render(<ChatInput onSend={onSend} />)
    const box = type('off it goes')
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe(''))
  })

  it('still clears for a handler that returns nothing', async () => {
    // The old contract. Surfaces that queue instead of sending rely on it.
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)
    const box = type('queued, not sent')
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe(''))
  })

  it('waits for an async handler before deciding', async () => {
    const onSend = vi.fn().mockResolvedValue(false)
    render(<ChatInput onSend={onSend} />)
    const box = type('slow refusal')
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(onSend).toHaveBeenCalled())
    expect((box as HTMLTextAreaElement).value).toBe('slow refusal')
  })

  it('sends nothing when disabled', () => {
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} disabled />)
    const box = type('blocked')
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })
})
