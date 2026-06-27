const parseApiPayload = async (response) => {
  const raw = await response.text()
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

const postJson = async (url, payload) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(data.detail || 'Request failed')
  }
  return data
}

export async function signup(payload) {
  return postJson('/api/auth/signup', payload)
}

export async function login(payload) {
  return postJson('/api/auth/login', payload)
}

export async function getSession() {
  const response = await fetch('/api/auth/session')
  const data = await parseApiPayload(response)
  if (!response.ok) {
    throw new Error(data.detail || 'Failed to fetch session')
  }
  return data
}

export async function logout() {
  return postJson('/api/auth/logout', {})
}

// Role-based permission helpers shared across the UI.
export const ROLE_LABELS = {
  admin: 'Admin',
  developer: 'Developer',
  user: 'User',
}

export function canRunWorkflows(user) {
  return user?.role === 'admin' || user?.role === 'developer'
}

export function canManageHistory(user) {
  return user?.role === 'admin'
}

const PASSWORD_RULES = [
  { test: (v) => v.length >= 8, label: 'At least 8 characters' },
  { test: (v) => /[A-Z]/.test(v), label: 'One uppercase letter' },
  { test: (v) => /[a-z]/.test(v), label: 'One lowercase letter' },
  { test: (v) => /\d/.test(v), label: 'One number' },
  { test: (v) => /[^A-Za-z0-9]/.test(v), label: 'One special character' },
]

export function evaluatePassword(value) {
  return PASSWORD_RULES.map((rule) => ({ label: rule.label, passed: rule.test(value || '') }))
}

export function isPasswordStrong(value) {
  return PASSWORD_RULES.every((rule) => rule.test(value || ''))
}
