import { useState, useCallback } from 'react'
import { api, type ChatMessage, type ChatSessionDetail } from '../lib/api'
import { streamChatMessage } from '../lib/stream'
import { getToken } from '../lib/auth'
import { getErrorMessage } from '../lib/errors'

export function useChatSession(sessionId: string | undefined) {
  const [session, setSession] = useState<ChatSessionDetail | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState<string | null>(null)

  const loadSession = useCallback(async (id: string) => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await api.chat.getSession(id)
      setSession(data)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setIsLoading(false)
    }
  }, [])

  const sendMessage = useCallback(async (content: string, extra?: Record<string, unknown>) => {
    if (!sessionId || isSending) return
    const token = getToken()
    if (!token) return

    const userMsg: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }

    setSession((prev) => prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev)
    setIsSending(true)
    setStreamingContent('')
    setError(null)

    try {
      let accumulated = ''
      for await (const event of streamChatMessage(sessionId, content, token, extra)) {
        if (event.error) {
          setError(event.error)
          setStreamingContent('')
          break
        }
        if (event.chunk) {
          accumulated += event.chunk
          setStreamingContent(accumulated)
        }
        if (event.done && event.message_id) {
          const assistantMsg: ChatMessage = {
            id: event.message_id,
            role: 'assistant',
            content: accumulated,
            created_at: new Date().toISOString(),
          }
          setSession((prev) =>
            prev ? { ...prev, messages: [...prev.messages, assistantMsg] } : prev
          )
          setStreamingContent('')
        }
      }
    } catch (err) {
      setError(getErrorMessage(err))
      setStreamingContent('')
    } finally {
      setIsSending(false)
    }
  }, [sessionId, isSending])

  return { session, isLoading, isSending, streamingContent, error, loadSession, sendMessage, setSession }
}
