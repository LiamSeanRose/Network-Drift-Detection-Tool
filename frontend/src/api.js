// api.js — API-key storage and an authenticated fetch wrapper (v3.5).
//
// The backend requires an X-API-Key header on all mutating requests
// (POST/PUT/PATCH/DELETE); read-only GETs are public. For a self-hosted
// dashboard the simplest fit is: the operator pastes a key (minted via
// `driftcheck create-api-key`), we keep it in localStorage, and attach it to
// mutating requests. GET requests are sent unchanged.

const KEY_STORAGE = 'netdrift_api_key'
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export function getApiKey() {
  return localStorage.getItem(KEY_STORAGE) || ''
}

export function setApiKey(key) {
  if (key) localStorage.setItem(KEY_STORAGE, key)
  else localStorage.removeItem(KEY_STORAGE)
}

// fetch wrapper that attaches the stored X-API-Key on mutating methods.
export function apiFetch(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const headers = { ...(options.headers || {}) }
  if (MUTATING.has(method)) {
    const key = getApiKey()
    if (key) headers['X-API-Key'] = key
  }
  return fetch(url, { ...options, headers })
}
