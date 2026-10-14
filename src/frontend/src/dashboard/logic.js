// Pure helpers for the Overview page: what state things are in, how to group, order and word them. No React in here.
import { problemRank } from '../containerLogic.js'
import { isOnline } from '../fleetLogic.js'

// ---------- wording ----------

// 64%, 11.7%, 0.03%: one decimal, two below 1, and no trailing ".0".
export function formatPercent(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  if (number !== 0 && Math.abs(number) < 1) return `${Number(number.toFixed(2))}%`
  return `${Number(number.toFixed(1))}%`
}

// 2.93 GB, 393 MB, 26 MB from megabytes.
export function formatMegabytes(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(2)} TB`
  if (number >= 1024) return `${(number / 1024).toFixed(number >= 10240 ? 1 : 2)} GB`
  return `${Math.round(number)} MB`
}

// 27.2, 457, 7.3 with the unit left to the caller's sentence; TB above 1024 GB.
export function gigabyteParts(value) {
  if (value == null || !Number.isFinite(Number(value))) return { text: '?', unit: 'GB' }
  const number = Number(value)
  if (number >= 1024) return { text: String(Number((number / 1024).toFixed(1))), unit: 'TB' }
  return { text: String(Number(number.toFixed(number >= 100 ? 0 : 1))), unit: 'GB' }
}

// "27.2 / 31.3 GB", or "5.3 / 7.3 TB".
export function formatUsedOfTotal(usedGb, totalGb, separator = ' / ') {
  const total = gigabyteParts(totalGb)
  const used = total.unit === 'TB' ? String(Number((Number(usedGb) / 1024).toFixed(1))) : gigabyteParts(usedGb).text
  return `${usedGb == null ? '?' : used}${separator}${total.text} ${total.unit}`
}

// A span in the largest sensible unit: 45 s, 12 min, 2 h, 41 d.
export function formatSpan(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const value = Math.max(0, seconds)
  if (value < 60) return `${Math.round(value)} s`
  if (value < 3600) return `${Math.round(value / 60)} min`
  if (value < 86400) return `${Math.round(value / 3600)} h`
  return `${Math.round(value / 86400)} d`
}

export function formatAge(time, now = Date.now()) {
  const moment = new Date(time).getTime()
  return Number.isNaN(moment) ? '—' : formatSpan((now - moment) / 1000)
}

// How long it has been quiet: "2 h 14 min", "6 days".
export function formatQuiet(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const value = Math.max(0, seconds)
  if (value < 3600) return `${Math.max(1, Math.round(value / 60))} min`
  if (value < 86400) return `${Math.floor(value / 3600)} h ${Math.floor((value % 3600) / 60)} min`
  const days = Math.round(value / 86400)
  return `${days} ${days === 1 ? 'day' : 'days'}`
}

// "Full in about 6 months" in a few words, or null when there is no trend to speak of.
export function forecastPhrase(days, { short = false } = {}) {
  if (days == null || !Number.isFinite(days)) return null
  if (days <= 0) return short ? 'full now' : 'Full now'
  let amount
  if (days < 1) amount = short ? '<1 d' : 'less than a day'
  else if (days < 60) amount = short ? `${Math.round(days)} d` : `about ${Math.round(days)} ${Math.round(days) === 1 ? 'day' : 'days'}`
  else if (days < 730) amount = short ? `~${Math.round(days / 30)} mo` : `about ${Math.round(days / 30)} months`
  else if (days < 3650) amount = short ? `~${Number((days / 365).toFixed(1))} y` : `about ${Number((days / 365).toFixed(1))} years`
  else return null
  return short ? `full in ${amount}` : `Full in ${amount}`
}

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

// "full around April 2027".
export function forecastMonth(days, now = Date.now()) {
  if (days == null || !Number.isFinite(days) || days <= 0 || days >= 3650) return null
  const date = new Date(now + days * 86400000)
  return `full around ${MONTH_NAMES[date.getMonth()]} ${date.getFullYear()}`
}

// A total over several containers, where two decimals would be false precision: 2.9 GB, 361 MB.
export function formatMegabytesShort(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value) >= 1024 ? `${Number((Number(value) / 1024).toFixed(1))} GB` : `${Math.round(Number(value))} MB`
}

// ---------- servers ----------

// critical | warn | offline | ok, from what is open on the server.
export function serverState(server, now = Date.now()) {
  const open = server.alerts_by_severity || {}
  if (open.CRITICAL) return 'critical'
  if (server.last_seen_at && !isOnline(server, now)) return 'offline'
  if (open.WARNING) return 'warn'
  return 'ok'
}

export function serverStateLabel(server, now = Date.now()) {
  const open = server.alerts_by_severity || {}
  if (open.CRITICAL) return `${open.CRITICAL} critical`
  if (server.last_seen_at && !isOnline(server, now)) return 'offline'
  if (open.WARNING) return `${open.WARNING} ${open.WARNING === 1 ? 'warning' : 'warnings'}`
  if (!server.last_seen_at) return 'waiting'
  return 'healthy'
}

const STATE_RANK = { critical: 0, offline: 1, warn: 2, ok: 3 }
const byHostname = (a, b) => a.hostname.localeCompare(b.hostname, undefined, { sensitivity: 'base', numeric: true })

// Problems first while anything is open; by name once the fleet is healthy.
export function sortServers(servers, now = Date.now()) {
  return [...servers].sort((a, b) => STATE_RANK[serverState(a, now)] - STATE_RANK[serverState(b, now)] || byHostname(a, b))
}

export function serverMounts(server) {
  if (server.mounts?.length) return server.mounts
  const host = server.host
  return host?.disk_usage_pct != null ? [{ mount: '/', used_pct: host.disk_usage_pct, inodes_pct: host.disk_inodes_pct }] : []
}

export function fullestMount(mounts) {
  return mounts.reduce((fullest, mount) => (mount.used_pct != null && (!fullest || mount.used_pct > fullest.used_pct) ? mount : fullest), null)
}

// 0 fine, 1 warning, 2 critical, by the same thresholds the alert rules use.
export function thresholdLevel(value, warning, critical) {
  if (value == null) return 0
  return value >= critical ? 2 : value >= warning ? 1 : 0
}

export function mountLevel(mount, thresholds = {}, mountThresholds = {}) {
  const own = mountThresholds?.[mount.mount]
  return thresholdLevel(mount.used_pct, own?.disk_warning_pct ?? thresholds.disk_warning_pct ?? 85, own?.disk_critical_pct ?? thresholds.disk_critical_pct ?? 95)
}

// "Debian 12 · 8 cores · 31 GB" and "10.0.0.21 · up 41 d".
export function systemLines(server) {
  const first = [server.os_info?.replace(/ \((.*)\)$/, '').replace(/ LTS$/, ''), server.cpu_cores ? `${server.cpu_cores} cores` : null, server.total_ram_gb ? `${Math.round(server.total_ram_gb)} GB` : null].filter(Boolean)
  const uptime = server.host?.system_uptime_seconds
  const second = [server.ip_address, uptime != null ? `up ${formatSpan(uptime)}` : null].filter(Boolean)
  return [first.join(' · '), second.join(' · ')]
}

// The kernel release without the flavour: 6.8.0-84-generic -> 6.8.0-84.
export const shortKernel = (kernel) => (kernel ? kernel.replace(/-(generic|amd64|arm64|cloud|lowlatency|virtual|pve|default)$/i, '') : null)

// ---------- needs attention ----------

export const needsAttention = (alert) => alert.severity === 'CRITICAL' || alert.severity === 'WARNING'

// The one thing to do about an alert, chosen from the rule that raised it.
export function attentionAction(alert) {
  const rule = alert.rule_key || ''
  if (rule.startsWith('network:')) return { label: 'Review ports', kind: 'network' }
  if (alert.severity === 'CRITICAL') return { label: 'Open diagnosis', kind: 'agent' }
  if (rule.startsWith('host:') || rule.startsWith('trend:')) return { label: 'See trend', kind: 'trend' }
  if (rule.startsWith('container:')) return { label: 'Open container', kind: 'containers' }
  return { label: 'Open diagnosis', kind: 'agent' }
}

// ---------- ports ----------

// 9101, 9102, 9103, 9200 -> ["9101-9103", "9200"].
export function collapseRanges(numbers) {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b)
  const parts = []
  for (let index = 0; index < sorted.length; index += 1) {
    let end = index
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1
    parts.push(end > index ? `${sorted[index]}-${sorted[end]}` : String(sorted[index]))
    index = end
  }
  return parts
}

// "8080->80/tcp, 3478->3478/udp" -> [{ host: 8080, container: 80, proto: 'tcp' }, …], one per host port and protocol.
export function parsePorts(text) {
  const seen = new Set()
  const ports = []
  for (const part of (text || '').split(',')) {
    const match = part.trim().match(/^(\d+)->(\d+)\/(tcp|udp)$/)
    if (!match) continue
    const key = `${match[3]}/${match[1]}`
    if (seen.has(key)) continue
    seen.add(key)
    ports.push({ host: Number(match[1]), container: Number(match[2]), proto: match[3] })
  }
  return ports.sort((a, b) => a.host - b.host)
}

const DATABASE_PORTS = new Set([1433, 1521, 3306, 5432, 5984, 6379, 9042, 9200, 11211, 27017])
const DATABASE_IMAGES = /(mariadb|mysql|postgres|mongo|redis|valkey|couchdb|influxdb|clickhouse|memcached|elasticsearch|cassandra)/i
// Published ports of well-known admin panels, by what runs them.
const ADMIN_PANELS = [
  { image: /nginx-proxy-manager/i, ports: [81], reason: 'Admin login page' },
  { image: /portainer/i, ports: [9000, 9443], reason: 'Controls Docker' },
  { image: /portainer/i, ports: [8000], reason: 'Not needed without Edge', what: 'agent tunnel' },
  { image: /(pgadmin|phpmyadmin|adminer|mongo-express)/i, ports: null, reason: 'Database admin page' },
  { image: /(traefik)/i, ports: [8080], reason: 'Admin dashboard' },
]
const ADMIN_PORTS = new Map([[2375, 'Controls Docker, unencrypted'], [2376, 'Controls Docker'], [8006, 'Hypervisor admin page'], [9090, 'Admin page'], [10000, 'Admin page']])
const WELL_KNOWN = new Map([['tcp/22', 'ssh'], ['tcp/53', 'dns'], ['udp/53', 'dns'], ['tcp/80', 'web'], ['tcp/443', 'web'], ['udp/1194', 'VPN'], ['udp/51820', 'VPN'], ['udp/123', 'time'], ['tcp/25', 'mail'], ['udp/5353', 'mDNS'], ['tcp/631', 'printing']])

// What a listening port is, and whether it is something that usually shouldn't face the network.
// ``owner`` is the container publishing it, when one does. Risky means: a database or an admin panel.
export function classifyPort(proto, port, owner) {
  const image = owner?.image || ''
  if (owner) {
    for (const panel of ADMIN_PANELS) {
      if (panel.image.test(image) && (!panel.ports || panel.ports.includes(port))) return { what: panel.what || 'admin UI', risky: true, reason: panel.reason }
    }
    if (DATABASE_IMAGES.test(image) || (proto === 'tcp' && DATABASE_PORTS.has(port))) return { what: 'database', risky: true, reason: 'Databases rarely need this' }
    const known = WELL_KNOWN.get(`${proto}/${port}`)
    return { what: known === 'VPN' ? 'VPN' : 'web', risky: false, reason: known === 'web' ? 'Expected, public entry' : known ? 'Expected' : '' }
  }
  if (proto === 'tcp' && DATABASE_PORTS.has(port)) return { what: 'database', risky: true, reason: 'Databases rarely need this' }
  if (proto === 'tcp' && ADMIN_PORTS.has(port)) return { what: 'admin UI', risky: true, reason: ADMIN_PORTS.get(port) }
  return { what: WELL_KNOWN.get(`${proto}/${port}`) || '', risky: false, reason: '' }
}

// Every listening port of a server with its owner, where it is bound and what the firewall files say about it.
// Ports that only differ in protocol (tcp+udp) or form a run under one owner are merged into one row.
export function listenerRows(server) {
  const owners = new Map()
  for (const container of server.containers || []) {
    for (const port of parsePorts(container.ports)) owners.set(`${port.proto}/${port.host}`, container)
  }
  const allowed = new Map((server.system?.firewall_allowed || []).map((rule) => [`${rule.proto}/${rule.port}`, rule.file]))
  // From the optional port-owners helper. Docker's own port forwarder says nothing a container name doesn't.
  const processes = new Map((server.system?.listener_owners || []).filter((item) => item.process !== 'docker-proxy').map((item) => [`${item.proto}/${item.port}`, item.process]))
  const byPort = new Map()
  for (const listener of server.network?.listeners || []) {
    const key = String(listener.port)
    const entry = byPort.get(key) || { port: listener.port, protos: new Set(), scopes: new Set(), firstSeen: listener.first_seen }
    entry.protos.add(listener.proto)
    entry.scopes.add(listener.scope)
    if (listener.first_seen > entry.firstSeen) entry.firstSeen = listener.first_seen
    byPort.set(key, entry)
  }
  const rows = [...byPort.values()].map((entry) => {
    const protos = [...entry.protos].sort()
    const proto = protos[0]
    const owner = protos.map((item) => owners.get(`${item}/${entry.port}`)).find(Boolean) || null
    const scope = entry.scopes.has('any') ? 'any' : entry.scopes.has('specific') ? 'specific' : 'loopback'
    const kind = classifyPort(proto, entry.port, owner)
    const rule = protos.map((item) => allowed.get(`${item}/${entry.port}`)).find(Boolean) || null
    return {
      port: entry.port, ports: [entry.port], proto: protos.join('+'), scope, reachable: scope !== 'loopback', firstSeen: entry.firstSeen,
      owner: owner?.name || protos.map((item) => processes.get(`${item}/${entry.port}`)).find(Boolean) || (WELL_KNOWN.get(`${proto}/${entry.port}`) === 'ssh' ? 'sshd' : null), ownerKind: owner ? 'container' : 'host',
      what: kind.what, risky: kind.risky && scope !== 'loopback', reason: scope === 'loopback' ? '' : kind.reason, rule,
    }
  })
  // One row for a run of neighbouring ports (or 80 and 443) that share everything else.
  const merged = []
  for (const row of rows.sort((a, b) => a.port - b.port)) {
    const twin = merged.find((item) => item.owner && item.owner === row.owner && item.ownerKind === row.ownerKind && item.proto === row.proto && item.scope === row.scope && item.risky === row.risky && item.what === row.what && item.rule === row.rule && !item.risky)
    if (twin) twin.ports.push(row.port)
    else merged.push(row)
  }
  return merged.map((row) => ({ ...row, label: row.ports.length > 2 || (row.ports.length === 2 && row.ports[1] === row.ports[0] + 1) ? collapseRanges(row.ports).join(', ') : row.ports.join(', ') }))
}

// Risky and reachable first, then reachable, then local; by port within each.
export function sortListeners(rows, sort = 'risk') {
  const rank = (row) => (row.risky ? 0 : row.reachable ? 1 : 2)
  if (sort === 'port') return [...rows].sort((a, b) => a.port - b.port)
  if (sort === 'owner') return [...rows].sort((a, b) => (a.owner || '~').localeCompare(b.owner || '~') || a.port - b.port)
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.port - b.port)
}

// What the firewall configuration files say overall: { text, tone, enabled }.
export function firewallSummary(server) {
  const files = server.network?.firewall || []
  const ufw = files.find((file) => file.name === 'ufw-conf')
  const rules = files.filter((file) => file.name !== 'ufw-conf' && file.rules > 0)
  if (ufw?.enabled === false) return { short: 'ufw off', text: 'ufw inactive', tone: 'warn', enabled: false, rules }
  if (ufw?.enabled === true) return { short: 'ufw on', text: 'ufw active', tone: 'ok', enabled: true, rules }
  if (rules.length) return { short: rules[0].name, text: `${rules[0].name} rules`, tone: 'ok', enabled: true, rules }
  return { short: 'no config found', text: 'no config found', tone: 'unknown', enabled: null, rules }
}

// "ufw disabled · nftables.conf 12 rules".
export function firewallLine(server) {
  const summary = firewallSummary(server)
  const names = { ufw: 'ufw', ufw6: 'ufw6', nftables: 'nftables.conf', iptables: 'iptables', ip6tables: 'ip6tables' }
  const parts = [summary.enabled === false ? 'ufw disabled' : summary.enabled === true && summary.short === 'ufw on' ? 'ufw enabled' : null, ...summary.rules.map((file) => `${names[file.name] || file.name} ${file.rules} rules`)].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'no firewall config found'
}

// Port events in plain words, with repeats on the same port folded into one "Re-bound ×12" line.
// Events come newest first: [{ time, kind, detail }].
export function groupNetworkEvents(events, now = Date.now()) {
  const groups = []
  const byPort = new Map()
  for (const event of events) {
    const match = event.detail.match(/^(New listener|Listener closed): (tcp|udp)\/(\d+) \((.+)\)$/)
    if (!match) {
      groups.push({ time: event.time, label: event.kind === 'firewall_weakened' ? 'Firewall weaker' : event.kind === 'network_notice' ? 'Notice' : 'Firewall', tone: event.kind === 'firewall_weakened' ? 'warn' : 'plain', port: null, text: event.detail, count: 1, events: [event] })
      continue
    }
    const port = Number(match[3])
    const group = byPort.get(port) || { port, opened: [], closed: [], events: [], protos: new Set() }
    group.events.push(event)
    group.protos.add(match[2])
    ;(match[1] === 'New listener' ? group.opened : group.closed).push({ time: event.time, scope: match[4] })
    if (!byPort.has(port)) { byPort.set(port, group); groups.push(group) }
  }
  return groups.map((group) => {
    if (group.label) return { ...group, age: formatAge(group.time, now) }
    const times = group.events.map((event) => new Date(event.time).getTime())
    const newest = Math.max(...times)
    const oldest = Math.min(...times)
    const age = group.events.length > 1 && formatAge(oldest, now) !== formatAge(newest, now) ? `${formatAge(newest, now).replace(/ \w+$/, '')}–${formatAge(oldest, now)}` : formatAge(newest, now)
    const proto = [...group.protos].sort().join('+')
    const lastOpened = group.opened[0]
    const lastClosed = group.closed[0]
    const stillOpen = lastOpened && (!lastClosed || lastOpened.time >= lastClosed.time)
    const common = { time: group.events[0].time, age, port: `${proto}/${group.port}`, count: group.events.length, events: group.events }
    if (group.opened.length && group.closed.length) {
      const exposedNow = stillOpen && lastOpened.scope !== 'loopback only'
      const wasLocal = group.closed.some((item) => item.scope === 'loopback only')
      if (exposedNow && wasLocal) return { ...common, label: 'Now exposed', tone: 'warn', text: `moved from loopback to ${lastOpened.scope}` }
      return { ...common, label: group.events.length > 2 ? `Re-bound ×${group.events.length}` : 'Re-bound', tone: 'plain', text: stillOpen ? 'closed and opened again. Same exposure before and after.' : 'opened and closed again.' }
    }
    if (group.opened.length) return { ...common, label: lastOpened.scope === 'loopback only' ? 'Opened' : 'Now exposed', tone: lastOpened.scope === 'loopback only' ? 'plain' : 'warn', text: `now listens on ${lastOpened.scope.replace(' only', '')}` }
    return { ...common, label: 'Closed', tone: 'plain', text: 'stopped listening' }
  })
}

// ---------- containers ----------

export const STANDALONE = 'Standalone'

export function containerExposed(container, reachablePorts) {
  return parsePorts(container.ports).some((port) => !reachablePorts || reachablePorts.has(`${port.proto}/${port.host}`))
}

// The host ports other machines can reach, as "tcp/8080" keys; null when the server reports no listeners at all.
export function reachablePortSet(server) {
  const listeners = server.network?.listeners || []
  if (!listeners.length) return null
  return new Set(listeners.filter((listener) => listener.scope !== 'loopback').map((listener) => `${listener.proto}/${listener.port}`))
}

export const CONTAINER_FILTERS = {
  all: () => true,
  problems: (container) => problemRank(container) < 2,
  exposed: (container, reachable) => containerExposed(container, reachable),
  restarted: (container) => (container.restart_count || 0) > 0,
  stopped: (container) => ['exited', 'dead', 'created', 'paused'].includes(container.status),
}

const containerOrders = {
  cpu: (a, b) => (b.cpu_pct ?? -1) - (a.cpu_pct ?? -1),
  memory: (a, b) => (b.memory_used_mb ?? -1) - (a.memory_used_mb ?? -1),
  name: () => 0,
}
const byName = (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true })

// Containers grouped by compose project with totals; problems first inside a group, standalone containers last.
// ``sort`` orders both the groups (by their total) and the rows in them.
export function groupContainers(containers, { sort = 'cpu', grouped = true } = {}) {
  const order = containerOrders[sort] || containerOrders.cpu
  const rows = (items) => [...items].sort((a, b) => problemRank(a) - problemRank(b) || order(a, b) || byName(a, b))
  const total = (items, key) => items.reduce((sum, item) => sum + (item[key] ?? 0), 0)
  const describe = (name, items) => ({ name, containers: rows(items), count: items.length, cpu: total(items, 'cpu_pct'), memoryMb: total(items, 'memory_used_mb'), problems: items.filter((item) => problemRank(item) < 2).length })
  if (!grouped) return [describe(null, containers)]
  const byProject = new Map()
  for (const container of containers) {
    const key = container.project || STANDALONE
    if (!byProject.has(key)) byProject.set(key, [])
    byProject.get(key).push(container)
  }
  const groups = [...byProject.entries()].map(([name, items]) => describe(name, items))
  const groupOrder = sort === 'memory' ? (a, b) => b.memoryMb - a.memoryMb : sort === 'name' ? () => 0 : (a, b) => b.cpu - a.cpu
  return groups.sort((a, b) => (a.name === STANDALONE) - (b.name === STANDALONE) || groupOrder(a, b) || a.name.localeCompare(b.name))
}

// "/srv/gitlab/config, /srv/gitlab/data" -> "/srv/gitlab/{config,data}"; unrelated paths stay listed.
export function collapsePaths(text) {
  const paths = (text || '').split(',').map((path) => path.trim()).filter(Boolean)
  const byParent = new Map()
  const result = []
  for (const path of paths) {
    const cut = path.lastIndexOf('/')
    if (cut <= 0) { result.push({ parent: null, names: [path] }); continue }
    const parent = path.slice(0, cut)
    if (!byParent.has(parent)) { byParent.set(parent, { parent, names: [] }); result.push(byParent.get(parent)) }
    byParent.get(parent).names.push(path.slice(cut + 1))
  }
  return result.map((item) => (item.parent === null ? item.names[0] : item.names.length === 1 ? `${item.parent}/${item.names[0]}` : `${item.parent}/{${item.names.join(',')}}`))
}

export function containerUptime(startedAt, now = Date.now()) {
  const started = startedAt ? new Date(startedAt).getTime() : NaN
  return Number.isNaN(started) || started <= 0 ? null : formatSpan((now - started) / 1000)
}

// ---------- health checks ----------

const TAINT_FAULT_BITS = [4, 5, 7, 9, 14]
const DAY = 24 * 3600 * 1000

// The server panel's checklist: plain verdicts, amber only where something needs doing.
// Each: { key, label, text, tone: ok | warn | unknown, action? }.
export function healthChecks(server, now = Date.now()) {
  const host = server.host || {}
  const system = server.system || {}
  const events = server.system_events || []
  const checks = []
  const listeners = server.network?.listeners || []
  const exposed = new Set(listeners.filter((listener) => listener.scope !== 'loopback').map((listener) => listener.port)).size
  const firewall = firewallSummary(server)
  if (listeners.length || firewall.enabled !== null) {
    checks.push({ key: 'firewall', label: 'Firewall', text: `${firewall.short}${listeners.length ? ` · ${exposed} ${exposed === 1 ? 'port' : 'ports'} exposed` : ''}`, tone: firewall.tone === 'warn' ? 'warn' : firewall.tone === 'unknown' ? 'unknown' : 'ok' })
  } else checks.push({ key: 'firewall', label: 'Firewall', text: 'Not reported', tone: 'unknown' })

  const kernel = shortKernel(system.reboot_kernel)
  const rebootedAgo = host.system_uptime_seconds != null && host.system_uptime_seconds < DAY / 1000 ? ` · rebooted ${formatSpan(host.system_uptime_seconds)} ago` : ''
  if (host.reboot_required) checks.push({ key: 'updates', label: 'Updates', text: kernel ? `Reboot needed for ${kernel}` : 'Reboot needed', tone: 'warn' })
  else if (system.updates_security > 0) checks.push({ key: 'updates', label: 'Updates', text: `${system.updates_security} security ${system.updates_security === 1 ? 'update' : 'updates'} pending`, tone: 'warn' })
  else if (host.reboot_required === false) checks.push({ key: 'updates', label: 'Updates', text: `${system.updates_pending > 0 ? `${system.updates_pending} pending, none security` : 'Up to date'}${rebootedAgo}`, tone: 'ok' })
  else checks.push({ key: 'updates', label: 'Updates', text: 'Not reported on this system', tone: 'unknown' })

  if (host.swap_pct != null && host.swap_pct >= 10) checks.push({ key: 'swap', label: 'Swap', text: `${formatPercent(host.swap_pct)} in use`, tone: host.swap_pct >= 25 ? 'warn' : 'ok' })

  if (host.system_uptime_seconds == null) checks.push({ key: 'booted', label: 'Booted', text: 'Unknown', tone: 'unknown' })
  else {
    const shutdown = host.last_shutdown === 'clean' ? ' · clean shutdown' : host.last_shutdown === 'unclean' ? ' · unclean shutdown' : ''
    checks.push({ key: 'booted', label: 'Booted', text: `${formatSpan(host.system_uptime_seconds)} ago${shutdown}`, tone: host.last_shutdown === 'unclean' ? 'warn' : 'ok' })
  }

  const service = system.time_service ? ` (${system.time_service.replace('systemd-', '')})` : ''
  checks.push({ key: 'clock', label: 'Clock', text: host.ntp_synced == null ? 'Unknown' : host.ntp_synced ? `Synced${service}` : 'Not synced', tone: host.ntp_synced == null ? 'unknown' : host.ntp_synced ? 'ok' : 'warn' })

  const ooms = events.filter((event) => event.kind === 'oom_kill' && now - new Date(event.time).getTime() < DAY).length
  checks.push({ key: 'oom', label: 'OOM kills', text: host.oom_kill_total == null ? 'Unknown' : ooms ? `${ooms} in 24 h` : 'None in 24 h', tone: host.oom_kill_total == null ? 'unknown' : ooms ? 'warn' : 'ok' })

  const faulty = host.kernel_tainted != null && TAINT_FAULT_BITS.some((bit) => (host.kernel_tainted >> bit) & 1)
  const kernelErrors = events.filter((event) => (event.kind === 'kernel_error' || event.kind === 'kernel_fault') && now - new Date(event.time).getTime() < DAY).length
  checks.push({ key: 'kernel', label: 'Kernel log', text: host.kernel_tainted == null ? 'Unknown' : faulty ? 'Faults reported' : kernelErrors ? `${kernelErrors} ${kernelErrors === 1 ? 'error' : 'errors'} in 24 h` : 'Clean', tone: host.kernel_tainted == null ? 'unknown' : faulty || kernelErrors ? 'warn' : 'ok' })

  const internet = { ok: ['Reachable', 'ok'], degraded: ['Partly reachable', 'warn'], dns_failed: ['DNS failing', 'warn'], down: ['Down', 'warn'] }[host.internet_status]
  if (internet) checks.push({ key: 'internet', label: 'Internet', text: `${internet[0]}${host.internet_latency_ms != null && internet[1] === 'ok' ? ` · ${host.internet_latency_ms} ms` : ''}`, tone: internet[1] })
  else checks.push({ key: 'internet', label: 'Internet', text: 'Not checked', tone: 'unknown', action: 'set targets' })

  if (host.docker_status === 'unreachable') checks.push({ key: 'docker', label: 'Docker', text: 'Unreachable', tone: 'warn' })
  return checks
}

export function healthVerdict(checks) {
  const warnings = checks.filter((check) => check.tone === 'warn').length
  return warnings ? `${warnings} to look at` : 'all passing'
}

// ---------- charts and history ----------

// MB/s in and out between consecutive check-ins, from running totals. A counter that went down (a reboot) is a gap.
export function trafficRates(samples) {
  const points = []
  for (let index = 1; index < samples.length; index += 1) {
    const before = samples[index - 1]
    const after = samples[index]
    const seconds = (new Date(after.time).getTime() - new Date(before.time).getTime()) / 1000
    const rate = (key) => (seconds > 0 && after[key] != null && before[key] != null && after[key] >= before[key] ? (after[key] - before[key]) / seconds : null)
    points.push({ time: after.time, in: rate('net_recv_mb'), out: rate('net_sent_mb') })
  }
  return points
}

// A round ceiling for a chart axis: 0.4 -> 0.5, 2.1 -> 4, 37 -> 50.
export function niceCeiling(value) {
  if (!Number.isFinite(value) || value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  for (const step of [1, 2, 4, 5, 6, 8, 10]) if (value <= step * magnitude) return Number((step * magnitude).toPrecision(6))
  return 10 * magnitude
}

// The last boots, oldest first: [{ time, unclean }], from reboot events (newest first).
export function bootHistory(events, limit = 10) {
  return events.filter((event) => event.kind === 'reboot').slice(0, limit).map((event) => ({ time: event.time, unclean: /unclean/i.test(event.detail) })).reverse()
}

// ---------- the event timeline ----------

export const EVENT_SOURCES = { alert: 'Alerts', container: 'Containers', network: 'Network', system: 'System' }

export function eventCategory(event) {
  return event.source === 'collector' || event.source === 'infradeck' ? 'system' : event.source
}

const SOURCE_NAMES = { alert: 'Alert', container: 'Container', network: 'Network', system: 'System', collector: 'Collector', infradeck: 'Infradeck' }

// One readable line per event: { source, tone, lead, text, tag }.
export function describeEvent(event) {
  const base = { source: SOURCE_NAMES[event.source] || event.source, tone: 'plain', lead: '', text: event.detail || '', tag: null }
  switch (event.kind) {
    case 'alert_opened':
      return { ...base, tone: event.severity === 'CRITICAL' ? 'signal' : event.severity === 'WARNING' ? 'warn' : 'plain', lead: event.title, text: '', tag: event.resolved ? { text: 'resolved', tone: 'ok' } : event.severity === 'CRITICAL' ? { text: 'critical', tone: 'signal' } : event.severity === 'WARNING' ? { text: 'open', tone: 'warn' } : null }
    case 'alert_resolved':
      return { ...base, source: 'Alert resolved', tone: 'ok', lead: event.title, text: `cleared after ${formatSpan(event.duration_seconds)}` }
    case 'collector_stopped':
      return { ...base, tone: 'signal', lead: 'Stopped reporting', text: '· several check-ins missed', tag: { text: 'critical', tone: 'signal' } }
    case 'collector_resumed':
      return { ...base, tone: 'ok', lead: 'Reporting again', text: `after ${formatSpan(event.duration_seconds)}` }
    case 'pending_collector':
      return { ...base, tone: 'paper', lead: event.title, text: event.detail }
    case 'port_opened': {
      const exposed = !/loopback/.test(event.detail)
      const match = event.detail.match(/(tcp|udp)\/\d+/)
      return { ...base, tone: exposed ? 'warn' : 'plain', lead: match ? match[0] : '', text: event.detail.replace(/^New listener: (tcp|udp)\/\d+ /, 'started listening '), tag: exposed ? { text: 'exposed', tone: 'warn' } : null }
    }
    case 'port_closed': {
      const match = event.detail.match(/(tcp|udp)\/\d+/)
      return { ...base, lead: match ? match[0] : '', text: event.detail.replace(/^Listener closed: (tcp|udp)\/\d+ /, 'stopped listening ') }
    }
    case 'firewall_weakened':
      return { ...base, tone: 'warn', tag: { text: 'weaker', tone: 'warn' } }
    case 'oom_kill':
    case 'kernel_fault':
    case 'kernel_error':
    case 'credentials_replaced':
      return { ...base, tone: 'warn' }
    case 'reboot':
      return { ...base, tone: /unclean/i.test(event.detail) ? 'warn' : 'plain', tag: /unclean/i.test(event.detail) ? { text: 'unclean', tone: 'warn' } : null }
    default: {
      const match = event.detail.match(/^Container (\S+) (.*)$/)
      return match ? { ...base, lead: match[1], text: match[2] } : base
    }
  }
}

// Folds runs of the same kind of event from the same source in the same hour into one row with the rest inside it.
// Alerts are never folded: each one is its own story.
export function groupRepeats(events) {
  const groups = []
  for (const event of events) {
    const last = groups[groups.length - 1]
    const hour = event.time.slice(0, 13)
    if (last && event.source !== 'alert' && event.source !== 'collector' && last.source === event.source && last.kind === event.kind && last.hour === hour) last.events.push(event)
    else groups.push({ source: event.source, kind: event.kind, hour, events: [event] })
  }
  return groups
}

const PLURAL_SOURCES = { container: 'Containers', network: 'Network', system: 'System' }

// A folded group told in one line.
export function describeGroup(group) {
  const first = describeEvent(group.events[0])
  if (group.events.length === 1) return { ...first, count: 1 }
  const leads = [...new Set(group.events.map((event) => describeEvent(event).lead).filter(Boolean))]
  const actions = { container_added: 'appeared', container_removed: 'were removed', image_changed: 'now run a different image build', port_opened: 'started listening', port_closed: 'stopped listening' }
  const shown = leads.slice(0, 3).join(', ')
  const rest = leads.length > 3 ? ` and ${leads.length - 3} more` : ''
  return { ...first, source: `${PLURAL_SOURCES[group.source] || first.source} ×${group.events.length}`, lead: `${shown}${rest}`, text: actions[group.kind] || first.text, count: group.events.length, tag: first.tag }
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const dayKey = (date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`

