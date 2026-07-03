const parseApiPayload = async (response) => {
  const raw = await response.text()
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

const postAction = async (url) => {
  const response = await fetch(url, { method: 'POST' })
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(data.detail || 'Request failed')
  }
  return data
}

export async function pauseJob(jobId) {
  return postAction(`/api/orchestrate/${jobId}/pause`)
}

export async function resumeJob(jobId) {
  return postAction(`/api/orchestrate/${jobId}/resume`)
}

export async function approveJob(jobId) {
  return postAction(`/api/orchestrate/${jobId}/approve`)
}

export async function rejectJob(jobId) {
  return postAction(`/api/orchestrate/${jobId}/reject`)
}
