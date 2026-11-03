// The Overview on a phone. Same order as the desktop in one column: what needs you, then the fleet as cards with
// three numbers each. Tapping a server opens it as its own screen with tabs, one chart at a time, and container
// details in a sheet from the bottom.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { seriesFor } from '../chartMath.js'
import { problemRank } from '../containerLogic.js'
import { containerState, isOnline } from '../fleetLogic.js'
import { Caret, Icon } from '../icons.jsx'
import { PhoneHeader, RoundButton } from '../shell.jsx'
import { Sheet, Soon } from '../ui.jsx'
import { LineChart, Meter, Sparkline } from './charts.jsx'
import { forecastKey, usePolled } from './data.js'
import { AllClear } from './NeedsAttention.jsx'
import { INTERVALS, intervalLabel } from './ServerPanel.jsx'
import {
  CONTAINER_FILTERS, attentionAction, containerUptime, forecastPhrase, formatAge, formatMegabytes, formatPercent, formatSpan, formatUsedOfTotal, fullestMount, groupContainers, healthChecks, healthVerdict,
  mountLevel, needsAttention, niceCeiling, parsePorts, reachablePortSet, serverMounts, serverState, serverStateLabel, thresholdLevel,
} from './logic.js'

const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const TONE_DOT = { ok: 'dot-ok', warn: 'dot-warn', unknown: '' }
const LEVEL_TEXT = ['text-paper', 'text-warn', 'text-signal']
const COUNT_WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine']
const READ_ONLY_NOTE = 'Infradeck only reads from Docker for now, so it cannot fetch logs or restart a container yet.'
const FILTER_LABELS = { all: 'All', problems: 'Problems', exposed: 'Exposed', restarted: 'Restarted', stopped: 'Stopped' }

const shortSystem = (server) => server.os_info?.replace(/ \((.*)\)$/, '').replace(/ LTS$/, '') || ''

// "3 servers · 63 containers." and, when something is wrong, "One needs you now."
export function headlineParts(servers, separator = ' · ') {
  const containers = servers.reduce((sum, server) => sum + (server.containers?.length || 0), 0)
  const troubled = servers.filter((server) => ['critical', 'offline'].includes(serverState(server))).length || servers.filter((server) => serverState(server) === 'warn').length
  const count = `${servers.length} ${servers.length === 1 ? 'server' : 'servers'}${separator}${containers} ${containers === 1 ? 'container' : 'containers'}.`
  return [count, troubled ? `${COUNT_WORDS[troubled] || troubled} ${troubled === 1 ? 'needs' : 'need'} you now.` : 'All healthy.']
}

function Heading({ title, detail, children }) {
  return (
    <div className="mb-2.5 flex items-baseline gap-2">
      <h2 className="m-0 text-[15px] font-semibold text-paper">{title}</h2>
      {detail != null && <span className="text-xs text-muted">{detail}</span>}
      {children && <span className="ml-auto text-[13px] text-soft">{children}</span>}
    </div>
  )
}

// ---------- the fleet screen ----------

function AttentionCard({ alert, onAction }) {
  const action = attentionAction(alert)
  const where = alert.hostname && !alert.title.includes(alert.hostname) ? `${alert.hostname} · ` : ''
  const age = formatAge(alert.created_at)
  if (alert.severity === 'CRITICAL') {
    // Still the pulled-out drawer: full orange, nudged past the rack rail, with a thumb-sized button.
    return (
      <div className="relative left-3.5 rounded-md bg-signal px-4 pb-4 pt-3.5 text-bg shadow-[0_10px_28px_rgba(0,0,0,0.4)]">
        <div className="flex items-baseline justify-between text-[11px] font-bold tracking-[0.08em]"><span>CRITICAL</span><span className="font-semibold tracking-normal">{age}</span></div>
        <div className="mt-1.5 text-[15px] font-semibold leading-5">{alert.title}</div>
        <p className="mb-0 mt-1 text-[13px] leading-5 text-bg/80">{where}{alert.description}</p>
        <button type="button" onClick={() => onAction(alert, action)} className="mt-3.5 flex h-11 w-full items-center justify-center rounded-md border-0 bg-[#160800] text-sm font-semibold text-paper">{action.label}</button>
      </div>
    )
  }
  return (
    <button type="button" onClick={() => onAction(alert, action)} className="block w-full rounded-md border border-line bg-ink px-4 pb-3.5 pt-3 text-left">
      <span className="flex items-baseline justify-between text-[11px]"><span className="font-bold tracking-[0.08em] text-warn">{alert.severity}</span><span className="text-muted">{age}</span></span>
      <span className="mt-1.5 block text-sm font-semibold text-paper">{alert.title}</span>
      <span className="mt-1 line-clamp-2 block text-[13px] leading-5 text-muted">{where}{alert.description}</span>
    </button>
  )
}