// "TODAY · MON 13 OCT" / "SUN 12 OCT".
export function dayHeading(time, now = Date.now()) {
  const date = new Date(time)
  const label = `${WEEKDAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]}`.toUpperCase()
  return dayKey(date) === dayKey(new Date(now)) ? `TODAY · ${label}` : label
}

export const BARS_PER_DAY = 5

// The week on one strip: per day a few bars (how much happened, and whether any of it was an alert).
export function eventStrip(events, days = 7, now = Date.now()) {
  const today = new Date(now)
  today.setHours(0, 0, 0, 0)
  const strip = []
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const start = new Date(today.getTime())
    start.setDate(start.getDate() - offset)
    strip.push({ key: dayKey(start), start: start.getTime(), label: offset === 0 ? 'Today' : `${WEEKDAYS[start.getDay()]} ${start.getDate()}`, today: offset === 0, count: 0, bars: Array.from({ length: BARS_PER_DAY }, () => ({ count: 0, tone: 'plain' })) })
  }
  const byKey = new Map(strip.map((day) => [day.key, day]))
  for (const event of events) {
    const date = new Date(event.time)
    const day = byKey.get(dayKey(date))
    if (!day) continue
    day.count += 1
    const bar = day.bars[Math.min(BARS_PER_DAY - 1, Math.floor((date.getHours() * 60 + date.getMinutes()) / (1440 / BARS_PER_DAY)))]
    bar.count += 1
    const opening = event.kind === 'alert_opened' || event.kind === 'collector_stopped'
    if (opening && event.severity === 'CRITICAL') bar.tone = 'signal'
    else if (opening && event.severity === 'WARNING' && bar.tone !== 'signal') bar.tone = 'warn'
  }
  const peak = Math.max(1, ...strip.flatMap((day) => day.bars.map((bar) => bar.count)))
  return strip.map((day) => ({ ...day, bars: day.bars.map((bar) => ({ ...bar, height: bar.count ? Math.max(0.12, bar.count / peak) : 0 })) }))
}

