// The containers of one server as small cards; selecting one shows its statistics over the chosen range.
import { useEffect, useMemo, useState } from 'react'
import { TimeSeriesChart } from './charts.jsx'
import { describeBucket, seriesFor, summarise } from './chartMath.js'
import { problemRank, sortContainers } from './containerLogic.js'

const LIVE_RANGES = new Set(['1h', '6h', '24h'])
const REFRESH_SECONDS = 30
const METRICS = 'cpu,memory_mb,memory_limit_mb,restarts'
const SORTS = [
  { id: 'problems', label: 'Problems first' },
  { id: 'name', label: 'Name' },
  { id: 'memory', label: 'Memory' },
  { id: 'cpu', label: 'CPU' },
]

function statusColour(container) {
  const rank = problemRank(container)
  if (rank === 0) return container.status === 'restarting' ? 'bg-red-400' : 'bg-amber-400'
  return rank === 1 ? 'bg-red-400' : 'bg-emerald-400'
}

function ContainerCard({ container, selected, onSelect }) {
  const memoryPct = container.memory_pct
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`min-w-0 rounded-2xl border p-3 text-left transition ${selected ? 'border-white/40 bg-white/10' : 'border-[#1f2326] bg-[#0d1013]/60 hover:border-white/20'}`}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusColour(container)}`} />
        <span className="truncate text-sm font-medium text-white">{container.name}</span>
      </div>
      <div className="mt-1 truncate text-xs text-[#7f8589]">
        {container.status}{container.healthcheck_status ? ` · ${container.healthcheck_status}` : ''}{container.restart_count > 0 ? ` · ${container.restart_count} restarts` : ''}
      </div>
      <div className="mt-2 flex items-baseline justify-between text-xs text-[#9aa0a6]">
        <span>CPU <span className="text-sm text-white">{container.cpu_pct == null ? '–' : `${container.cpu_pct}%`}</span></span>
        <span>Memory <span className="text-sm text-white">{container.memory_used_mb == null ? '–' : `${Math.round(container.memory_used_mb)} MB`}</span></span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10" title={memoryPct == null ? 'No memory limit' : `${memoryPct}% of its memory limit`}>
        {memoryPct != null && <div className={`h-full rounded-full ${memoryPct >= 90 ? 'bg-red-400' : memoryPct >= 75 ? 'bg-amber-400' : 'bg-emerald-400'}`} style={{ width: `${Math.min(Math.max(memoryPct, 0), 100)}%` }} />}
      </div>
    </button>
  )
}

function Figure({ label, value }) {
  return (
    <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3">
      <div className="text-xs uppercase tracking-[0.18em] text-[#9aa0a6]">{label}</div>
      <div className="mt-1 text-xl font-semibold text-white">{value}</div>
    </div>
  )
}

function ContainerDetail({ request, serverId, container, range, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const load = () => request(`/monitoring/servers/${serverId}/containers/${encodeURIComponent(container.container_id)}/history?range=${range}&metrics=${METRICS}`, { method: 'GET', signal: controller.signal })
      .then((result) => { setData(result); setError('') })
      .catch((requestError) => { if (requestError.name !== 'AbortError') setError(requestError.message || 'Could not load this container.') })
    load()
    if (!LIVE_RANGES.has(range)) return () => controller.abort()
    const timer = setInterval(() => { if (!document.hidden) load() }, REFRESH_SECONDS * 1000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [request, serverId, container.container_id, range])

  const current = data && data.container_id === container.container_id ? data : null
  const series = useMemo(() => ({ cpu: seriesFor(current?.points, 'cpu'), memory: seriesFor(current?.points, 'memory_mb') }), [current])
  const limit = useMemo(() => Math.max(0, ...(current?.points || []).map((point) => point.memory_limit_mb?.max ?? 0)) || null, [current])
  const figures = useMemo(() => {
    const points = current?.points || []
    const samples = points.reduce((total, point) => total + point.samples, 0)
    const down = points.reduce((total, point) => total + (point.down || 0), 0)
    const restarts = points.map((point) => point.restarts?.max).filter((value) => value != null)
    return { samples, notRunning: samples ? (100 * down) / samples : null, restarts: restarts.length ? restarts[restarts.length - 1] - restarts[0] : null }
  }, [current])
  const cpu = summarise(series.cpu)
  const memory = summarise(series.memory)
  const common = { bucketSeconds: current?.bucket_seconds ?? 60, start: current ? new Date(current.start).getTime() : undefined, end: current ? new Date(current.end).getTime() : undefined, utc: current ? ['day', 'week', 'month'].includes(current.source) : false }

  return (
    <section aria-label={`Statistics for ${container.name}`} className="space-y-4 rounded-3xl border border-white/10 bg-[#0d1013]/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-lg font-semibold text-white">{container.name}</h3>
          <div className="truncate text-xs text-[#7f8589]">{container.image || 'unknown image'}</div>
        </div>
        <button type="button" onClick={onClose} className="shrink-0 rounded-xl border border-white/10 px-3 py-1.5 text-xs text-[#9aa0a6] hover:text-white">Close</button>
      </div>
      {error && <div role="alert" className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
      {current && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Figure label="Average CPU" value={cpu.average == null ? '–' : `${cpu.average.toFixed(1)}%`} />
            <Figure label="Peak memory" value={memory.maximum == null ? '–' : `${Math.round(memory.maximum)} MB`} />
            <Figure label="Not running" value={figures.notRunning == null ? '–' : `${figures.notRunning.toFixed(1)}% of the time`} />
            <Figure label="Restarts in range" value={figures.restarts == null ? '–' : figures.restarts} />
          </div>
          <p className="text-xs text-[#7f8589]">{current.points.length ? `One point per ${describeBucket(current.bucket_seconds)}. A container only reports CPU and memory while it is running.` : 'Nothing recorded for this container in this range.'}</p>
          <div className="grid gap-4 xl:grid-cols-2">
            <TimeSeriesChart {...common} title="CPU usage" series={series.cpu} color="#34d399" />
            <TimeSeriesChart {...common} title="Memory used" series={series.memory} color="#a78bfa" unit="MB" decimals={0} limit={limit} />
          </div>
        </>
      )}
    </section>
  )
}

export default function ContainerStatistics({ request, serverId, containers, range, selectedId, onSelect }) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('problems')
  const shown = useMemo(() => sortContainers(containers.filter((container) => container.name.toLowerCase().includes(query.trim().toLowerCase())), sort), [containers, query, sort])
  const selected = containers.find((container) => container.container_id === selectedId) || null
  const problems = containers.filter((container) => problemRank(container) < 2).length

  return (
    <section aria-label="Containers" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-white">Containers</h2>
          <p className="text-xs text-[#7f8589]">{containers.length} on this server{problems ? ` · ${problems} need attention` : ''}. Select one for its statistics.</p>
        </div>
        {containers.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <input type="search" aria-label="Search containers" placeholder="Search" value={query} onChange={(event) => setQuery(event.target.value)} className="min-h-11 w-40 rounded-xl border border-white/10 bg-[#111417] px-3 text-sm text-white sm:min-h-0 sm:py-2" />
            <select aria-label="Sort containers" value={sort} onChange={(event) => setSort(event.target.value)} className="min-h-11 rounded-xl border border-white/10 bg-[#111417] px-3 text-sm text-white sm:min-h-0 sm:py-2">
              {SORTS.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
            </select>
          </div>
        )}
      </div>
      {containers.length === 0 && <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-6 text-sm text-[#9aa0a6]">This server has no containers (or its collector has no Docker access).</div>}
      {containers.length > 0 && shown.length === 0 && <div className="text-sm text-[#7f8589]">No container matches “{query}”.</div>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((container) => <ContainerCard key={container.container_id} container={container} selected={container.container_id === selectedId} onSelect={() => onSelect(container.container_id === selectedId ? '' : container.container_id)} />)}
      </div>
      {selected && <ContainerDetail request={request} serverId={serverId} container={selected} range={range} onClose={() => onSelect('')} />}
    </section>
  )
}
