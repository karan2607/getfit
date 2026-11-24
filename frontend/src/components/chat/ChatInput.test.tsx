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

/**
 * The full-screen editor, from a keyboard.
 *
 * It was a `role="dialog"` with no `aria-modal`, no Escape handler, and a
 * Collapse that returned focus to `document.body` — so a keyboard user who
 * opened it had to find the one button that closes it, and then tab back
 * through the entire transcript to reach the composer they were typing in.
 */
describe('ChatInput expanded editor', () => {
  function expand(value = 'x'.repeat(200)) {
    render(<ChatInput onSend={vi.fn()} />)
    const box = screen.getByRole('textbox')
    fireEvent.change(box, { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: /expand/i }))
    return box
  }

  it('closes on Escape', async () => {
    expand()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('announces itself as modal', () => {
    expand()
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
  })

  it('gives focus back to the composer on collapse', async () => {
    const box = expand()
    box.focus()
    fireEvent.click(screen.getByRole('button', { name: /expand/i }))
    fireEvent.click(screen.getByRole('button', { name: /collapse/i }))
    await waitFor(() => expect(document.activeElement).toBe(box))
  })

  it('says why Send is unavailable', () => {
    render(<ChatInput onSend={vi.fn()} disabled />)
    const send = screen.getByRole('button', { name: /send/i })
    expect(send).toBeDisabled()
    expect(send.getAttribute('aria-label')).toMatch(/not ready/i)
  })

  it('says the other reason when there is nothing typed', () => {
    render(<ChatInput onSend={vi.fn()} />)
    const send = screen.getByRole('button', { name: /send/i })
    expect(send.getAttribute('aria-label')).toMatch(/type a message/i)
  })
})

describe('clearing the composer', () => {
  /*
   * Reported: "on sending message, textbox doesn't become empty".
   *
   * The hook's send resolves when the REPLY has finished streaming, not when
   * the message is accepted, so awaiting it before clearing left the question
   * sitting in the composer for the whole answer.
   */
  it('empties the box as soon as the message is sent, not when the reply ends', async () => {
    let finish: (v: boolean) => void = () => {}
    const onSend = vi.fn(() => new Promise<boolean>((r) => { finish = r }))
    render(<ChatInput onSend={onSend} />)
    const box = screen.getByRole('textbox')

    fireEvent.change(box, { target: { value: 'swap my squats' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    // The reply has NOT arrived yet.
    await waitFor(() => expect(box).toHaveValue(''))
    expect(onSend).toHaveBeenCalledWith('swap my squats')

    finish(true)
    await waitFor(() => expect(box).toHaveValue(''))
  })

  it('puts the text back when the send is refused', async () => {
    const onSend = vi.fn(async () => false)
    render(<ChatInput onSend={onSend} />)
    const box = screen.getByRole('textbox')

    fireEvent.change(box, { target: { value: 'swap my squats' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(box).toHaveValue('swap my squats'))
  })
})
