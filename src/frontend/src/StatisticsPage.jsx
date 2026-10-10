// The Statistics page: pick a server and a time range and see how it behaved, from the last hour to all time.
// It only needs a way to call the API, so it has no import from App.jsx.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TimeSeriesChart } from './charts.jsx'
import ContainerStatistics from './ContainerStatistics.jsx'
import FleetStatistics from './FleetStatistics.jsx'
import { BusyHours, Distributions, Storage } from './ServerInsights.jsx'
import { describeBucket, formatDateTime, seriesFor, summarise } from './chartMath.js'

const RANGES = [
  { id: '1h', label: '1 hour' },
  { id: '6h', label: '6 hours' },
  { id: '24h', label: '24 hours' },
  { id: '7d', label: '7 days' },
  { id: '30d', label: '30 days' },
  { id: '90d', label: '90 days' },
  { id: '1y', label: '1 year' },
  { id: 'all', label: 'All time' },
]
const RANGE_IDS = new Set(RANGES.map((range) => range.id))
const DEFAULT_RANGE = '24h'
const RANGE_STORAGE_KEY = 'homelab.statisticsRange'
// Only short ranges change from one minute to the next.
const LIVE_RANGES = new Set(['1h', '6h', '24h'])
const REFRESH_SECONDS = 30
const METRICS = 'cpu,ram,swap,load,disk,docker_ms,collect_ms'
const SOURCES = {
  detailed: 'check-ins grouped together',
  hour: 'hourly summaries',
  day: 'daily summaries',
  week: 'weekly summaries (Monday to Sunday, UTC)',
  month: 'monthly summaries (UTC)',
}

function rememberedRange() {
  try {
    const stored = window.localStorage.getItem(RANGE_STORAGE_KEY)
    return RANGE_IDS.has(stored) ? stored : DEFAULT_RANGE
  } catch {
    return DEFAULT_RANGE
  }
}

function SummaryCard({ label, value, detail }) {
  return (
    <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3">
      <div className="text-xs uppercase tracking-[0.18em] text-[#9aa0a6]">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-white">{value}</div>
      {detail && <div className="text-xs text-[#7f8589]">{detail}</div>}
    </div>
  )
}

const percent = (value) => (value === null || value === undefined ? '–' : `${value.toFixed(1)}%`)

