// One shared poll of /monitoring/overview for whoever is on screen: the sidebar on every page, the Overview page
// itself. It runs at the fastest pace any mounted reader asks for, and not at all while the tab is hidden.
import { useEffect, useSyncExternalStore } from 'react'
import { apiRequest } from './api.js'

let snapshot = { data: null, error: '', loadedAt: 0 }
const listeners = new Set()
const readers = new Map()   // token -> { seconds, onUnauthorized }
let timer = null
let timerSeconds = null
let inFlight = null

function publish(next) {
  snapshot = { ...snapshot, ...next }
  for (const listener of listeners) listener()
}

export function refreshOverview() {
  if (inFlight) return inFlight
  inFlight = apiRequest('/monitoring/overview', { method: 'GET' })
    .then((data) => publish({ data, error: '', loadedAt: Date.now() }))
    .catch((error) => {
      // Signed out or expired: every reader hands the user back to the sign-in page.
      if (error.status === 401) { for (const reader of readers.values()) reader.onUnauthorized?.(); return }
      publish({ error: error.message || 'Could not load monitoring data.' })
    })
    .finally(() => { inFlight = null })
  return inFlight
}

function reschedule() {
  const seconds = readers.size && !document.hidden ? Math.min(...[...readers.values()].map((reader) => reader.seconds)) : null
  if (seconds === timerSeconds) return
  if (timer !== null) clearInterval(timer)
  timer = seconds === null ? null : setInterval(refreshOverview, seconds * 1000)
  timerSeconds = seconds
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && readers.size) refreshOverview()
    reschedule()
  })
}

const subscribe = (listener) => { listeners.add(listener); return () => listeners.delete(listener) }
const getSnapshot = () => snapshot

// { data, error, loadedAt } kept fresh every ``seconds`` while the component is mounted and ``enabled``.
export function useOverview(enabled, seconds, onUnauthorized) {
  useEffect(() => {
    if (!enabled) return undefined
    const token = {}
    readers.set(token, { seconds, onUnauthorized })
    if (Date.now() - snapshot.loadedAt > seconds * 1000) refreshOverview()
    reschedule()
    return () => { readers.delete(token); reschedule() }
    // onUnauthorized is the app's sign-out handler; a new function each render must not restart the poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, seconds])
  return useSyncExternalStore(subscribe, getSnapshot)
}

// Forget what was loaded, so the next person to sign in on this browser never sees the last one's fleet.
export function resetOverview() {
  snapshot = { data: null, error: '', loadedAt: 0 }
  for (const listener of listeners) listener()
}
