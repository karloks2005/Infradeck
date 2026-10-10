// The extra views of one server: how its load is distributed, when it is busy, how its disks are filling up.
import { useEffect, useMemo, useState } from 'react'
import { TimeSeriesChart } from './charts.jsx'
import { daysUntilFull, formatForecast, histogramBins, seriesFor } from './chartMath.js'
import { ColumnChart, Heatmap } from './distributions.jsx'

const LIVE_RANGES = new Set(['1h', '6h', '24h'])
const REFRESH_SECONDS = 30

// How much of the time a metric spent in each 10% band: weighted by the check-ins behind every point.
function shareOfTime(series) {
  const bins = histogramBins(series.filter((point) => point.avg !== null).map((point) => ({ value: point.avg, weight: point.samples })))
  const total = bins.reduce((sum, bin) => sum + bin.count, 0)
  return bins.map((bin) => ({ label: `${bin.from}–${bin.to}`, values: { share: total ? Math.round((1000 * bin.count) / total) / 10 : 0 } }))
}

export function Distributions({ cpu, memory }) {
  const share = [{ key: 'share', label: 'Share of the time', color: '#7dd3fc' }]
  const memoryShare = [{ key: 'share', label: 'Share of the time', color: '#a78bfa' }]
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ColumnChart title="CPU usage distribution" subtitle="Share of the time the average CPU was in each band (%)" categories={shareOfTime(cpu)} series={share} unit="%" empty="Not enough data for this range." />
      <ColumnChart title="Memory usage distribution" subtitle="Share of the time the memory in use was in each band (%)" categories={shareOfTime(memory)} series={memoryShare} unit="%" empty="Not enough data for this range." />
    </div>
  )
}

export function BusyHours({ request, serverId }) {
  const [data, setData] = useState(null)
  const [metric, setMetric] = useState('cpu')
  const [error, setError] = useState('')
  const offset = -new Date().getTimezoneOffset()

  useEffect(() => {
    const controller = new AbortController()
    request(`/stats/servers/${serverId}/heatmap?metric=${metric}&offset=${offset}`, { method: 'GET', signal: controller.signal })
      .then((result) => { setData(result); setError('') })
      .catch((requestError) => { if (requestError.name !== 'AbortError') setError(requestError.message || 'Could not load the busy hours.') })
    return () => controller.abort()
  }, [request, serverId, metric, offset])

  const current = data && data.server_id === serverId && data.metric === metric ? data : null
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-white">When it is busy</h2>
        <div role="group" aria-label="Metric" className="flex gap-1.5">
          {[['cpu', 'CPU'], ['ram', 'Memory']].map(([id, label]) => (
            <button key={id} type="button" aria-pressed={metric === id} onClick={() => setMetric(id)} className={`min-h-9 rounded-xl border px-3 text-sm ${metric === id ? 'border-white/30 bg-white/10 text-white' : 'border-white/10 text-[#9aa0a6] hover:text-white'}`}>{label}</button>
          ))}
        </div>
      </div>
      {error && <div role="alert" className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
      {current && <Heatmap title={`Average ${metric === 'cpu' ? 'CPU' : 'memory'} by hour of the day`} subtitle="The last 30 days, in your time zone, whatever range is selected above." grid={current.cells} color={metric === 'cpu' ? '#7dd3fc' : '#a78bfa'} empty="Not enough history yet. This fills in as days pass." />}
    </div>
  )
}

function MountChart({ request, serverId, mount, range }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const load = () => request(`/monitoring/servers/${serverId}/mounts/history?mount=${encodeURIComponent(mount.mount)}&range=${range}&metrics=used_pct,used_gb,total_gb`, { method: 'GET', signal: controller.signal })
      .then((result) => { setData(result); setError('') })
      .catch((requestError) => { if (requestError.name !== 'AbortError') setError(requestError.message || 'Could not load this filesystem.') })
    load()
    if (!LIVE_RANGES.has(range)) return () => controller.abort()
    const timer = setInterval(() => { if (!document.hidden) load() }, REFRESH_SECONDS * 1000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [request, serverId, mount.mount, range])

  const current = data && data.mount === mount.mount ? data : null
  const series = useMemo(() => seriesFor(current?.points, 'used_pct'), [current])
  const forecast = useMemo(() => daysUntilFull(series.filter((point) => point.avg !== null).map((point) => ({ t: point.t, value: point.avg }))), [series])
  const common = { bucketSeconds: current?.bucket_seconds ?? 60, start: current ? new Date(current.start).getTime() : undefined, end: current ? new Date(current.end).getTime() : undefined, utc: current ? ['day', 'week', 'month'].includes(current.source) : false }
  return (
    <div className="min-w-0 space-y-2">
      {error && <div role="alert" className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
      <TimeSeriesChart {...common} title={`Disk usage · ${mount.mount}`} series={series} color="#f472b6" domain={[0, 100]} />
      <p className="px-1 text-xs text-[#9aa0a6]">
        {mount.used_gb != null && mount.total_gb != null ? `${mount.used_gb.toFixed(1)} of ${mount.total_gb.toFixed(1)} GB used now. ` : ''}
        {current ? formatForecast(forecast) : ''}
      </p>
    </div>
  )
}

export function Storage({ request, serverId, mounts, range }) {
  if (!mounts.length) return null
  return (
    <div className="space-y-3">
      <h2 className="text-xl font-semibold text-white">Storage</h2>
      <div className="grid gap-4 xl:grid-cols-2">
        {mounts.map((mount) => <MountChart key={mount.mount} request={request} serverId={serverId} mount={mount} range={range} />)}
      </div>
    </div>
  )
}
