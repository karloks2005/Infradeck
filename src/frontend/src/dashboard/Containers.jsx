// Containers grouped by compose project: the summary card under a server's Overview and the full Containers tab.
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { problemRank } from '../containerLogic.js'
import { Caret, Icon } from '../icons.jsx'
import { LineChart, Meter, Sparkline } from './charts.jsx'
import {
  CONTAINER_FILTERS, STANDALONE, classifyPort, collapsePaths, collapseRanges, containerUptime, formatMegabytes, formatMegabytesShort, formatPercent, formatSpan, groupContainers, niceCeiling, parsePorts, reachablePortSet,
} from './logic.js'

// Tablets drop the Image column; it comes back from 1200 px.
const COLUMNS = 'grid-cols-[196px_128px_150px_161px_minmax(150px,1fr)_52px_72px] xl:grid-cols-[196px_minmax(140px,1.1fr)_128px_150px_161px_minmax(150px,1fr)_52px_72px]'
const READ_ONLY_NOTE = 'Infradeck only reads from Docker for now, so it cannot fetch logs or restart a container yet.'
const FILTER_LABELS = { all: 'All', problems: 'Problems', exposed: 'Exposed', restarted: 'Restarted', stopped: 'Stopped' }
const APP_PROJECT = /infradeck|homie/i

function statusTone(container) {
  if (container.status === 'running') return container.healthcheck_status === 'unhealthy' ? 'text-signal' : 'text-ok'
  return container.status === 'restarting' ? 'text-signal' : 'text-warn'
}

function StatusText({ container }) {
  const stopped = container.status === 'exited' || container.status === 'dead'
  const extra = stopped && container.exit_code != null ? (container.oom_killed ? 'out of memory' : `code ${container.exit_code}`) : container.healthcheck_status
  return <span className="block truncate text-xs" title={[container.status, extra].filter(Boolean).join(' · ')}><span className={statusTone(container)}>{container.status}</span>{extra && <span className={extra === 'unhealthy' || extra === 'out of memory' ? 'text-signal' : 'text-muted'}> · {extra}</span>}</span>
}

// Published ports as chips; databases and admin panels that other machines can reach are amber.
function PortChips({ container, scopes }) {
  const ports = parsePorts(container.ports)
  if (!ports.length) return <span className="text-xs text-muted">none</span>
  const byHost = new Map()
  for (const port of ports) {
    const entry = byHost.get(port.host) || { host: port.host, protos: [] }
    entry.protos.push(port.proto)
    byHost.set(port.host, entry)
  }
  const chips = []
  for (const entry of byHost.values()) {
    const scope = entry.protos.map((proto) => scopes.get(`${proto}/${entry.host}`)).find(Boolean)
    const risky = scope !== 'loopback' && entry.protos.some((proto) => classifyPort(proto, entry.host, container).risky)
    const proto = entry.protos.length > 1 ? 'tcp+udp' : entry.protos[0] === 'udp' ? 'udp' : ''
    const last = chips[chips.length - 1]
    if (last && !risky && !last.risky && last.proto === proto && last.hosts[last.hosts.length - 1] === entry.host - 1) last.hosts.push(entry.host)
    else chips.push({ hosts: [entry.host], proto, risky, note: risky && scope === 'any' ? 'all interfaces' : '' })
  }
  const shown = chips.slice(0, 3)
  return (
    <span className="flex min-w-0 items-center gap-1 overflow-hidden">
      {shown.map((chip) => <span key={chip.hosts[0]} className={`port ${chip.risky ? 'port-risky' : ''}`} title={chip.risky ? 'A database or admin panel that other machines can reach' : undefined}>{collapseRanges(chip.hosts).join(', ')}{(chip.proto || chip.note) && <span>{[chip.proto, chip.note].filter(Boolean).join(' ')}</span>}</span>)}
      {chips.length > shown.length && <span className="text-[11px] text-muted">+{chips.length - shown.length}</span>}
    </span>
  )
}

function Facts({ items }) {
  return <dl className="kv m-0" style={{ '--kv-label': '72px' }}>{items.map(([label, value, mono]) => <div key={label} className="contents"><dt>{label}</dt><dd className={`break-words text-paper ${mono ? 'mono break-all' : ''}`} title={typeof value === 'string' ? value : undefined}>{value}</dd></div>)}</dl>
}

