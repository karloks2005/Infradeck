// Pure helpers for the container cards: which need attention, and how to order them.

// 0 = needs attention, 1 = running but unhealthy, 2 = fine.
export function problemRank(container) {
  if (['exited', 'dead', 'restarting', 'paused', 'created'].includes(container.status)) return 0
  return container.healthcheck_status === 'unhealthy' ? 1 : 2
}

export function sortContainers(containers, sort) {
  const byName = (a, b) => a.name.localeCompare(b.name)
  const descending = (key) => (a, b) => (b[key] ?? -1) - (a[key] ?? -1) || byName(a, b)
  const order = { name: byName, memory: descending('memory_used_mb'), cpu: descending('cpu_pct'), problems: (a, b) => problemRank(a) - problemRank(b) || byName(a, b) }
  return [...containers].sort(order[sort] || byName)
}

