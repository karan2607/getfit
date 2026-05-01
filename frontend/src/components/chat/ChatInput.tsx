import { useState, useRef, type KeyboardEvent } from 'react'

interface Props {
  onSend: (content: string) => void
  disabled?: boolean
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  function handleSend() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleInput() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }

  return (
    <div className="flex items-end gap-2 bg-white border border-gray-200 rounded-2xl px-4 py-3 shadow-sm">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onInput={handleInput}
        disabled={disabled}
        placeholder="Ask your AI trainer anything..."
        rows={1}
        className="flex-1 resize-none text-sm text-gray-800 placeholder-gray-400 outline-none bg-transparent leading-relaxed disabled:opacity-50"
        style={{ maxHeight: '160px' }}
      />
      <button
        onClick={handleSend}
        disabled={disabled || !value.trim()}
        className="flex-shrink-0 w-8 h-8 rounded-xl bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 flex items-center justify-center transition-colors"
        aria-label="Send"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M2 8L14 2L8 14L7 9L2 8Z" fill="white" stroke="white" strokeWidth="1" strokeLinejoin="round"/>
        </svg>
      </button>
    </div>
  )
}