function ContainerDetails({ server, container }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  // Re-fetch whenever the overview poll brings in a newer sample for this container.
  const sampleTime = container.time

  useEffect(() => {
    let active = true
    apiRequest(`/monitoring/servers/${server.id}/containers/${encodeURIComponent(container.container_id)}?limit=60`, { method: 'GET' })
      .then((data) => { if (active) { setDetail(data); setError('') } })
      .catch((requestError) => { if (active) setError(requestError.message || 'Could not load container details.') })
    return () => { active = false }
  }, [server.id, container.container_id, sampleTime])

  if (error) return <div className="border-l-2 border-warn bg-bg px-[18px] py-4 text-xs text-warn">{error}</div>
  if (!detail) return <div className="border-l-2 border-paper bg-bg px-[18px] py-4 text-xs text-muted">Loading container details…</div>

  const latest = detail.latest
  const history = detail.history
  const hostMb = (server.total_ram_gb || 0) * 1024
  const limited = latest.memory_limit_mb && (!hostMb || latest.memory_limit_mb < hostMb * 0.98)
  const span = history.length > 1 ? (new Date(history[history.length - 1].time) - new Date(history[0].time)) / 1000 : 0
  const window = span ? `last ${formatSpan(span)}` : 'recent'
  const cpuMax = Math.max(100, niceCeiling(Math.max(0, ...history.map((sample) => sample.cpu_pct ?? 0))))
  const memoryPeak = Math.max(1, ...history.map((sample) => sample.memory_used_mb ?? 0))
  const inGb = memoryPeak >= 1024
  const memoryMax = niceCeiling((inGb ? memoryPeak / 1024 : memoryPeak) * 1.3)
  const volumes = collapsePaths(detail.volumes)
  const checked = formatSpan((Date.now() - new Date(latest.time).getTime()) / 1000)
  return (
    <div className="grid border-l-2 border-paper bg-bg lg:grid-cols-[312px_minmax(0,1fr)_minmax(0,1fr)] lg:divide-x lg:divide-line">
      <div className="min-w-0 px-[18px] py-4">
        <Facts items={[
          ['ID', detail.container_id.slice(0, 12), true],
          ['Health', latest.healthcheck_status ? `${latest.healthcheck_status} · checked ${checked} ago` : `no healthcheck · seen ${checked} ago`],
          ['Limit', limited ? formatMegabytes(latest.memory_limit_mb) : `none${hostMb ? ` (can use ${Number(server.total_ram_gb.toFixed(1))} GB)` : ''}`],
          ['Processes', latest.pids ?? '—'],
          ['Volumes', volumes.length ? volumes.join(', ') : 'none', true],
        ]} />
        <div className="mt-4 grid grid-cols-2 gap-x-4">
          <div><div className="text-[11px] text-muted">Network in / out</div><div className="mt-1 whitespace-nowrap text-[13px] font-semibold text-paper">{formatMegabytes(latest.net_rx_mb)} / {formatMegabytes(latest.net_tx_mb)}</div></div>
          <div><div className="text-[11px] text-muted">Disk read / write</div><div className="mt-1 whitespace-nowrap text-[13px] font-semibold text-paper">{formatMegabytes(latest.block_read_mb)} / {formatMegabytes(latest.block_write_mb)}</div></div>
        </div>
        <div className="mt-4 flex flex-wrap gap-1.5">
          <button type="button" className="btn btn-sm" disabled title={READ_ONLY_NOTE}><Icon name="logs" size={13} />Logs</button>
          <button type="button" className="btn btn-sm" disabled title={READ_ONLY_NOTE}><Icon name="restart" size={13} />Restart</button>
          <Link to="/agent" className="btn btn-sm"><Icon name="agent" size={13} />Ask the agent</Link>
        </div>
      </div>
      <div className="min-w-0 px-[18px] py-4">
        <div className="mb-3 flex items-baseline justify-between"><span className="text-xs font-medium text-soft">CPU · {window}</span><span className="text-[15px] font-semibold text-paper">{formatPercent(latest.cpu_pct)}</span></div>
        <LineChart label={`CPU use of ${detail.name}`} points={history.map((sample) => ({ time: sample.time, value: sample.cpu_pct }))} max={cpuMax} ticks={[cpuMax, cpuMax / 2, 0]} format={(tick) => `${tick}%`} area height={92} />
        <p className="mb-0 mt-3 text-[11px] text-muted">Can pass 100% when the container uses more than one core.</p>
      </div>
      <div className="min-w-0 px-[18px] py-4">
        <div className="mb-3 flex items-baseline justify-between"><span className="text-xs font-medium text-soft">Memory · {window}</span><span className="text-[15px] font-semibold text-paper">{formatMegabytes(latest.memory_used_mb)}</span></div>
        <LineChart label={`Memory use of ${detail.name}`} points={history.map((sample) => ({ time: sample.time, value: sample.memory_used_mb == null ? null : inGb ? sample.memory_used_mb / 1024 : sample.memory_used_mb }))} max={memoryMax} ticks={[memoryMax, memoryMax / 2, 0]} format={(tick) => `${Number(tick.toFixed(1))} ${inGb ? 'GB' : 'MB'}`} area height={92} gutter={46} />
        <p className="mb-0 mt-3 text-[11px] text-muted">Totals for network and disk count from container start.</p>
      </div>
    </div>
  )
}

