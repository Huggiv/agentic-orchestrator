const parseApiPayload = async (response) => {
  const raw = await response.text()
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

const unwrapError = (data, fallback) => {
  if (!data) return fallback
  if (typeof data.detail === 'string' && data.detail) return data.detail
  if (data.detail && typeof data.detail === 'object') {
    if (data.detail.message) return data.detail.message
    if (data.detail.code) return data.detail.code
  }
  if (data.error && typeof data.error === 'object') {
    if (data.error.message) return data.error.message
    if (data.error.code) return data.error.code
  }
  return fallback
}

export async function createChatSession(payload) {
  const response = await fetch('/api/chat/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to create chat session'))
  }
  return data
}

export async function listChatSessions(limit = 30) {
  const response = await fetch(`/api/chat/sessions?limit=${encodeURIComponent(limit)}`)
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to load chat sessions'))
  }
  return Array.isArray(data.sessions) ? data.sessions : []
}

export async function getChatSessionMessages(sessionId) {
  const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`)
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to load chat messages'))
  }
  return Array.isArray(data.messages) ? data.messages : []
}

export async function sendChatSessionMessage(sessionId, payload) {
  const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to send chat message'))
  }
  return data
}

export async function prepareChatSessionTrigger(sessionId, payload) {
  const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/prepare-trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to prepare workflow trigger'))
  }
  return data
}

export async function confirmChatSessionTrigger(sessionId, payload) {
  const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/confirm-trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to confirm workflow trigger'))
  }
  return data
}

export async function archiveChatSession(sessionId) {
  const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(unwrapError(data, 'Failed to archive chat session'))
  }
  return data
}
