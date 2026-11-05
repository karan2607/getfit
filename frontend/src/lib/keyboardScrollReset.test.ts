import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { installKeyboardScrollReset } from './keyboardScrollReset'

describe('installKeyboardScrollReset', () => {
  let uninstall: () => void
  let scrollTo: ReturnType<typeof vi.fn>

  function setScrollY(y: number) {
    Object.defineProperty(window, 'scrollY', { value: y, configurable: true })
  }

  function setDocHeight(h: number) {
    Object.defineProperty(document.documentElement, 'scrollHeight', { value: h, configurable: true })
  }

  beforeEach(() => {
    vi.useFakeTimers()
    scrollTo = vi.fn()
    window.scrollTo = scrollTo as unknown as typeof window.scrollTo
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true })
    setDocHeight(800)
    document.body.innerHTML = '<input id="a" /><input id="b" /><button id="btn">x</button>'
    uninstall = installKeyboardScrollReset(window)
  })

  afterEach(() => {
    uninstall()
    setScrollY(0)
    vi.useRealTimers()
  })

  it('scrolls the page back after the keyboard closes and leaves it shifted', () => {
    const input = document.getElementById('a') as HTMLInputElement
    input.focus()
    setScrollY(300) // Safari pushed the page up for the keyboard
    input.blur()
    vi.advanceTimersByTime(100)
    expect(scrollTo).toHaveBeenCalledWith(0, 0)
  })

  it('leaves the page alone when focus moves to another field', () => {
    const a = document.getElementById('a') as HTMLInputElement
    const b = document.getElementById('b') as HTMLInputElement
    a.focus()
    setScrollY(300)
    b.focus()
    vi.advanceTimersByTime(100)
    expect(scrollTo).not.toHaveBeenCalled()
  })

  it('keeps the scroll on pages that are genuinely taller than the screen', () => {
    setDocHeight(1600)
    const input = document.getElementById('a') as HTMLInputElement
    input.focus()
    setScrollY(300)
    input.blur()
    vi.advanceTimersByTime(100)
    expect(scrollTo).not.toHaveBeenCalled()
  })

  it('does nothing when the page was never shifted', () => {
    const input = document.getElementById('a') as HTMLInputElement
    input.focus()
    input.blur()
    vi.advanceTimersByTime(100)
    expect(scrollTo).not.toHaveBeenCalled()
  })
})