function ContainerRow({ server, container, sparkline, scopes, expanded, onToggle }) {
  const hostMb = (server.total_ram_gb || 0) * 1024
  const share = hostMb && container.memory_used_mb != null ? (container.memory_used_mb / hostMb) * 100 : null
  const problem = problemRank(container)
  return <>
    <button type="button" onClick={onToggle} aria-expanded={expanded} className={`grid w-full ${COLUMNS} h-11 items-center border-0 border-t border-solid border-line px-[18px] text-left transition-colors hover:bg-raised ${expanded ? 'bg-raised' : 'bg-transparent'}`}>
      <span className="flex min-w-0 items-center gap-2.5 pl-5 pr-3"><span className={`dot ${problem === 2 ? 'dot-ok' : container.status === 'restarting' || problem === 1 ? 'dot-signal' : 'dot-warn'}`} style={{ width: 6, height: 6 }} /><span className="truncate text-[13px] font-medium text-paper" title={container.name}>{container.name}</span></span>
      <span className="mono hidden truncate pr-4 text-soft xl:block" title={container.image || ''}>{container.image || '—'}</span>
      <span className="min-w-0 pr-3"><StatusText container={container} /></span>
      <span className="flex items-center gap-2 pr-5"><Sparkline values={sparkline} width={80} max={100} /><span className="ml-auto text-xs font-medium text-paper">{formatPercent(container.cpu_pct)}</span></span>
      <span className="pr-5">
        <span className="flex items-baseline justify-between gap-2"><span className="text-xs font-medium text-paper">{formatMegabytes(container.memory_used_mb)}</span>{share != null && share >= 10 && <span className="truncate text-[11px] text-muted">{Math.round(share)}% of host</span>}</span>
        <Meter className="mt-1" percent={share} label={`${container.name} memory share of the host`} />
      </span>
      <PortChips container={container} scopes={scopes} />
      <span className="truncate text-right text-xs text-soft">{containerUptime(container.started_at) || '—'}</span>
      <span className={`text-right text-xs ${container.restart_count ? 'text-warn' : 'text-paper'}`}>{container.restart_count}</span>
    </button>
    {expanded && <div className="border-t border-line"><ContainerDetails server={server} container={container} /></div>}
  </>
}

function summary(containers) {
  const count = (test) => containers.filter(test).length
  const restarting = count((container) => container.status === 'restarting')
  const unhealthy = count((container) => container.healthcheck_status === 'unhealthy' && container.status === 'running')
  const stopped = count(CONTAINER_FILTERS.stopped)
  const parts = [restarting ? `${restarting} restarting` : null, unhealthy ? `${unhealthy} unhealthy` : null, stopped ? `${stopped} stopped` : null].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'all running'
}

// Filter, sort and grouping state shared by the card and the tab, plus which groups and rows are open.
function useContainerView(server, { rowLimit, openFirst }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState('cpu')
  const [grouped, setGrouped] = useState(true)
  const [toggled, setToggled] = useState({})
  const [unfolded, setUnfolded] = useState(() => new Set())
  const [expandedId, setExpandedId] = useState(null)
  const containers = server.containers || []
  const reachable = useMemo(() => reachablePortSet(server), [server])
  const scopes = useMemo(() => new Map((server.network?.listeners || []).map((listener) => [`${listener.proto}/${listener.port}`, listener.scope])), [server])
  const counts = Object.fromEntries(Object.keys(CONTAINER_FILTERS).map((key) => [key, containers.filter((container) => CONTAINER_FILTERS[key](container, reachable)).length]))
  const needle = query.trim().toLowerCase()
  const narrowed = needle !== '' || filter !== 'all'
  const shown = containers.filter((container) => CONTAINER_FILTERS[filter](container, reachable) && (!needle || `${container.name} ${container.image || ''} ${parsePorts(container.ports).map((port) => port.host).join(' ')}`.toLowerCase().includes(needle)))
  const groups = groupContainers(shown, { sort, grouped })
  const isOpen = (group, index) => toggled[group.name] ?? (narrowed || group.problems > 0 || (openFirst && (index === 0 || group.name === STANDALONE)) || !grouped)
  return {
    query, setQuery, filter, setFilter, sort, setSort, grouped, setGrouped, counts, groups, scopes, narrowed, expandedId,
    toggleRow: (id) => setExpandedId((current) => (current === id ? null : id)),
    isOpen, toggleGroup: (group, index) => setToggled((current) => ({ ...current, [group.name]: !isOpen(group, index) })),
    openGroup: (name) => setToggled((current) => ({ ...current, [name]: true })),
    limitFor: (group) => (narrowed || unfolded.has(group.name) || !grouped ? Infinity : rowLimit),
    unfold: (name) => setUnfolded((current) => new Set(current).add(name)),
  }
}

