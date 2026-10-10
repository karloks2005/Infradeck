// Pure helpers for the all-servers view: what to count, rank and label. No React in here.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (value) => String(value).padStart(2, '0')

export const CONTAINER_STATES = [
  { key: 'running', label: 'Running', color: '#34d399' },
  { key: 'unhealthy', label: 'Unhealthy', color: '#f87171' },
  { key: 'restarting', label: 'Restarting', color: '#fbbf24' },
  { key: 'stopped', label: 'Stopped', color: '#94a3b8' },
]

export function containerState(container) {
  if (container.status === 'running') return container.healthcheck_status === 'unhealthy' ? 'unhealthy' : 'running'
  return container.status === 'restarting' ? 'restarting' : 'stopped'
}

export function containerStateSlices(servers) {
  const counts = { running: 0, unhealthy: 0, restarting: 0, stopped: 0 }
  for (const server of servers) for (const container of server.containers || []) counts[containerState(container)] += 1
  return CONTAINER_STATES.map((state) => ({ label: state.label, color: state.color, value: counts[state.key] }))
}

// The containers using the most of something right now, across all servers: [{label: 'server / name', value}].
export function topContainers(servers, key, count = 10) {
  const rows = []
  for (const server of servers) {
    for (const container of server.containers || []) {
      const value = container[key]
      if (typeof value === 'number' && Number.isFinite(value)) rows.push({ label: `${server.hostname} / ${container.name}`, value })
    }
  }
  return rows.sort((a, b) => b.value - a.value || a.label.localeCompare(b.label)).slice(0, count)
}

// Servers by how full their root disk is, coloured the way the dashboard colours it.
export function serversByDisk(servers, warning = 85, critical = 95) {
  return servers
    .filter((server) => typeof server.host?.disk_usage_pct === 'number')
    .map((server) => ({ label: server.hostname, value: server.host.disk_usage_pct, color: server.host.disk_usage_pct >= critical ? '#f87171' : server.host.disk_usage_pct >= warning ? '#fbbf24' : '#7dd3fc' }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
}

export function isOnline(server, now = Date.now()) {
  if (!server.last_seen_at) return false
  return now - new Date(server.last_seen_at).getTime() < (server.check_interval_seconds || 60) * 3 * 1000
}

export function availabilityRows(availability) {
  return availability
    .filter((item) => item.percent !== null && item.percent !== undefined)
    .map((item) => ({ label: item.hostname, value: item.percent, color: item.percent >= 99 ? '#34d399' : item.percent >= 95 ? '#fbbf24' : '#f87171', detail: `${item.checkins} of about ${item.expected} check-ins` }))
    .sort((a, b) => a.value - b.value || a.label.localeCompare(b.label))
}

export function formatDuration(minutes) {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return '–'
  if (minutes < 1) return 'under a minute'
  if (minutes < 120) return `${Math.round(minutes)} min`
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(1)} h`
  return `${(minutes / 1440).toFixed(1)} days`
}

function startOfBucket(bucket, time) {
  const date = new Date(time)
  if (bucket === 'hour') return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), date.getUTCHours())
  const day = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
  if (bucket === 'day') return day
  if (bucket === 'week') return day - ((date.getUTCDay() + 6) % 7) * 86400000
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)
}

function nextBucket(bucket, start) {
  if (bucket === 'hour') return start + 3600000
  if (bucket === 'day') return start + 86400000
  if (bucket === 'week') return start + 7 * 86400000
  const date = new Date(start)
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1)
}

export function bucketLabel(time, bucket, utcHours = false) {
  const date = new Date(time)
  if (bucket === 'hour') return `${pad(utcHours ? date.getUTCHours() : date.getHours())}:00`
  if (bucket === 'month') return `${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`
}

// One column for every bucket from ``start`` to ``end``, with zeros where nothing happened, so a quiet day shows as
// quiet instead of missing. At most ``limit`` (the newest) buckets are returned.
export function incidentColumns(byBucket, bucket, start, end, limit = 120) {
  const known = new Map(byBucket.map((entry) => [startOfBucket(bucket, new Date(entry.time).getTime()), entry]))
  const columns = []
  for (let moment = startOfBucket(bucket, new Date(start).getTime()); moment <= new Date(end).getTime(); moment = nextBucket(bucket, moment)) {
    const entry = known.get(moment)
    columns.push({ time: moment, values: { critical: entry?.critical ?? 0, warning: entry?.warning ?? 0, info: entry?.info ?? 0 } })
  }
  return columns.slice(-limit)
}