function containerNote(server) {
  const containers = server.containers || []
  const states = containers.map(containerState)
  const running = states.filter((state) => state === 'running' || state === 'unhealthy').length
  const restarting = states.filter((state) => state === 'restarting').length
  const unhealthy = states.filter((state) => state === 'unhealthy').length
  const stopped = states.filter((state) => state === 'stopped').length
  const warnings = server.alerts_by_severity?.WARNING || 0
  const note = restarting ? [`${restarting} restarting`, 'text-signal'] : unhealthy ? [`${unhealthy} unhealthy`, 'text-signal'] : stopped ? [`${stopped} stopped`, 'text-warn'] : warnings ? [`${warnings} ${warnings === 1 ? 'warning' : 'warnings'}`, ''] : null
  return { count: containers.length ? `${running}/${containers.length} containers` : null, note }
}

function Figure({ label, value, tone = 'text-paper', children }) {
  return (
    <span className="min-w-0">
      <span className="block text-xs text-muted">{label}</span>
      <span className={`mt-1 block text-[15px] font-semibold ${tone}`}>{value}</span>
      <span className="mt-1.5 block h-[18px]">{children}</span>
    </span>
  )
}

function ServerCard({ server, overview, sparkline, onOpen }) {
  const state = serverState(server)
  const host = server.host
  const disk = fullestMount(serverMounts(server))
  const thresholds = overview.alert_thresholds || {}
  const memoryLevel = thresholdLevel(host?.ram_pct, thresholds.memory_warning_pct ?? 85, thresholds.memory_critical_pct ?? 95)
  const diskLevel = disk ? mountLevel(disk, overview.disk_thresholds, overview.mount_thresholds) : 0
  const critical = state === 'critical'
  const badge = critical || state === 'offline' ? serverStateLabel(server) : null
  const { count, note } = containerNote(server)
  return (
    <Link to={`/?server=${server.id}`} onClick={onOpen} aria-label={`${server.hostname}, ${serverStateLabel(server)}`} className={`block rounded-md border bg-ink px-4 pb-3.5 pt-3.5 ${critical ? 'border-2 border-signal' : 'border-line'}`}>
      <span className="flex items-center gap-2.5">
        <span className={`dot ${STATE_DOT[state]}`} />
        <span className="truncate text-[15px] font-semibold text-paper">{server.hostname}</span>
        {badge ? <span className={`chip ${critical ? 'chip-signal' : ''}`}>{badge}</span> : server.is_dashboard_host && <span className="chip">this machine</span>}
        <Icon name="chevron" size={14} className="ml-auto shrink-0 text-muted" />
      </span>
      {host ? (
        <span className="mt-3 grid grid-cols-3 gap-x-5">
          <Figure label="CPU" value={formatPercent(host.cpu_utilization_pct)}><Sparkline values={sparkline} width={96} height={16} tone={critical ? 'signal' : 'paper'} /></Figure>
          <Figure label="Memory" value={formatPercent(host.ram_pct)} tone={LEVEL_TEXT[memoryLevel]}><Meter className="mt-1.5" percent={host.ram_pct} level={memoryLevel} label={`${server.hostname} memory`} /></Figure>
          <Figure label="Disk" value={disk ? formatPercent(disk.used_pct) : '—'} tone={LEVEL_TEXT[diskLevel]}><Meter className="mt-1.5" percent={disk?.used_pct} level={diskLevel} label={`${server.hostname} busiest disk`} /></Figure>
        </span>
      ) : <span className="mt-3 block text-[13px] text-muted">{isOnline(server) ? 'Collecting the first measurement…' : 'The collector has not sent a measurement yet.'}</span>}
      <span className="mt-2.5 block truncate text-xs text-muted">
        {[shortSystem(server), count].filter(Boolean).join(' · ')}
        {note && <> · <span className={note[1]}>{note[0]}</span></>}
      </span>
    </Link>
  )
}