export function eventsToCsv(events) {
  const quote = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`
  const lines = [['time', 'source', 'kind', 'severity', 'title', 'detail'].join(',')]
  for (const event of events) lines.push([event.time, event.source, event.kind, event.severity || '', event.title || '', event.detail || ''].map(quote).join(','))
  return lines.join('\n')
}

// ---------- adding a server ----------

// The same two hardened containers (collector plus read-only Docker proxy), three ways to start them.
export function installCommands({ url, token, name, collectorImage, proxyImage }) {
  const nameLine = name ? `\n      INFRADECK_SERVER_NAME: ${name}` : ''
  const compose = `services:
  collector:
    image: ${collectorImage}
    container_name: infradeck-collector
    restart: unless-stopped
    environment:
      INFRADECK_API_URL: ${url}
      INFRADECK_JOIN_TOKEN: ${token}${nameLine}
      INFRADECK_DOCKER_HOST: /run/docker-proxy/docker.sock
    volumes:
      - /proc:/host/proc:ro
      - /:/hostfs:ro
      - collector_state:/var/lib/infradeck-collector
      - docker_proxy_socket:/run/docker-proxy:ro
    cap_drop: [ALL]
    read_only: true
    security_opt: [no-new-privileges:true]
  docker-proxy:
    image: ${proxyImage}
    container_name: infradeck-docker-proxy
    restart: unless-stopped
    user: "0:0"
    network_mode: none
    cap_drop: [ALL]
    read_only: true
    security_opt: [no-new-privileges:true]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - docker_proxy_socket:/run/docker-proxy
volumes:
  collector_state:
  docker_proxy_socket:`
  const hardened = '--restart unless-stopped --cap-drop ALL --read-only --security-opt no-new-privileges:true'
  const nameFlag = name ? ` \\\n  -e INFRADECK_SERVER_NAME=${name}` : ''
  const dockerRun = `docker run -d --name infradeck-docker-proxy ${hardened} \\
  --user 0:0 --network none \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  -v infradeck_docker_proxy_socket:/run/docker-proxy \\
  ${proxyImage}
docker run -d --name infradeck-collector ${hardened} \\
  -e INFRADECK_API_URL=${url} \\
  -e INFRADECK_JOIN_TOKEN=${token}${nameFlag} \\
  -e INFRADECK_DOCKER_HOST=/run/docker-proxy/docker.sock \\
  -v /proc:/host/proc:ro -v /:/hostfs:ro \\
  -v infradeck_collector_state:/var/lib/infradeck-collector \\
  -v infradeck_docker_proxy_socket:/run/docker-proxy:ro \\
  ${collectorImage}`
  const systemd = `sudo mkdir -p /etc/infradeck-collector
sudo tee /etc/infradeck-collector/compose.yml >/dev/null <<'EOF'
${compose}
EOF
sudo tee /etc/systemd/system/infradeck-collector.service >/dev/null <<'EOF'
[Unit]
Description=Infradeck collector
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker compose -p infradeck-collector -f /etc/infradeck-collector/compose.yml up -d
ExecStop=/usr/bin/docker compose -p infradeck-collector -f /etc/infradeck-collector/compose.yml down

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now infradeck-collector`
  return { 'docker run': dockerRun, compose: `docker compose -p infradeck-collector -f - up -d <<'EOF'\n${compose}\nEOF`, systemd }
}
