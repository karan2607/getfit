import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react'

interface Props {
  /**
   * Hand the message over. Return false (or a promise of it) to say it was
   * NOT accepted, and the composer keeps what was typed.
   *
   * It used to return nothing and the textarea was cleared regardless, so
   * every path that dropped a message -- no session, no token, a turn
   * already in flight -- also destroyed the words.
   */
  onSend: (content: string) => boolean | void | Promise<boolean | void>
  /**
   * Hard block — the input cannot be used at all (e.g. the chat session is
   * still being created). Deliberately NOT set while a reply is streaming:
   * putting `disabled` on the textarea in that case is what stopped you typing
   * your next question while the AI was still answering.
   */
  disabled?: boolean
  /** A reply is in flight; messages sent now are queued, not dropped. */
  isReplying?: boolean
}

/*
 * The cap is a share of the viewport, not a fixed 160px.
 *
 * 160px is roughly four lines. On a 390x844 phone a pasted prompt of a few
 * hundred characters filled it and then had nowhere to go: the textarea had
 * `maxHeight` but no `overflow`, so the text scrolled under its own top edge
 * with no scrollbar and no way to reach it. You could send something you had
 * never been able to read.
 *
 * 40% leaves the conversation visible above the composer, which is the reason
 * not to simply grow without limit. Past the cap the field scrolls, and the
 * expand button opens the full-screen editor for anything long enough that
 * scrolling a quarter of the screen is the wrong way to review it.
 */
const VIEWPORT_SHARE = 0.4
const MIN_CAP = 120

export default function ChatInput({ onSend, disabled, isReplying }: Props) {
  const [value, setValue] = useState('')
  const [expanded, setExpanded] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const expandedRef = useRef<HTMLTextAreaElement>(null)

  const cap = useCallback(
    () => Math.max(MIN_CAP, Math.round(window.innerHeight * VIEWPORT_SHARE)),
    [],
  )

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    const max = cap()
    el.style.height = 'auto'
    const next = Math.min(el.scrollHeight, max)
    el.style.height = `${next}px`
    // Only scrollable once it has actually stopped growing, so short input
    // never shows a scrollbar.
    el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
  }, [cap])

  // The cap is relative to the viewport, so it moves when the on-screen
  // keyboard opens or the phone rotates.
  useEffect(() => {
    resize()
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [resize, value])

  /*
   * Focus goes into the editor, and comes back out where it started.
   *
   * Collapsing returned focus to `document.body`, which drops a keyboard
   * user at the top of the page and makes them tab back through the whole
   * transcript to reach the composer they were just typing in.
   */
  useEffect(() => {
    if (!expanded) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    expandedRef.current?.focus()
    return () => {
      const back = previouslyFocused ?? textareaRef.current
      back?.focus?.()
    }
  }, [expanded])

  // Escape closes it. A full-screen dialog that traps you until you find
  // the Collapse button is the kind of thing a keyboard user cannot guess.
  useEffect(() => {
    if (!expanded) return
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        setExpanded(false)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [expanded])

  async function handleSend() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return

    /*
     * Clear first, put it back only if the send is refused.
     *
     * Waiting for `onSend` to resolve before clearing looked like the safe
     * way to avoid losing typed text, and it was wrong: the hook resolves
     * when the whole REPLY has finished streaming, not when the message is
     * accepted. So the composer sat there still full of the question for the
     * entire answer -- eight seconds on a good turn, a hundred on a slow one
     * -- and reported as "on sending message, textbox doesn't become empty".
     *
     * A refusal is decided by guards at the top of the hook and comes back
     * in the same tick, so the text reappears instantly in the one case
     * where it matters, and nothing typed is ever lost.
     */
    setValue('')
    setExpanded(false)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.overflowY = 'hidden'
    }
    // Only `false` means refused. A handler that returns nothing is the
    // old contract and keeps the composer cleared.
    const accepted = await onSend(trimmed)
    if (accepted === false) {
      setValue(trimmed)
      requestAnimationFrame(() => textareaRef.current?.focus())
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Worth expanding only once the text is long enough that the capped field
  // would be a keyhole. Below that the button is noise.
  const longEnoughToExpand = value.length > 180

  /*
   * Why Send is unavailable, in words.
   *
   * It was `disabled` with `opacity-40` and a fixed aria-label of "Send",
   * so a greyed-out button gave a sighted user no reason and a screen
   * reader user nothing at all. "Nothing typed yet" is self-evident on
   * screen; "the chat is still opening" is not, and in the drawer it is
   * exactly the state a failed start leaves you in.
   */
  const sendBlockedBecause = disabled
    ? 'The chat is not ready yet'
    : !value.trim()
      ? 'Type a message first'
      : null

  return (
    <div>
      {expanded && (
        <div
          className="fixed inset-0 z-[60] bg-white flex flex-col"
          role="dialog"
          aria-modal="true"
          aria-label="Edit message"
        >
          <div
            className="flex items-center justify-between px-4 py-3 border-b border-gray-200"
            style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}
          >
            <button
              onClick={() => setExpanded(false)}
              className="text-sm font-medium text-gray-500 min-h-[44px] px-2"
            >
              Collapse
            </button>
            <span className="text-xs text-gray-400">{value.length} characters</span>
            <button
              onClick={handleSend}
              disabled={disabled || !value.trim()}
              title={sendBlockedBecause ?? 'Send'}
              aria-label={sendBlockedBecause ? `Send — ${sendBlockedBecause}` : 'Send'}
              className="text-sm font-semibold text-accent-600 disabled:opacity-40 min-h-[44px] px-2"
            >
              Send
            </button>
          </div>
          <textarea
            ref={expandedRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Ask your AI trainer anything..."
            className="flex-1 w-full resize-none px-4 py-3 text-base text-gray-800 placeholder-gray-400 outline-none leading-relaxed"
            style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
          />
        </div>
      )}

      {isReplying && (
        <p className="text-[11px] text-gray-400 px-2 pb-1">
          Still replying — send another and it'll answer both.
        </p>
      )}
      <div className="flex items-end gap-2 bg-white border border-gray-200 rounded-2xl px-4 py-3 shadow-sm">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={isReplying ? 'Add another message...' : 'Ask your AI trainer anything...'}
          rows={1}
          className="flex-1 min-w-0 resize-none text-sm text-gray-800 placeholder-gray-400 outline-none bg-transparent leading-relaxed disabled:opacity-50"
        />
        {longEnoughToExpand && (
          <button
            onClick={() => setExpanded(true)}
            className="flex-shrink-0 w-11 h-11 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-50 flex items-center justify-center transition-colors"
            aria-label="Expand to full screen"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 2H2v4M10 14h4v-4M14 6V2h-4M2 10v4h4" />
            </svg>
          </button>
        )}
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          title={sendBlockedBecause ?? 'Send'}
          className="flex-shrink-0 w-11 h-11 rounded-xl bg-accent-600 hover:bg-accent-700 disabled:opacity-40 flex items-center justify-center transition-colors"
          aria-label={sendBlockedBecause ? `Send — ${sendBlockedBecause}` : 'Send'}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 8L14 2L8 14L7 9L2 8Z" fill="white" stroke="white" strokeWidth="1" strokeLinejoin="round"/>
          </svg>
        </button>
      </div>
    </div>
  )
}
