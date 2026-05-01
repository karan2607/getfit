const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export async function* streamChatMessage(
  sessionId: string,
  content: string,
  token: string,
): AsyncGenerator<{ chunk?: string; done?: boolean; message_id?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}/messages/`, {
    method: 'POST',
    headers: {
      Authorization: `Token ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ content }),
  })

  if (!res.ok || !res.body) {
    throw new Error(`Stream request failed (${res.status})`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const payload = JSON.parse(line.slice(6))
          yield payload
          if (payload.done) return
        } catch {
          // skip malformed SSE line
        }
      }
    }
  }
}
