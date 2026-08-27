/**
 * Singleton service for fetching available Copilot models.
 *
 * The API call is made at most once per browser session (module lifetime).
 * Subsequent calls return the cached result immediately without any network
 * round-trip — satisfying the "call once at container start" requirement.
 */

let _cachedModels = null
let _pendingPromise = null

function asNonEmptyString(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

export function normalizeModels(rawModels) {
  if (!Array.isArray(rawModels)) return []

  const seen = new Set()
  const normalized = []

  rawModels.forEach((item) => {
    let id = ''
    let name = ''

    if (typeof item === 'string') {
      id = asNonEmptyString(item)
      name = id
    } else if (item && typeof item === 'object') {
      id =
        asNonEmptyString(item.id)
        || asNonEmptyString(item.value)
        || asNonEmptyString(item.model)
        || asNonEmptyString(item.slug)
      name =
        asNonEmptyString(item.name)
        || asNonEmptyString(item.label)
        || asNonEmptyString(item.display_name)
        || id
    }

    if (!id || seen.has(id)) return
    seen.add(id)
    normalized.push({ id, name })
  })

  return normalized
}

function fetchModels() {
  return fetch('/api/models')
    .then((res) => {
      if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`)
      return res.json()
    })
    .then((data) => {
      _cachedModels = normalizeModels(data.models)
      return _cachedModels
    })
    .catch(() => {
      _cachedModels = []
      return _cachedModels
    })
    .finally(() => {
      _pendingPromise = null
    })
}

/**
 * Returns the list of available models as `{ name, id }` objects.
 * Fetches from the backend on first call; returns cached data thereafter.
 *
 * @returns {Promise<Array<{name: string, id: string}>>}
 */
export async function getModels() {
  if (_cachedModels !== null) return _cachedModels

  if (_pendingPromise) return _pendingPromise

  _pendingPromise = fetchModels()

  return _pendingPromise
}

/**
 * Forces a fresh model fetch and replaces the in-memory cache.
 *
 * @returns {Promise<Array<{name: string, id: string}>>}
 */
export async function refreshModels() {
  if (_pendingPromise) return _pendingPromise
  _cachedModels = null
  _pendingPromise = fetchModels()
  return _pendingPromise
}
