// The fleet as a rack: one row per server with the same columns, so three or thirty machines compare at a glance.
import { containerState } from '../fleetLogic.js'
import { Meter, Sparkline } from './charts.jsx'
import { SectionHeading } from './NeedsAttention.jsx'
import { forecastKey } from './data.js'
import { forecastPhrase, formatPercent, formatUsedOfTotal, fullestMount, mountLevel, serverMounts, serverState, serverStateLabel, systemLines, thresholdLevel } from './logic.js'
import { isOnline } from '../fleetLogic.js'

export const FLEET_COLUMNS = 'grid-cols-[196px_minmax(150px,1fr)_144px_144px_166px_56px_116px]'
const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const LEVEL_TEXT = ['text-paper', 'text-warn', 'text-signal']

export function rangeLabel(minutes) {
  return minutes < 60 ? `${minutes} min` : `${minutes / 60} h`
}

function ContainersCell({ server }) {
  const containers = server.containers || []
  if (!containers.length) return <span className="text-xs text-dim">{server.host?.docker_status === 'unreachable' ? 'Docker unreachable' : 'none'}</span>
  const states = containers.map(containerState)
  const running = states.filter((state) => state === 'running' || state === 'unhealthy').length
  const restarting = states.filter((state) => state === 'restarting').length
  const unhealthy = states.filter((state) => state === 'unhealthy').length
  const stopped = states.filter((state) => state === 'stopped').length
  const note = restarting ? [`${restarting} restarting`, 'text-signal'] : unhealthy ? [`${unhealthy} unhealthy`, 'text-signal'] : stopped ? [`${stopped} stopped`, 'text-warn'] : ['running', 'text-muted']
  return <span className="whitespace-nowrap"><span className="text-[13px] font-medium text-paper">{running}/{containers.length}</span> <span className={`text-[11px] ${note[1]}`}>{note[0]}</span></span>
}

function ServerRow({ server, selected, sparkline, forecasts, overview, onSelect }) {
  const state = serverState(server)
  const host = server.host
  const mounts = serverMounts(server)
  const disk = fullestMount(mounts)
  const [system, address] = systemLines(server)
  const thresholds = overview.alert_thresholds || {}
  const memoryLevel = thresholdLevel(host?.ram_pct, thresholds.memory_warning_pct ?? 85, thresholds.memory_critical_pct ?? 95)
  const diskLevel = disk ? mountLevel(disk, overview.disk_thresholds, overview.mount_thresholds) : 0
  const forecast = disk ? forecastPhrase(forecasts[forecastKey(server.id, disk.mount)], { short: true }) : null
  const diskNote = !disk ? '' : forecast ? `${disk.mount} · ${forecast}` : disk.total_gb != null && disk.mount.length < 9 ? `${disk.mount} · ${formatUsedOfTotal(disk.used_gb, disk.total_gb)}` : disk.mount
  const critical = state === 'critical'
  const badge = critical || state === 'offline' ? serverStateLabel(server) : null
  return (
    <button type="button" onClick={() => onSelect(server.id)} aria-pressed={selected} aria-label={`${server.hostname}, ${serverStateLabel(server)}${selected ? ', selected' : ''}`}
      className={`drawer w-full ${FLEET_COLUMNS} ${critical ? 'drawer-alert drawer-out' : ''} ${selected ? 'drawer-selected' : ''}`} style={{ minHeight: 58, width: critical ? 'calc(100% - 24px)' : undefined }}>
      <span className="flex min-w-0 items-center gap-2.5 pr-3">
        <span className={`dot ${STATE_DOT[state]}`} />
        <span className="truncate text-sm font-semibold text-paper">{server.hostname}</span>
        {badge ? <span className={`chip ${critical ? 'chip-signal' : ''}`}>{badge}</span> : server.is_dashboard_host && <span className="chip">this machine</span>}
      </span>
      <span className="min-w-0 pr-4">
        <span className="block truncate text-xs text-soft">{system || 'Waiting for the first check-in'}</span>
        <span className="block truncate text-[11px] text-muted">{address}</span>
      </span>
      {host ? <>
        <span className="flex items-center gap-2.5 pr-4"><Sparkline values={sparkline} tone={critical ? 'signal' : 'paper'} /><span className="ml-auto text-[13px] font-medium text-paper">{formatPercent(host.cpu_utilization_pct)}</span></span>
        <span className="pr-4">
          <span className="flex items-baseline justify-between gap-2"><span className={`text-[13px] font-medium ${LEVEL_TEXT[memoryLevel]}`}>{formatPercent(host.ram_pct)}</span><span className="truncate text-[11px] text-muted">{server.total_ram_gb ? formatUsedOfTotal(host.ram_used_gb, server.total_ram_gb) : ''}</span></span>
          <Meter className="mt-1.5" percent={host.ram_pct} level={memoryLevel} label={`${server.hostname} memory`} />
        </span>
        <span className="pr-4">
          <span className="flex items-baseline justify-between gap-2"><span className={`text-[13px] font-medium ${LEVEL_TEXT[diskLevel]}`}>{disk ? formatPercent(disk.used_pct) : '—'}</span><span className="truncate text-[11px] text-muted" title={diskNote}>{diskNote}</span></span>
          <Meter className="mt-1.5" percent={disk?.used_pct} level={diskLevel} label={`${server.hostname} busiest disk`} />
        </span>
        <span className="text-[13px] font-medium text-paper">{host.load_1m == null ? '—' : host.load_1m.toFixed(2)}</span>
        <ContainersCell server={server} />
      </> : <span className="col-span-5 text-xs text-muted">{isOnline(server) ? 'Collecting the first measurement…' : 'The collector has not sent a measurement yet.'}</span>}
    </button>
  )
}

export function FleetHeader({ minutes }) {
  return (
    <div className={`grid ${FLEET_COLUMNS} grid-head px-[43px] pb-1`}>
      <span>Server</span><span>System</span><span>CPU · {rangeLabel(minutes)}</span><span>Memory</span><span>Busiest disk</span><span>Load</span><span>Containers</span>
    </div>
  )
}

export function FleetRack({ overview, servers, selectedId, sparklines, forecasts, minutes, onSelect }) {
  const reporting = servers.filter((server) => isOnline(server)).length
  const troubled = servers.some((server) => serverState(server) !== 'ok')
  return (
    <section aria-label="Fleet">
      <SectionHeading title="Fleet" detail={`${servers.length} ${servers.length === 1 ? 'server' : 'servers'} · ${troubled ? `${reporting} ${reporting === 1 ? 'collector' : 'collectors'} reporting` : 'all healthy'}`}>Sort: {troubled ? 'problems first' : 'name'}</SectionHeading>
      <div className="custom-scroll overflow-x-auto">
        <div className="min-w-[1080px]">
          <FleetHeader minutes={minutes} />
          <div className="rack">
            {servers.map((server) => <ServerRow key={server.id} server={server} selected={server.id === selectedId} sparkline={sparklines?.[server.id]} forecasts={forecasts} overview={overview} onSelect={onSelect} />)}
          </div>
        </div>
      </div>
    </section>
  )
}
