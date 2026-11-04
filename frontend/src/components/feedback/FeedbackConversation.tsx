import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { Feedback, FeedbackMessage } from '../../lib/api'
import { statusLine } from '../../lib/feedbackStatus'

/**
 * The turns of one feedback thread, plus the box to answer in.
 *
 * Shared by the bell panel and the standalone /feedback/:id page so the two
 * cannot drift. The panel is where this is actually used -- the page exists
 * for a direct link -- and neither owns a header, so each can sit in
 * whatever chrome it already has.
 */

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function Bubble({ message }: { message: FeedbackMessage }) {
  const mine = message.role === 'user'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div className="max-w-[85%]">
        <div
          className={`rounded-2xl px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words ${
            mine
              ? 'bg-brand-500 text-white rounded-br-md'
              : 'bg-gray-100 text-gray-800 rounded-bl-md'
          }`}
        >
          {message.body}
        </div>
        <p className={`text-[11px] text-gray-400 mt-0.5 ${mine ? 'text-right' : ''}`}>
          {mine ? 'You' : 'Agent'} · {when(message.created_at)}
        </p>
      </div>
    </div>
  )
}

interface Props {
  item: Feedback
  sending: boolean
  onReply: (body: string) => void
}

export default function FeedbackConversation({ item, sending, onReply }: Props) {
  const [draft, setDraft] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const boxRef = useRef<HTMLTextAreaElement>(null)
  const line = statusLine(item)
  const working = item.status === 'new' || item.status === 'in_progress'

  useEffect(() => {
    // Absent in jsdom and on some older WebViews. Failing to scroll is
    // cosmetic, so it must not take the view down with it.
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [item.messages.length, working])

  // Grows with the text, up to a few lines. A fixed single line meant a
  // two-sentence reply scrolled under its own top edge with no scrollbar.
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 96)}px`
  }, [draft])

  const send = () => {
    const body = draft.trim()
    if (!body || sending) return
    onReply(body)
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, shift+Enter breaks the line. On a phone the on-screen
    // return key inserts a newline either way, which is why there is also a
    // visible send button.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3">
        {/* What you filed is the first turn, not a header: the whole thing
            reads as one conversation that way. */}
        <Bubble
          message={{
            id: 'head', role: 'user', body: item.body,
            seen_at: null, created_at: item.created_at,
          }}
        />
        {item.messages.map((m) => <Bubble key={m.id} message={m} />)}

        {/* An item that finished with nothing to say would otherwise end the
            thread on your own question, which reads as having been ignored. */}
        {!working && !item.messages.some((m) => m.role === 'agent') && (
          <Bubble
            message={{
              id: 'fallback', role: 'agent', body: line.fallback,
              seen_at: null, created_at: item.updated_at,
            }}
          />
        )}

        {working && (
          <div className="flex justify-start">
            <div className="bg-gray-100 rounded-2xl rounded-bl-md px-3.5 py-2 text-sm text-gray-500">
              {item.status === 'new'
                ? 'Queued — it polls every few minutes.'
                : 'Working on it now…'}
            </div>
          </div>
        )}

        {item.commit_sha && (
          <p className="text-[11px] text-gray-400 font-mono text-center">
            shipped in {item.commit_sha.slice(0, 10)}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex-shrink-0 border-t border-gray-100 p-2">
        <div className="flex items-end gap-2">
          <textarea
            ref={boxRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Reply to the agent…"
            aria-label="Reply to the agent"
            // 16px minimum, or iOS Safari zooms the viewport on focus. The
            // global touch rule in index.css enforces it; stated here so a
            // utility class cannot quietly win against it.
            className="flex-1 resize-none rounded-xl border border-gray-200 px-3 py-2 text-base leading-snug focus:outline-none focus:border-brand-400"
          />
          <button
            type="button"
            onClick={send}
            disabled={!draft.trim() || sending}
            className="flex-shrink-0 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl bg-brand-500 text-white disabled:opacity-40 hover:bg-brand-600 transition-colors"
            aria-label="Send"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 12h15M13 6l6 6-6 6" />
            </svg>
          </button>
        </div>
        <p className="px-1 pt-1 text-[11px] text-gray-400">
          Replies reopen this item and go back to the agent.
        </p>
      </div>
    </>
  )
}
