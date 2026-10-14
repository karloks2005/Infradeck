// The Statistics page: pick a server and a range, read the two or three things that stand out, then one shared
// timeline, when it is busy, how the disks fill up and which containers use the most.
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { describeBucket } from '../chartMath.js'
import { forecastKey, useForecasts, usePolled } from '../dashboard/data.js'
import { forecastMonth, forecastPhrase, formatPercent, fullestMount, serverMounts, serverState, sortServers } from '../dashboard/logic.js'
import FleetStatistics from '../FleetStatistics.jsx'
import { Icon } from '../icons.jsx'
import { useOverview } from '../overviewStore.js'
import { AppShell } from '../shell.jsx'
import { apiRequest } from '../api.js'
import { ContainersTable } from './ContainersTable.jsx'
import { BusyHours, Disks, Reliability, TimeAtLevels } from './Insights.jsx'
import { Timeline } from './Timeline.jsx'
import { RANGES, RANGE_BY_ID, deltaPoints, eventMarks, findGaps, findings, formatMoment, summarize, timelineCsv } from './logic.js'

const DEFAULT_RANGE = '7d'
const RANGE_KEY = 'infradeck.statisticsRange'
const COMPARE_KEY = 'infradeck.statistics.compare'
// Only short ranges change from one minute to the next.
const LIVE = new Set(['1h', '6h', '24h'])
const METRICS = ['cpu', 'ram', 'swap', 'load', 'disk', 'docker_ms', 'collect_ms']
const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const SOURCES = { detailed: 'check-ins grouped together', hour: 'hourly summaries', day: 'daily summaries', week: 'weekly summaries (UTC)', month: 'monthly summaries (UTC)' }

