// The one way the pages talk to the backend.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'
// Vite only exposes VITE_-prefixed env vars; clamp so a bad value can't flood the API.
export const LIVE_REFRESH_SECONDS = Math.max(Number(import.meta.env.VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS) || 3, 1)

export async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  })

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}))
    const detail = errorBody.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Request failed.')
    error.status = response.status
    throw error
  }

  return response.json()
}