export function PhoneOverview({ overview, servers, isAdmin, waiting, sparklines, watch, error, onAttention, onConnect }) {
  const alerts = (overview.alerts || []).filter(needsAttention)
  const counts = overview.alerts_by_severity || {}
  const open = (counts.CRITICAL || 0) + (counts.WARNING || 0)
  const troubled = servers.some((server) => serverState(server) !== 'ok')
  const [count, verdict] = headlineParts(servers)
  return (
    <>
      <PhoneHeader mark title="Overview" subtitle={<>{count} <span className={troubled ? 'text-paper' : ''}>{verdict}</span></>} />
      <div className="space-y-7 px-4 pt-4">
        {error && <div role="alert" className="rounded-md border border-warn/40 px-3.5 py-2.5 text-xs text-warn">{error}</div>}
        {isAdmin && waiting.length > 0 && (
          <button type="button" onClick={onConnect} className="flex w-full items-center gap-3 rounded-md border border-edge bg-ink px-3.5 py-3 text-left text-[13px] text-soft">
            <span className="dot shrink-0" style={{ background: 'var(--color-paper)' }} />
            <span className="min-w-0 flex-1"><span className="font-medium text-paper">{waiting.map((item) => item.hostname).join(', ')}</span> {waiting.length === 1 ? 'is' : 'are'} asking to join the fleet.</span>
            <span className="font-medium text-paper">Review</span>
          </button>
        )}
        <section aria-label="Needs attention">
          <Heading title="Needs attention" detail={open}><Link to="/agent" className="inline-flex h-7 items-center">All</Link></Heading>
          {open ? <>
            <div className="space-y-2.5 border-x-2 border-rail px-2.5">{alerts.map((alert) => <AttentionCard key={alert.id} alert={alert} onAction={onAttention} />)}</div>
            {open > alerts.length && <p className="mb-0 mt-2 text-xs text-muted">Showing the {alerts.length} most severe of {open}. The rest are in Agent.</p>}
          </> : <AllClear overview={overview} watch={watch} />}
        </section>
        <section aria-label="Fleet">
          <Heading title="Fleet" detail={`${servers.length} ${servers.length === 1 ? 'server' : 'servers'}`}>{troubled ? 'Problems first' : 'By name'}</Heading>
          <div className="space-y-2.5">{servers.map((server) => <ServerCard key={server.id} server={server} overview={overview} sparkline={sparklines?.[server.id]} />)}</div>
        </section>
        {isAdmin && <button type="button" onClick={onConnect} className="btn h-12 w-full text-sm"><Icon name="plus" size={15} />Connect a server</button>}
      </div>
    </>
  )
}

// ---------- one server ----------

