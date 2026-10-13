// Data the Overview page loads next to the shared overview poll.
import { useEffect, useRef, useState } from 'react'
import { apiRequest } from '../api.js'
import { daysUntilFull, seriesFor } from '../chartMath.js'

// GET ``path`` now and every ``seconds`` while the tab is visible. A null path loads nothing.
// Returns { data, error, reload }; data from a previous path is dropped as soon as the path changes.
export function usePolled(path, seconds) {
  const [state, setState] = useState({ path: null, data: null, error: '' })
  const reloadRef = useRef(() => {})
  useEffect(() => {
    if (!path) return undefined
    let active = true
    const load = () => apiRequest(path, { method: 'GET' })
      .then((data) => { if (active) setState({ path, data, error: '' }) })
      .catch((error) => { if (active) setState((current) => ({ path, data: current.path === path ? current.data : null, error: error.message || 'Could not load this.' })) })
    reloadRef.current = load
    load()
    const timer = seconds ? setInterval(() => { if (!document.hidden) load() }, seconds * 1000) : null
    return () => { active = false; if (timer) clearInterval(timer) }
  }, [path, seconds])
  const current = state.path === path
  return { data: current ? state.data : null, error: current ? state.error : '', reload: () => reloadRef.current() }
}

const FORECAST_TTL_MS = 10 * 60 * 1000
const forecastCache = new Map()   // "serverId:mount" -> { days, loadedAt }
export const forecastKey = (serverId, mount) => `${serverId}:${mount}`

// Days until each asked-for filesystem is full, from its last 30 days: { "serverId:mount": days | null }.
// Slow-moving, so each one is asked for at most every ten minutes.
export function useForecasts(wanted) {
  const [, bump] = useState(0)
  const signature = wanted.map((item) => forecastKey(item.serverId, item.mount)).sort().join('|')
  useEffect(() => {
    let active = true
    for (const item of wanted) {
      const key = forecastKey(item.serverId, item.mount)
      const cached = forecastCache.get(key)
      if (cached && Date.now() - cached.loadedAt < FORECAST_TTL_MS) continue
      forecastCache.set(key, { days: cached?.days ?? null, loadedAt: Date.now() })
      apiRequest(`/monitoring/servers/${item.serverId}/mounts/history?mount=${encodeURIComponent(item.mount)}&range=30d&metrics=used_pct`, { method: 'GET' })
        .then((result) => {
          const series = seriesFor(result.points, 'used_pct').filter((point) => point.avg !== null).map((point) => ({ t: point.t, value: point.avg }))
          forecastCache.set(key, { days: daysUntilFull(series), loadedAt: Date.now() })
          if (active) bump((value) => value + 1)
        })
        .catch(() => { forecastCache.delete(key) })
    }
    return () => { active = false }
    // The list is rebuilt every render; its contents are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])
  return Object.fromEntries(wanted.map((item) => { const key = forecastKey(item.serverId, item.mount); return [key, forecastCache.get(key)?.days ?? null] }))
}