function remembered(key, fallback, allowed) {
  try {
    const stored = window.localStorage.getItem(key)
    return stored !== null && (!allowed || allowed(stored)) ? stored : fallback
  } catch {
    return fallback
  }
}
function remember(key, value) {
  try { window.localStorage.setItem(key, value) } catch { /* the choice just isn't remembered */ }
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function Kpi({ label, value, tone, children }) {
  return (
    <div className="min-w-0 px-[18px] py-4">
      <div className="text-xs text-soft">{label}</div>
      <div className={`mt-2.5 text-2xl font-semibold leading-none ${tone === 'warn' ? 'text-warn' : tone === 'signal' ? 'text-signal' : 'text-paper'}`}>{value}</div>
      <div className="mt-3 min-h-4 text-xs leading-4 text-muted">{children}</div>
    </div>
  )
}

const parse = (points) => (points || []).map((point) => ({ ...point, t: new Date(point.time).getTime() }))
const metric = (points, key) => points.map((point) => ({ t: point.t, avg: point[key]?.avg ?? null, max: point[key]?.max ?? null, samples: point.samples })).filter((point) => point.avg !== null || point.max !== null)

function ServerStatistics({ server, overview, range, compare, containerId, choose }) {
  const [focus, setFocus] = useState(null)
  const base = `/monitoring/servers/${server.id}/history?range=${range.id}&metrics=${METRICS.join(',')}`
  const { data, error } = usePolled(base, LIVE.has(range.id) ? 30 : null)
  const { data: before } = usePolled(compare && range.previous ? `${base}&previous=true` : null, null)
  const { data: busiestData } = usePolled(`/stats/servers/${server.id}/busiest?range=${range.id}`, LIVE.has(range.id) ? 60 : null)
  const { data: usage } = usePolled(`/stats/servers/${server.id}/containers?range=${range.id}`, LIVE.has(range.id) ? 60 : null)
  const { data: spikeData } = usePolled(`/stats/servers/${server.id}/spikes?range=${range.id}`, LIVE.has(range.id) ? 60 : null)
  const { data: eventData } = usePolled(`/monitoring/servers/${server.id}/events?days=${range.days}`, LIVE.has(range.id) ? 60 : null)
  const mounts = useMemo(() => serverMounts(server).filter((mount) => mount.total_gb != null), [server])
  const forecasts = useForecasts(mounts.map((mount) => ({ serverId: server.id, mount: mount.mount })))

  const points = useMemo(() => parse(data?.points), [data])
  const previous = useMemo(() => {
    if (!before || !data) return null
    const shift = new Date(data.end).getTime() - new Date(before.end).getTime()
    return parse(before.points).map((point) => ({ ...point, t: point.t + shift }))
  }, [before, data])
  const series = useMemo(() => Object.fromEntries(METRICS.map((key) => [key, metric(points, key)])), [points])
  const earlier = useMemo(() => (previous ? { cpu: summarize(metric(previous, 'cpu')), ram: summarize(metric(previous, 'ram')) } : null), [previous])
  const busiest = useMemo(() => (busiestData ? { bucketSeconds: busiestData.bucket_seconds, points: busiestData.points.map((point) => ({ ...point, t: new Date(point.time).getTime() })) } : null), [busiestData])

  const start = data ? new Date(data.start).getTime() : 0
  const end = data ? new Date(data.end).getTime() : 0
  const bucket = data?.bucket_seconds ?? 60
  const utc = data ? ['day', 'week', 'month'].includes(data.source) : false
  const gaps = useMemo(() => findGaps(points.map((point) => point.t), bucket, end), [points, bucket, end])
  const disks = mounts.map((mount) => ({ mount: mount.mount, usedPct: mount.used_pct, days: forecasts[forecastKey(server.id, mount.mount)] }))
  const noticed = useMemo(() => (points.length ? findings({ cpu: series.cpu, ram: series.ram, swap: series.swap, load: series.load, cores: server.cpu_cores, swapTotalGb: server.system?.swap_total_gb, bucketSeconds: bucket, gaps, events: eventData?.events, busiest, containers: server.containers, disks, spikes: spikeData?.spikes?.map((spike) => ({ ...spike, t: new Date(spike.time).getTime() })) }) : []),
    // The forecasts arrive one by one; their values are what matters, not the array's identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [series, bucket, gaps, eventData, busiest, server, spikeData, JSON.stringify(disks)])
  const marks = useMemo(() => eventMarks(eventData?.events, start, end, noticed.flatMap((finding) => finding.marks || [])), [eventData, start, end, noticed])

  if (error && !data) return <div role="alert" className="rounded-md border border-warn/40 px-3.5 py-2.5 text-xs text-warn">{error}</div>
  if (!data) return <p className="m-0 py-20 text-center text-[13px] text-muted">Loading the history of {server.hostname}…</p>

  const cpu = summarize(series.cpu)
  const ram = summarize(series.ram)
  const swap = summarize(series.swap)
  const load = summarize(series.load)
  const swapFinding = noticed.find((finding) => finding.key === 'swap')
  const root = mounts.find((mount) => mount.mount === '/') || fullestMount(mounts)
  const rootDays = root ? forecasts[forecastKey(server.id, root.mount)] : null
  const cores = server.cpu_cores || 1
  const swapWarn = overview.alert_thresholds?.swap_warning_pct ?? 50
  const versus = range.short ? `vs ${range.short}` : ''
  const jump = (t) => setFocus({ t, nonce: Date.now() })
  const rows = [
    { key: 'cpu', label: 'CPU', pair: true, format: formatPercent, axis: (tick) => `${tick}%`, max: 100, sub: `avg ${formatPercent(cpu.average)} · peak ${formatPercent(cpu.peak)}` },
    { key: 'ram', label: 'Memory', format: formatPercent, axis: (tick) => `${tick}%`, max: 100, sub: `avg ${formatPercent(ram.average)}${server.total_ram_gb ? ` · of ${Number(server.total_ram_gb.toFixed(1))} GB` : ''}` },
    { key: 'swap', label: 'Swap', format: formatPercent, axis: (tick) => `${Number(tick.toFixed(1))}%`, floor: 20 / 1.15, tone: 'warn', sub: `now ${formatPercent(swap.last)}${server.system?.swap_total_gb ? ` · of ${Number(server.system.swap_total_gb.toFixed(1))} GB` : ''}`, note: swapFinding ? { t: swapFinding.at, text: swapFinding.note } : null },
    { key: 'load', label: 'Load (1 min)', short: 'Load', format: (value) => value.toFixed(2), axis: (tick) => String(Number(tick.toFixed(1))), floor: cores * 1.3, limit: { value: cores, label: `${cores} ${cores === 1 ? 'core' : 'cores'}` }, sub: `${cores} ${cores === 1 ? 'core' : 'cores'} · avg ${load.average == null ? '–' : load.average.toFixed(2)}` },
  ]

  return <>
    <section className="card grid grid-cols-2 divide-x divide-y divide-line overflow-hidden md:grid-cols-3 xl:grid-cols-6 xl:divide-y-0" aria-label="Headline figures">
      <Kpi label="Average CPU" value={formatPercent(cpu.average)}>{earlier ? `${deltaPoints(cpu.average, earlier.cpu.average) ?? 'no data'} ${versus}` : `over the last ${range.words}`}</Kpi>
      <Kpi label="Peak CPU" value={formatPercent(cpu.peak)}>{cpu.peakAt != null && <>{formatMoment(cpu.peakAt, { utc })} · <button type="button" className="link" onClick={() => jump(cpu.peakAt)}>jump to</button></>}</Kpi>
      <Kpi label="Average memory" value={formatPercent(ram.average)}>{[server.total_ram_gb && ram.average != null ? `${Number(((ram.average / 100) * server.total_ram_gb).toFixed(1))} of ${Number(server.total_ram_gb.toFixed(1))} GB` : null, earlier ? deltaPoints(ram.average, earlier.ram.average) : null].filter(Boolean).join(' · ')}</Kpi>
      <Kpi label="Peak memory" value={formatPercent(ram.peak)}>{ram.peakAt != null && <>{formatMoment(ram.peakAt, { utc })} · <button type="button" className="link" onClick={() => jump(ram.peakAt)}>jump to</button></>}</Kpi>
      <Kpi label="Swap now" value={formatPercent(server.host?.swap_pct ?? swap.last)} tone={(server.host?.swap_pct ?? swap.last ?? 0) >= Math.min(swapWarn, 10) && swapFinding ? 'warn' : (server.host?.swap_pct ?? 0) >= swapWarn ? 'warn' : undefined}>{swapFinding ? `Climbing since ${formatMoment(swapFinding.at, { clock: false })}` : swap.peak != null ? `peak ${formatPercent(swap.peak)} in this range` : ''}</Kpi>
      <Kpi label={root?.mount === '/' || !root ? 'Root disk' : `Disk ${root.mount}`} value={formatPercent(root?.used_pct ?? server.host?.disk_usage_pct)}>{rootDays != null ? (forecastMonth(rootDays) ? `F${forecastMonth(rootDays).slice(1)}` : forecastPhrase(rootDays) || 'No clear growth trend') : 'No clear growth trend'}</Kpi>
    </section>

    <section className="card overflow-hidden" aria-label="Noticed in this range">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-[18px] py-3"><h2 className="m-0 text-sm font-semibold text-paper">Noticed in this range</h2><span className="text-xs text-muted">Computed from the data below. Nothing is guessed.</span></div>
      {noticed.length ? <ol className="m-0 list-none divide-y divide-line p-0">
        {noticed.map((finding, position) => (
          <li key={finding.key} className="grid grid-cols-[30px_minmax(0,1fr)] items-start gap-x-2 px-[18px] py-3.5 sm:grid-cols-[38px_minmax(0,1fr)_auto]">
            <span className="mono pt-0.5 text-dim">{String(position + 1).padStart(2, '0')}</span>
            <span className="min-w-0"><span className="block text-[13px] font-semibold text-paper">{finding.title}</span><span className="mt-1 block text-[13px] leading-5 text-soft">{finding.text}</span></span>
            {finding.at != null && <button type="button" className="btn btn-sm col-start-2 mt-2 justify-self-start sm:col-start-3 sm:mt-0" onClick={() => jump(finding.at)}>Show on timeline</button>}
          </li>
        ))}
      </ol> : <p className="m-0 px-[18px] py-5 text-[13px] text-soft">{points.length ? `Nothing stands out over the last ${range.words}: no growing swap, no repeating spikes, no gaps and no disk about to fill up.` : 'Nothing has been recorded for this server in this range.'}</p>}
    </section>

    <Timeline rows={rows} points={points} previous={previous} start={start} end={end} bucketSeconds={bucket} gaps={gaps} marks={marks} busiest={busiest} utc={utc} focus={focus}
      note={points.length ? <span title={`Built from ${SOURCES[data.source] || data.source}.`}>{points.length} {points.length === 1 ? 'point' : 'points'}, one per {describeBucket(bucket)}. Line is the average, the band reaches the highest value in each {describeBucket(bucket)}.{utc ? ' Dates are UTC.' : ''}</span> : ''}
      legend={{ current: `Last ${range.words}`, previous: previous ? `The ${range.previous}` : null }} />

    <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <BusyHours key={server.id} serverId={server.id} />
      <TimeAtLevels cpu={series.cpu} ram={series.ram} words={range.span} />
    </div>

    <Disks server={server} mounts={mounts} range={range} forecasts={forecasts} />
    <Reliability docker={series.docker_ms} collect={series.collect_ms} start={start} end={end} bucketSeconds={bucket} gaps={gaps} utc={utc} interval={server.check_interval_seconds} />
    <ContainersTable key={server.id} server={server} usage={usage && usage.server_id === server.id ? usage : null} range={range} compare={compare} selectedId={containerId} onSelect={(id) => choose({ container: id })} />
  </>
}

export default function StatisticsPage({ user, onLogout }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: overview, error } = useOverview(Boolean(user), 30, onLogout)
  const [compare, setCompare] = useState(() => remembered(COMPARE_KEY, 'on') === 'on')
  const servers = useMemo(() => sortServers(overview?.servers || []), [overview])
  const rangeId = RANGE_BY_ID[searchParams.get('range')] ? searchParams.get('range') : remembered(RANGE_KEY, DEFAULT_RANGE, (value) => Boolean(RANGE_BY_ID[value]))
  const range = RANGE_BY_ID[rangeId]
  const wantsAll = searchParams.get('server') === 'all'
  const requested = Number(searchParams.get('server'))
  const server = wantsAll ? null : servers.find((item) => item.id === requested) || servers.find((item) => item.is_dashboard_host) || servers[0] || null

  const choose = useCallback((changes) => {
    const next = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(changes)) {
      if (value === '' || value === null) next.delete(key)
      else next.set(key, String(value))
    }
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const exportCsv = async () => {
    if (!server) return
    const data = await apiRequest(`/monitoring/servers/${server.id}/history?range=${range.id}&metrics=${METRICS.join(',')}`, { method: 'GET' })
    download(`${server.hostname}-${range.id}.csv`, timelineCsv(data.points, METRICS))
  }

  const crumbs = [{ label: 'Statistics', to: '/statistics' }, { label: wantsAll ? 'All servers' : server?.hostname || '…' }]
  return (
    <AppShell user={user} onLogout={onLogout} crumbs={servers.length ? crumbs : undefined} bare>
      <div className="space-y-6 px-3 pt-5 sm:px-4 lg:px-7 lg:pt-7">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="m-0 text-[21px] font-semibold leading-7 text-paper">Statistics</h1>
            <p className="mb-0 mt-1.5 text-[13px] text-soft">{wantsAll ? 'How the whole fleet is doing: incidents, availability and who uses the most.' : 'How a server behaved over time. Hover anywhere on the timeline to read every metric at that moment.'}</p>
          </div>
          {server && <button type="button" className="btn" onClick={exportCsv}><Icon name="download" size={14} />Export CSV</button>}
        </header>

        {error && !overview && <div role="alert" className="rounded-md border border-warn/40 px-3.5 py-2.5 text-xs text-warn">{error}</div>}
        {overview && !servers.length && (
          <div className="card px-[18px] py-10 text-center">
            <p className="m-0 text-sm font-medium text-paper">No server is reporting yet.</p>
            <p className="mb-0 mt-2 text-[13px] text-muted">History appears here once a collector has checked in. <Link to="/" className="link">Connect a server on the Overview</Link>.</p>
          </div>
        )}

        {servers.length > 0 && <>
          <div className="card flex flex-wrap items-center gap-x-6 gap-y-3 px-[14px] py-2.5">
            <div className="flex items-center gap-3"><span className="text-xs text-muted">Server</span>
              <div className="seg flex-wrap" role="group" aria-label="Server">
                <button type="button" aria-pressed={wantsAll} onClick={() => choose({ server: 'all', container: '' })}>All</button>
                {servers.map((item) => <button key={item.id} type="button" aria-pressed={server?.id === item.id} onClick={() => choose({ server: item.id, container: '' })} className="!inline-flex items-center gap-2"><span className={`dot ${STATE_DOT[serverState(item)]}`} style={{ width: 6, height: 6 }} />{item.hostname}</button>)}
              </div>
            </div>
            <div className="flex items-center gap-3"><span className="text-xs text-muted">Range</span>
              <div className="seg flex-wrap" role="group" aria-label="Time range">
                {RANGES.map((option) => <button key={option.id} type="button" aria-pressed={range.id === option.id} onClick={() => { remember(RANGE_KEY, option.id); choose({ range: option.id }) }}>{option.label}</button>)}
              </div>
            </div>
            {!wantsAll && <label className={`ml-auto flex items-center gap-2.5 text-xs ${range.previous ? 'cursor-pointer text-soft' : 'text-dim'}`}>
              <input type="checkbox" role="switch" className="peer sr-only" checked={compare && Boolean(range.previous)} disabled={!range.previous} onChange={(event) => { setCompare(event.target.checked); remember(COMPARE_KEY, event.target.checked ? 'on' : 'off') }} />
              <span aria-hidden="true" className="relative h-[18px] w-8 rounded-full border border-edge bg-raised transition-colors after:absolute after:left-[2px] after:top-[2px] after:h-3 after:w-3 after:rounded-full after:bg-muted after:transition-all peer-checked:border-paper peer-checked:bg-paper peer-checked:after:left-[16px] peer-checked:after:bg-bg peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-paper" />
              {range.previous ? `Compare with ${range.previous}` : 'Nothing before “All” to compare with'}
            </label>}
          </div>

          {wantsAll ? <FleetStatistics request={apiRequest} servers={servers} range={range.id} />
            : server && <ServerStatistics key={`${server.id}-${range.id}`} server={server} overview={overview} range={range} compare={compare} containerId={searchParams.get('container') || ''} choose={choose} />}
        </>}
      </div>
    </AppShell>
  )
}