// One full-width chart with pills to switch metric; a sideways swipe on the chart moves to the next one.
function MetricChart({ server, samples, minutes }) {
  const host = server.host
  const disk = fullestMount(serverMounts(server).filter((mount) => mount.total_gb != null))
  const metrics = [
    { key: 'cpu', label: 'CPU', value: formatPercent(host.cpu_utilization_pct) },
    { key: 'ram', label: 'Memory', value: formatPercent(host.ram_pct) },
    { key: 'load', label: 'Load', value: host.load_1m == null ? '—' : host.load_1m.toFixed(2) },
    ...(disk ? [{ key: 'disk', label: 'Disk', value: formatPercent(disk.used_pct) }] : []),
  ]
  const [metric, setMetric] = useState('cpu')
  const holding = useRef(false)
  const touch = useRef(null)
  const pills = useRef(null)
  const diskRange = minutes > 60 ? '6h' : '1h'
  const { data: diskHistory } = usePolled(metric === 'disk' && disk ? `/monitoring/servers/${server.id}/mounts/history?mount=${encodeURIComponent(disk.mount)}&range=${diskRange}&metrics=used_pct` : null, 60)

  useEffect(() => { pills.current?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }, [metric])

  const cores = server.cpu_cores || 1
  let points = samples.map((sample) => ({ time: sample.time, value: sample[metric] }))
  let max = 100
  let ticks = [100, 50, 0]
  let format = (tick) => `${tick}%`
  let readout = (value) => formatPercent(value)
  let span = `last ${minutes < 60 ? `${minutes} min` : `${minutes / 60} h`}`
  if (metric === 'load') {
    const peak = Math.max(cores, ...samples.map((sample) => sample.load ?? 0))
    max = peak <= cores ? cores : niceCeiling(peak)
    ticks = [max, max / 2, 0]
    format = (tick) => String(Number(tick.toFixed(1)))
    readout = (value) => value.toFixed(2)
  } else if (metric === 'disk') {
    points = seriesFor(diskHistory?.points, 'used_pct').map((point) => ({ time: point.t, value: point.avg }))
    span = `${disk.mount} · last ${diskRange.replace('h', ' h')}`
  }
  const known = points.map((point) => point.value).filter((value) => value != null)
  const average = known.length ? known.reduce((sum, value) => sum + value, 0) / known.length : null
  const current = metrics.find((item) => item.key === metric) || metrics[0]
  const step = (direction) => {
    const index = metrics.findIndex((item) => item.key === metric)
    const next = metrics[index + direction]
    if (next) setMetric(next.key)
  }

  return (
    <div>
      <div ref={pills} className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4" role="group" aria-label="Chart">
        {metrics.map((item) => (
          <button key={item.key} type="button" aria-pressed={metric === item.key} onClick={() => setMetric(item.key)} className={`h-9 shrink-0 rounded-full border px-3.5 text-[13px] font-medium ${metric === item.key ? 'border-paper bg-paper text-bg' : 'border-edge bg-transparent text-soft'}`}>{item.label} {item.value}</button>
        ))}
      </div>
      <div className="mt-3 rounded-md border border-line bg-ink px-4 pb-3 pt-3.5"
        onTouchStart={(event) => { touch.current = [event.touches[0].clientX, event.touches[0].clientY] }}
        onTouchEnd={(event) => {
          const start = touch.current
          touch.current = null
          if (!start || holding.current) return
          const dx = event.changedTouches[0].clientX - start[0]
          const dy = event.changedTouches[0].clientY - start[1]
          if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1)
        }}>
        <div className="mb-3 flex items-baseline gap-2">
          <span className="text-[28px] font-semibold leading-8 tracking-[-0.01em] text-paper">{current.value}</span>
          <span className="min-w-0 truncate text-xs text-muted">{current.label} · {span}</span>
          {average != null && <span className="ml-auto shrink-0 text-xs text-muted">avg {metric === 'load' ? average.toFixed(2) : formatPercent(average)}</span>}
        </div>
        <LineChart label={`${current.label} on ${server.hostname}`} points={points} max={max} ticks={ticks} format={format} area={metric !== 'cpu'} height={150} gutter={34} readout={readout} onHold={(value) => { holding.current = value }} />
      </div>
      <p className="mb-0 mt-2 text-center text-xs leading-5 text-muted">Swipe the chart for memory and load. Touch and hold to read a value.</p>
    </div>
  )
}