export default function StatisticsContent({ request }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [servers, setServers] = useState(null)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const range = RANGE_IDS.has(searchParams.get('range')) ? searchParams.get('range') : rememberedRange()
  const requestedServer = Number(searchParams.get('server'))
  const wantsAll = searchParams.get('server') === 'all'
  const serverId = useMemo(() => {
    if (!servers?.length || wantsAll) return null
    return servers.find((server) => server.id === requestedServer)?.id ?? (servers.find((server) => server.is_dashboard_host) || servers[0]).id
  }, [servers, requestedServer, wantsAll])

  const choose = useCallback((changes) => {
    const next = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(changes)) {
      if (value === '' || value === null) next.delete(key)
      else next.set(key, String(value))
    }
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    let cancelled = false
    const load = () => request('/monitoring/overview', { method: 'GET' })
      .then((overview) => { if (!cancelled) setServers((overview.servers || []).map((server) => ({ ...server, is_dashboard_host: Boolean(server.is_dashboard_host), containers: server.containers || [] }))) })
      .catch((requestError) => { if (!cancelled) { setServers((current) => current || []); setError(requestError.message || 'Could not load the servers.') } })
    load()
    // The containers on the cards change, so keep them current, but not while the tab is hidden.
    const timer = setInterval(() => { if (!document.hidden) load() }, REFRESH_SECONDS * 1000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [request])

  useEffect(() => {
    if (serverId === null) return undefined
    const controller = new AbortController()
    const load = (showSpinner) => {
      if (showSpinner) setLoading(true)
      request(`/monitoring/servers/${serverId}/history?range=${range}&metrics=${METRICS}`, { method: 'GET', signal: controller.signal })
        .then((result) => { setData(result); setError('') })
        .catch((requestError) => { if (requestError.name !== 'AbortError') setError(requestError.message || 'Could not load the statistics.') })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }
    load(true)
    if (!LIVE_RANGES.has(range)) return () => controller.abort()
    // Refresh the short ranges, but not while the tab is hidden.
    const timer = setInterval(() => { if (!document.hidden) load(false) }, REFRESH_SECONDS * 1000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [request, serverId, range])

  const series = useMemo(() => Object.fromEntries(['cpu', 'ram', 'swap', 'load', 'disk', 'docker_ms', 'collect_ms'].map((metric) => [metric, seriesFor(data?.points, metric)])), [data])
  const cpu = summarise(series.cpu)
  const memory = summarise(series.ram)
  const start = data ? new Date(data.start).getTime() : undefined
  const end = data ? new Date(data.end).getTime() : undefined
  const utc = data ? ['day', 'week', 'month'].includes(data.source) : false
  const common = { bucketSeconds: data?.bucket_seconds ?? 60, start, end, utc }
  const ready = data && data.server_id === serverId

  return (
    <div className="space-y-5">
      <div>
        <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">History</div>
        <h1 className="mt-1 text-3xl font-semibold text-white">Statistics</h1>
        <p className="mt-2 text-sm text-[#9aa0a6]">How a server behaved over time, from the last hour to everything that has been recorded.</p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-xs text-[#9aa0a6]">
          Server
          <select
            value={wantsAll ? 'all' : serverId ?? ''}
            onChange={(event) => choose({ server: event.target.value, container: '' })}
            disabled={!servers?.length}
            className="min-h-11 rounded-xl border border-white/10 bg-[#111417] px-3 py-2 text-sm text-white sm:min-h-0"
          >
            {servers?.length > 0 && <option value="all">All servers</option>}
            {(servers || []).map((server) => <option key={server.id} value={server.id}>{server.hostname}{server.is_dashboard_host ? ' (this machine)' : ''}</option>)}
          </select>
        </label>
        <div role="group" aria-label="Time range" className="flex flex-wrap gap-1.5">
          {RANGES.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={range === option.id}
              onClick={() => {
                try { window.localStorage.setItem(RANGE_STORAGE_KEY, option.id) } catch { /* the choice just isn't remembered */ }
                choose({ range: option.id })
              }}
              className={`min-h-11 rounded-xl border px-3 text-sm transition sm:min-h-9 ${range === option.id ? 'border-white/30 bg-white/10 text-white' : 'border-white/10 text-[#9aa0a6] hover:text-white'}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div role="alert" className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
      {servers !== null && servers.length === 0 && !error && <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-6 text-sm text-[#9aa0a6]">No servers are reporting yet. Add one from the dashboard and its history will appear here.</div>}

      {servers?.length > 0 && wantsAll && <FleetStatistics request={request} servers={servers} range={range} />}

      {ready && (
        <div className={loading ? 'space-y-5 opacity-60 transition-opacity' : 'space-y-5 transition-opacity'}>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <SummaryCard label="Average CPU" value={percent(cpu.average)} />
            <SummaryCard label="Peak CPU" value={percent(cpu.maximum)} />
            <SummaryCard label="Average memory" value={percent(memory.average)} />
            <SummaryCard label="Peak memory" value={percent(memory.maximum)} />
          </div>
          <p className="text-xs text-[#7f8589]">
            {data.points.length
              ? `${data.points.length} ${data.points.length === 1 ? 'point' : 'points'}, one per ${describeBucket(data.bucket_seconds)} (${SOURCES[data.source] || data.source}). The line is the average and the lighter band reaches up to the highest value. `
              : 'Nothing has been recorded for this server in this range. '}
            {data.data_since && `History starts ${formatDateTime(new Date(data.data_since).getTime(), utc)}. `}
            {utc ? 'Dates are UTC.' : 'Times are in your time zone.'}
          </p>
          <div className="grid gap-4 xl:grid-cols-2">
            <TimeSeriesChart {...common} title="CPU usage" series={series.cpu} color="#7dd3fc" domain={[0, 100]} />
            <TimeSeriesChart {...common} title="Memory used" series={series.ram} color="#a78bfa" domain={[0, 100]} />
            <TimeSeriesChart {...common} title="Swap used" series={series.swap} color="#fbbf24" domain={[0, 100]} />
            <TimeSeriesChart {...common} title="Load (1 minute average)" series={series.load} color="#34d399" unit="" decimals={2} />
            <TimeSeriesChart {...common} title="Root disk used" series={series.disk} color="#f472b6" domain={[0, 100]} />
          </div>
          <Distributions cpu={series.cpu} memory={series.ram} />
          <BusyHours request={request} serverId={serverId} />
          <Storage request={request} serverId={serverId} mounts={servers.find((server) => server.id === serverId)?.mounts || []} range={range} />
          {(series.docker_ms.length > 0 || series.collect_ms.length > 0) && (
            <div className="space-y-3">
              <h2 className="text-xl font-semibold text-white">Reliability</h2>
              <div className="grid gap-4 xl:grid-cols-2">
                <TimeSeriesChart {...common} title="Docker response time (listing containers)" series={series.docker_ms} color="#fb923c" unit="ms" decimals={0} />
                <TimeSeriesChart {...common} title="Time the collector takes per check-in" series={series.collect_ms} color="#94a3b8" unit="ms" decimals={0} />
              </div>
            </div>
          )}
        </div>
      )}
      {servers?.length > 0 && serverId !== null && !wantsAll && (
        <ContainerStatistics
          request={request}
          serverId={serverId}
          containers={servers.find((server) => server.id === serverId)?.containers || []}
          range={range}
          selectedId={searchParams.get('container') || ''}
          onSelect={(containerId) => choose({ container: containerId })}
        />
      )}
      {servers === null && !error && <div className="text-sm text-[#7f8589]">Loading…</div>}
    </div>
  )
}