function Toolbar({ view, filters, server, showSort }) {
  return <>
    <label className="relative block w-full max-w-[262px]">
      <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-[9px] text-muted" />
      <input type="search" className="field pl-9" value={view.query} onChange={(event) => view.setQuery(event.target.value)} placeholder="Filter by name, image or port" aria-label={`Filter containers on ${server.hostname}`} />
    </label>
    <div className="seg" role="group" aria-label="Show">
      {filters.map((key) => <button key={key} type="button" aria-pressed={view.filter === key} onClick={() => view.setFilter(key)}>{FILTER_LABELS[key]} {view.counts[key]}</button>)}
    </div>
    <span className="ml-auto flex items-center gap-3">
      {showSort && <label className="flex items-center gap-3 text-xs text-muted">Sort
        <select className="select" value={view.sort} onChange={(event) => view.setSort(event.target.value)}><option value="cpu">CPU</option><option value="memory">Memory</option><option value="name">Name</option></select>
      </label>}
      <select className="select" aria-label="Grouping" value={view.grouped ? 'project' : 'none'} onChange={(event) => view.setGrouped(event.target.value === 'project')}><option value="project">Group: project</option><option value="none">Group: none</option></select>
    </span>
  </>
}

function ContainerTable({ server, view, sparklines }) {
  if (!view.groups.some((group) => group.count)) return <p className="m-0 border-t border-line px-[18px] py-6 text-center text-xs text-muted">No containers match{view.query.trim() ? ` “${view.query.trim()}”` : ' this filter'}.</p>
  return (
    <div className="custom-scroll overflow-x-auto">
      <div className="min-w-[900px] xl:min-w-[1060px]">
        <div className={`grid ${COLUMNS} grid-head h-10 items-center border-t border-line px-[18px]`}>
          <span>Name</span><span className="hidden xl:block">Image</span><span>Status</span><span>CPU · 15 min</span><span>Memory</span><span>Published ports</span><span className="text-right">Up</span><span className="text-right">Restarts</span>
        </div>
        {view.groups.map((group, index) => {
          const open = view.isOpen(group, index)
          const limit = view.limitFor(group)
          const visible = group.containers.slice(0, limit)
          const hidden = group.containers.slice(limit)
          const appGroup = server.is_dashboard_host && group.name !== null && APP_PROJECT.test(group.name)
          const totals = `${group.count} ${group.count === 1 ? 'container' : 'containers'} · ${formatPercent(group.cpu)} CPU · ${formatMegabytesShort(group.memoryMb)}`
          return (
            <div key={group.name ?? 'all'}>
              {group.name !== null && (
                <button type="button" onClick={() => view.toggleGroup(group, index)} aria-expanded={open} className="flex h-[38px] w-full items-center gap-2.5 border-0 border-t border-solid border-line bg-sunken px-[18px] text-left hover:bg-raised">
                  <Caret open={open} className="text-muted" />
                  <span className="text-[13px] font-semibold text-paper">{group.name}</span>
                  {appGroup && <span className="chip">this app</span>}
                  <span className="min-w-0 truncate text-xs text-muted">{totals}{!open && appGroup ? ` · ${group.containers.map((container) => container.name).sort().join(', ')}` : ''}</span>
                  {group.problems > 0 && <span className="chip chip-signal ml-1">{group.problems} {group.problems === 1 ? 'problem' : 'problems'}</span>}
                </button>
              )}
              {open && <>
                {visible.map((container) => <ContainerRow key={container.container_id} server={server} container={container} sparkline={sparklines?.[container.container_id]} scopes={view.scopes} expanded={view.expandedId === container.container_id} onToggle={() => view.toggleRow(container.container_id)} />)}
                {hidden.length > 0 && <button type="button" onClick={() => view.unfold(group.name)} className="block h-10 w-full truncate border-0 border-t border-solid border-line bg-transparent pl-[58px] pr-[18px] text-left text-xs text-muted hover:bg-raised hover:text-soft">{hidden.length} more · {hidden.map((container) => container.name).sort().join(', ')}</button>}
              </>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// Under a server's Overview: the same table in short, or just the group names when nothing is wrong anywhere.
export function ContainersCard({ server, sparklines, calm }) {
  const view = useContainerView(server, { rowLimit: 5, openFirst: true })
  const [showTable, setShowTable] = useState(false)
  const containers = server.containers || []
  if (!containers.length) return null
  const collapsed = calm && !showTable && !view.narrowed && !view.groups.some((group) => group.problems)
  return (
    <section className="card overflow-hidden" aria-label={`Containers on ${server.hostname}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 px-[18px] py-3">
        <h2 className="m-0 text-sm font-semibold text-paper">Containers</h2>
        <span className="mr-auto text-xs text-muted">{containers.length} on {server.hostname} · {summary(containers)}</span>
        <Toolbar view={view} filters={['all', 'problems', 'exposed', 'restarted']} server={server} />
      </div>
      {collapsed ? <>
        <div className="flex flex-wrap gap-2.5 border-t border-line px-[18px] py-3.5">
          {view.groups.map((group) => <button key={group.name ?? 'all'} type="button" className="chip h-[22px] cursor-pointer text-paper hover:border-muted" onClick={() => { view.openGroup(group.name); setShowTable(true) }}>{(group.name ?? 'all').toLowerCase()} · {group.count}</button>)}
        </div>
        <p className="m-0 border-t border-line px-[18px] py-3 text-xs text-muted">All groups collapsed because nothing is wrong. A group opens by itself when one of its containers has a problem.</p>
      </> : <ContainerTable server={server} view={view} sparklines={sparklines} />}
    </section>
  )
}

function Stat({ label, value, unit, tone }) {
  return (
    <div className="min-w-0 px-[18px] py-3">
      <div className="truncate text-[11px] text-muted">{label}</div>
      <div className="mt-1.5 whitespace-nowrap"><span className={`text-[17px] font-semibold ${tone === 'warn' ? 'text-warn' : tone === 'signal' ? 'text-signal' : 'text-paper'}`}>{value}</span>{unit && <span className="ml-1.5 text-[11px] text-muted">{unit}</span>}</div>
    </div>
  )
}

export function StatStrip({ children, columns }) {
  return <div className={`grid grid-cols-2 divide-x divide-line border-b border-line ${columns}`}>{children}</div>
}

export function ContainersTab({ server, sparklines }) {
  const view = useContainerView(server, { rowLimit: 4, openFirst: true })
  const containers = server.containers || []
  if (!containers.length) {
    return <p className="m-0 px-[18px] py-10 text-center text-[13px] text-muted">{server.host?.docker_status === 'unreachable' ? 'Docker is not answering on this machine, so there is no container data right now.' : `No containers reported on ${server.hostname}.`}</p>
  }
  const running = containers.filter((container) => container.status === 'running').length
  const unhealthy = containers.filter((container) => container.healthcheck_status === 'unhealthy').length
  const restarts = containers.reduce((sum, container) => sum + (container.restart_count || 0), 0)
  const cpu = containers.reduce((sum, container) => sum + (container.cpu_pct ?? 0), 0)
  const memory = containers.reduce((sum, container) => sum + (container.memory_used_mb ?? 0), 0)
  const hostMb = (server.total_ram_gb || 0) * 1024
  return <>
    <StatStrip columns="md:grid-cols-3 xl:grid-cols-6">
      <Stat label="Running" value={running} unit={`of ${containers.length}`} tone={running < containers.length ? 'warn' : undefined} />
      <Stat label="Unhealthy" value={unhealthy} tone={unhealthy ? 'signal' : undefined} />
      <Stat label="Restarts · since created" value={restarts} tone={restarts ? 'warn' : undefined} />
      <Stat label="Exposed to network" value={view.counts.exposed} tone={view.counts.exposed ? 'warn' : undefined} />
      <Stat label="CPU, all containers" value={formatPercent(cpu)} unit={server.cpu_cores ? `of ${server.cpu_cores * 100}%` : undefined} />
      <Stat label="Memory, all containers" value={formatMegabytesShort(memory)} unit={hostMb ? `${Math.round((memory / hostMb) * 100)}% of host` : undefined} />
    </StatStrip>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 px-[18px] py-3"><Toolbar view={view} filters={['all', 'problems', 'exposed', 'restarted', 'stopped']} server={server} showSort /></div>
    <ContainerTable server={server} view={view} sparklines={sparklines} />
    <p className="m-0 border-t border-line px-[18px] py-3 text-xs text-muted">Click a row for its details and a recent chart. Amber ports are admin panels or databases that other machines can reach.</p>
  </>
}
