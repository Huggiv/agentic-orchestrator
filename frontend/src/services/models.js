/**
 * Singleton service for fetching available Copilot models.
 *
 * The API call is made at most once per browser session (module lifetime).
 * Subsequent calls return the cached result immediately without any network
 * round-trip — satisfying the "call once at container start" requirement.
 */

let _cachedModels = null
let _pendingPromise = null

/**
 * Returns the list of available models as `{ name, id }` objects.
 * Fetches from the backend on first call; returns cached data thereafter.
 *
 * @returns {Promise<Array<{name: string, id: string}>>}
 */
export async function getModels() {
  if (_cachedModels !== null) return _cachedModels

  if (_pendingPromise) return _pendingPromise

  _pendingPromise = fetch('/api/models')
    .then((res) => {
      if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`)
      return res.json()
    })
    .then((data) => {
      _cachedModels = Array.isArray(data.models) ? data.models : []
      return _cachedModels
    })
    .catch(() => {
      _cachedModels = []
      return _cachedModels
    })
    .finally(() => {
      _pendingPromise = null
    })

  return _pendingPromise
}
