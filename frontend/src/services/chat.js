const parseApiPayload = async (response) => {
  const raw = await response.text()
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

export async function assignGroomingFlow(payload) {
  const response = await fetch('/api/chat/grooming/assign', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(data.detail || 'Failed to assign grooming flow')
  }
  return data
}