function HealthList({ server }) {
  const [help, setHelp] = useState(false)
  const checks = healthChecks(server)
  return (
    <section aria-label="Health checks">
      <Heading title="Health checks" detail={healthVerdict(checks)} />
      <ul className="m-0 list-none divide-y divide-line overflow-hidden rounded-md border border-line bg-ink p-0">
        {checks.map((check) => (
          <li key={check.key} className="grid min-h-[45px] grid-cols-[18px_96px_minmax(0,1fr)] items-center px-4 py-2 text-[13px]">
            <span className={`dot ${TONE_DOT[check.tone]}`} style={{ width: 7, height: 7 }} />
            <span className="text-muted">{check.label}</span>
            <span className={check.tone === 'warn' ? 'text-warn' : check.tone === 'unknown' ? 'text-muted' : 'text-paper'}>
              {check.text}{check.action && <> · <button type="button" className="link" aria-expanded={help} onClick={() => setHelp((value) => !value)}>{check.action}</button></>}
            </span>
          </li>
        ))}
      </ul>
      {help && <p className="mb-0 mt-2 text-xs leading-5 text-muted">The internet check is off until this machine's collector is given targets: set <span className="mono text-soft">INFRADECK_CONNECTIVITY_TARGETS</span> (for example <span className="mono text-soft">1.1.1.1:443,dns.google:443</span>) in its environment and restart it.</p>}
    </section>
  )
}

function StorageList({ server, overview, forecasts }) {
  const mounts = serverMounts(server)
  if (!mounts.length) return null
  return (
    <section aria-label="Storage">
      <Heading title="Storage" />
      <div className="divide-y divide-line overflow-hidden rounded-md border border-line bg-ink">
        {mounts.map((mount) => {
          const level = mountLevel(mount, overview.disk_thresholds, overview.mount_thresholds)
          const forecast = forecastPhrase(forecasts[forecastKey(server.id, mount.mount)])
          return (
            <div key={mount.mount} className="px-4 py-3">
              <div className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="mono min-w-0 truncate text-paper">{mount.mount}</span>
                <span className={`shrink-0 ${LEVEL_TEXT[level]}`}>{mount.total_gb != null ? formatUsedOfTotal(mount.used_gb, mount.total_gb, ' of ') : formatPercent(mount.used_pct)}</span>
              </div>
              <Meter className="mt-2.5" percent={mount.used_pct} level={level} label={`${mount.mount} usage`} />
              <div className="mt-2.5 text-xs text-muted">{forecast ? forecast.charAt(0).toUpperCase() + forecast.slice(1) : 'No clear growth trend'}</div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function containerLine(container) {
  const stopped = container.status === 'exited' || container.status === 'dead'
  const extra = stopped && container.exit_code != null ? (container.oom_killed ? 'out of memory' : `code ${container.exit_code}`) : container.healthcheck_status
  return [container.status, extra].filter(Boolean)
}

const containerDot = (container) => {
  const problem = problemRank(container)
  return problem === 2 ? 'dot-ok' : container.status === 'restarting' || problem === 1 ? 'dot-signal' : 'dot-warn'
}

function ContainerItem({ container, right, sub, onOpen }) {
  const [status, extra] = containerLine(container)
  return (
    <button type="button" onClick={onOpen} className="flex min-h-[60px] w-full items-center gap-3 border-0 border-t border-solid border-line bg-transparent px-4 py-2.5 text-left first:border-t-0">
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2"><span className={`dot ${containerDot(container)}`} style={{ width: 7, height: 7 }} /><span className="truncate text-sm font-medium text-paper">{container.name}</span></span>
        <span className="mt-1 block truncate pl-[15px] text-xs text-muted">
          <span className={status === 'running' ? '' : 'text-warn'}>{status}</span>{extra && <span className={extra === 'unhealthy' || extra === 'out of memory' ? 'text-signal' : ''}> · {extra}</span>}{sub ? ` · ${sub}` : ''}
        </span>
      </span>
      <span className="shrink-0 text-right">{right}</span>
    </button>
  )
}

function ContainerSheet({ server, container, onClose }) {
  const { data: detail, error } = usePolled(`/monitoring/servers/${server.id}/containers/${encodeURIComponent(container.container_id)}?limit=60`, 30)
  const latest = detail?.latest
  const history = detail?.history || []
  const cpuMax = Math.max(100, niceCeiling(Math.max(0, ...history.map((sample) => sample.cpu_pct ?? 0))))
  const span = history.length > 1 ? formatSpan((new Date(history[history.length - 1].time) - new Date(history[0].time)) / 1000) : null
  const health = container.healthcheck_status
  const title = (
    <div>
      <div className="flex items-center gap-2.5">
        <span className={`dot ${containerDot(container)}`} />
        <h2 className="m-0 truncate text-[17px] font-semibold text-paper">{container.name}</h2>
        {health && <span className={`chip ${health === 'healthy' ? 'chip-ok' : health === 'unhealthy' ? 'chip-signal' : ''}`}>{health}</span>}
      </div>
      <div className="mono mt-1.5 truncate text-[11px] text-muted">{[container.image, container.container_id?.slice(0, 12)].filter(Boolean).join(' · ')}</div>
    </div>
  )
  const tile = (label, value, tone = 'text-paper') => <div className="rounded-md border border-line bg-bg px-3.5 py-3"><div className="text-xs text-muted">{label}</div><div className={`mt-1.5 text-[17px] font-semibold ${tone}`}>{value}</div></div>
  return (
    <Sheet open onClose={onClose} title={title} label={`Container ${container.name}`} footer={<>
      <div className="grid grid-cols-3 gap-2">
        <button type="button" className="btn h-12 flex-col gap-0.5" disabled title={READ_ONLY_NOTE}><span className="flex items-center gap-2"><Icon name="logs" size={15} />Logs</span><Soon /></button>
        <button type="button" className="btn h-12 flex-col gap-0.5" disabled title={READ_ONLY_NOTE}><span className="flex items-center gap-2"><Icon name="restart" size={15} />Restart</span><Soon /></button>
        <Link to="/agent" className="btn h-12"><Icon name="agent" size={15} />Ask</Link>
      </div>
      <p className="mb-0 mt-2 text-center text-[11px] text-dim">{READ_ONLY_NOTE}</p>
    </>}>
      <div className="grid grid-cols-2 gap-2">
        {tile('CPU', formatPercent(container.cpu_pct))}
        {tile('Memory', formatMegabytes(container.memory_used_mb))}
        {tile('Up', containerUptime(container.started_at) || '—')}
        {tile('Restarts', container.restart_count ?? 0, container.restart_count ? 'text-warn' : 'text-paper')}
      </div>
      <div className="mt-2 rounded-md border border-line bg-bg px-3 pb-2.5 pt-3">
        {error ? <p className="m-0 py-6 text-center text-xs text-warn">{error}</p> : !latest ? <p className="m-0 py-6 text-center text-xs text-muted">Loading the recent chart…</p> : <>
          <div className="mb-2 text-xs text-muted">CPU · {span ? `last ${span}` : 'recent'}</div>
          <LineChart label={`CPU use of ${container.name}`} points={history.map((sample) => ({ time: sample.time, value: sample.cpu_pct }))} max={cpuMax} ticks={[cpuMax, cpuMax / 2, 0]} format={(tick) => `${tick}%`} area height={96} gutter={36} readout={(value) => formatPercent(value)} />
        </>}
      </div>
      {latest && <dl className="kv m-0 mt-3 text-[13px]" style={{ '--kv-label': '96px' }}>
        <dt>Limit</dt><dd className="text-paper">{latest.memory_limit_mb && (!server.total_ram_gb || latest.memory_limit_mb < server.total_ram_gb * 1024 * 0.98) ? formatMegabytes(latest.memory_limit_mb) : 'none'}</dd>
        <dt>Processes</dt><dd className="text-paper">{latest.pids ?? '—'}</dd>
        <dt>Net in / out</dt><dd className="text-paper">{formatMegabytes(latest.net_rx_mb)} / {formatMegabytes(latest.net_tx_mb)}</dd>
        <dt>Disk r / w</dt><dd className="text-paper">{formatMegabytes(latest.block_read_mb)} / {formatMegabytes(latest.block_write_mb)}</dd>
      </dl>}
    </Sheet>
  )
}

function TopContainers({ server, onAll, onOpen }) {
  const containers = server.containers || []
  if (!containers.length) return null
  const running = containers.filter((container) => container.status === 'running').length
  const top = [...containers].sort((a, b) => problemRank(a) - problemRank(b) || (b.cpu_pct ?? -1) - (a.cpu_pct ?? -1)).slice(0, 3)
  return (
    <section aria-label="Containers">
      <Heading title="Containers" detail={`${running} running`}><button type="button" onClick={onAll} className="h-7 border-0 bg-transparent p-0 text-[13px] text-soft">See all</button></Heading>
      <div className="overflow-hidden rounded-md border border-line bg-ink">
        {top.map((container) => <ContainerItem key={container.container_id} container={container} sub={formatMegabytes(container.memory_used_mb)} onOpen={() => onOpen(container)} right={<><span className="block text-sm font-medium text-paper">{formatPercent(container.cpu_pct)}</span><span className="block text-xs text-muted">CPU</span></>} />)}
      </div>
    </section>
  )
}

function ContainerList({ server, onOpen }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [closed, setClosed] = useState({})
  const containers = server.containers || []
  const reachable = useMemo(() => reachablePortSet(server), [server])
  if (!containers.length) return <p className="m-0 py-10 text-center text-[13px] text-muted">{server.host?.docker_status === 'unreachable' ? 'Docker is not answering on this machine, so there is no container data right now.' : `No containers reported on ${server.hostname}.`}</p>
  const counts = Object.fromEntries(Object.keys(CONTAINER_FILTERS).map((key) => [key, containers.filter((container) => CONTAINER_FILTERS[key](container, reachable)).length]))
  const needle = query.trim().toLowerCase()
  const shown = containers.filter((container) => CONTAINER_FILTERS[filter](container, reachable) && (!needle || `${container.name} ${container.image || ''} ${parsePorts(container.ports).map((port) => port.host).join(' ')}`.toLowerCase().includes(needle)))
  const groups = groupContainers(shown, { sort: 'cpu', grouped: true })
  return (
    <div>
      <label className="relative block">
        <Icon name="search" size={15} className="pointer-events-none absolute left-3.5 top-[15px] text-muted" />
        <input type="search" className="field h-11 pl-10 text-sm" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter by name, image or port" aria-label={`Filter containers on ${server.hostname}`} />
      </label>
      <div className="no-scrollbar -mx-4 mt-3 flex gap-2 overflow-x-auto px-4" role="group" aria-label="Show">
        {Object.keys(FILTER_LABELS).map((key) => <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)} className={`h-9 shrink-0 rounded-full border px-3.5 text-[13px] font-medium ${filter === key ? 'border-raised bg-raised text-paper' : 'border-edge bg-transparent text-soft'}`}>{FILTER_LABELS[key]} {counts[key]}</button>)}
      </div>
      {!groups.some((group) => group.count) ? <p className="m-0 py-8 text-center text-[13px] text-muted">No containers match{needle ? ` “${query.trim()}”` : ' this filter'}.</p> : (
        <div className="mt-3 overflow-hidden rounded-md border border-line bg-ink">
          {groups.map((group) => {
            const open = !(closed[group.name] ?? false)
            return (
              <div key={group.name ?? 'all'} className="border-t border-line first:border-t-0">
                {group.name !== null && <button type="button" onClick={() => setClosed((current) => ({ ...current, [group.name]: open }))} aria-expanded={open} className="flex h-11 w-full items-center gap-2.5 border-0 bg-sunken px-4 text-left">
                  <Caret open={open} className="text-muted" />
                  <span className="truncate text-[13px] font-semibold text-paper">{group.name}</span>
                  <span className="shrink-0 text-xs text-muted">{group.count} · {formatPercent(group.cpu)} CPU</span>
                  {group.problems > 0 && <span className="chip chip-signal ml-auto">{group.problems}</span>}
                </button>}
                {open && group.containers.map((container) => <ContainerItem key={container.container_id} container={container} onOpen={() => onOpen(container)} right={<><span className="block text-sm font-medium text-paper">{formatPercent(container.cpu_pct)}</span><span className="block text-xs text-muted">{formatMegabytes(container.memory_used_mb)}</span></>} />)}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ServerActions({ server, isAdmin, onInterval, onClose }) {
  return (
    <Sheet open onClose={onClose} title={server.hostname}>
      <div className="overflow-hidden rounded-lg border border-line">
        <label className="flex min-h-12 items-center justify-between gap-3 border-b border-line px-3.5 text-sm text-paper">Check every
          {isAdmin ? <select className="select h-9" value={server.check_interval_seconds} onChange={(event) => onInterval(server, Number(event.target.value))}>
            {!INTERVALS.some(([value]) => value === server.check_interval_seconds) && <option value={server.check_interval_seconds}>{intervalLabel(server.check_interval_seconds)}</option>}
            {INTERVALS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select> : <span className="text-soft">{intervalLabel(server.check_interval_seconds)}</span>}
        </label>
        <Link to={`/statistics?server=${server.id}`} className="flex h-12 items-center justify-between px-3.5 text-sm font-medium text-paper">Full history in Statistics<Icon name="chevron" size={14} className="text-muted" /></Link>
      </div>
      {isAdmin && <p className="mb-0 mt-3 text-xs leading-5 text-muted">Renaming and removing this server are in its System tab and in Admin.</p>}
    </Sheet>
  )
}

export function PhoneServer({ server, overview, tab, onTab, isAdmin, onInterval, samples, minutes, forecasts, listening, error, children }) {
  const [actions, setActions] = useState(false)
  const [opened, setOpened] = useState(null)
  const state = serverState(server)
  const uptime = server.host?.system_uptime_seconds
  const meta = [shortSystem(server), server.cpu_cores ? `${server.cpu_cores} cores` : null, server.total_ram_gb ? `${Math.round(server.total_ram_gb)} GB` : null, uptime != null ? `up ${formatSpan(uptime)}` : null].filter(Boolean).join(' · ')
  const tabs = [
    ['overview', 'Overview', null],
    ['containers', 'Containers', server.containers?.length || null],
    ['network', 'Network', listening || null],
    ['system', 'System', null],
    ['events', 'Events', null],
  ]
  // The container in the sheet follows the overview poll, so its numbers stay current while it is open.
  const openContainer = opened ? (server.containers || []).find((container) => container.container_id === opened) : null
  return (
    <>
      <PhoneHeader back={{ label: 'Overview', to: '/' }} title={server.hostname}
        subtitle={<span className="flex min-w-0 items-center gap-2"><span className={`dot shrink-0 ${STATE_DOT[state]}`} style={{ width: 7, height: 7 }} /><span className="truncate">{meta || serverStateLabel(server)}</span></span>}
        action={<RoundButton label={`Actions for ${server.hostname}`} icon="more" onClick={() => setActions(true)} />} />
      <div role="tablist" aria-label={`${server.hostname} sections`} className="no-scrollbar sticky top-0 z-10 mt-2 flex gap-6 overflow-x-auto border-b border-line bg-bg px-4">
        {tabs.map(([id, label, count]) => <button key={id} type="button" role="tab" aria-selected={tab === id} aria-controls="server-tabpanel" className="tab shrink-0" onClick={() => onTab(id)}>{label}{count != null && <small>{count}</small>}</button>)}
      </div>
      <div role="tabpanel" id="server-tabpanel" className="px-4 pt-4">
        {error && <div role="alert" className="mb-4 rounded-md border border-warn/40 px-3.5 py-2.5 text-xs text-warn">{error}</div>}
        {tab === 'overview' && (server.host ? <div className="space-y-7">
          <MetricChart server={server} samples={samples} minutes={minutes} />
          <HealthList server={server} />
          <StorageList server={server} overview={overview} forecasts={forecasts} />
          <TopContainers server={server} onAll={() => onTab('containers')} onOpen={(container) => setOpened(container.container_id)} />
        </div> : <p className="m-0 py-10 text-center text-[13px] text-muted">The collector on {server.hostname} has not sent a measurement yet. It shows up here on its first check-in.</p>)}
        {tab === 'containers' && <ContainerList server={server} onOpen={(container) => setOpened(container.container_id)} />}
        {tab !== 'overview' && tab !== 'containers' && <div className="-mx-4 overflow-hidden border-y border-line bg-ink">{children}</div>}
      </div>
      {actions && <ServerActions server={server} isAdmin={isAdmin} onInterval={onInterval} onClose={() => setActions(false)} />}
      {openContainer && <ContainerSheet server={server} container={openContainer} onClose={() => setOpened(null)} />}
    </>
  )
}
