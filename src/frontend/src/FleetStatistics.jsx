// The "All servers" view of the Statistics page: how the whole fleet is doing at a glance.
import { useEffect, useMemo, useState } from 'react'
import { BarList, ColumnChart, Donut } from './distributions.jsx'
import { availabilityRows, bucketLabel, containerStateSlices, formatDuration, incidentColumns, isOnline, serversByDisk, topContainers } from './fleetLogic.js'

const LIVE_RANGES = new Set(['1h', '6h', '24h'])
const REFRESH_SECONDS = 30
const SEVERITIES = [
  { key: 'critical', label: 'Critical', color: '#f87171' },
  { key: 'warning', label: 'Warning', color: '#fbbf24' },
  { key: 'info', label: 'Info', color: '#7dd3fc' },
]

function Figure({ label, value, detail }) {
  return (
    <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3">
      <div className="text-xs uppercase tracking-[0.18em] text-[#9aa0a6]">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-white">{value}</div>
      {detail && <div className="text-xs text-[#7f8589]">{detail}</div>}
    </div>
  )
}

export default function FleetStatistics({ request, servers, range }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const load = () => request(`/stats/fleet?range=${range}`, { method: 'GET', signal: controller.signal })
      .then((result) => { setData(result); setError('') })
      .catch((requestError) => { if (requestError.name !== 'AbortError') setError(requestError.message || 'Could not load the fleet statistics.') })
    load()
    if (!LIVE_RANGES.has(range)) return () => controller.abort()
    const timer = setInterval(() => { if (!document.hidden) load() }, REFRESH_SECONDS * 1000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [request, range])

  const incidents = data?.incidents
  const current = data && data.incidents.range === range ? data : null
  const online = servers.filter((server) => isOnline(server)).length
  const containerSlices = useMemo(() => containerStateSlices(servers), [servers])
  const containerTotal = containerSlices.reduce((sum, slice) => sum + slice.value, 0)
  const running = containerSlices.find((slice) => slice.label === 'Running')?.value ?? 0
  const columns = useMemo(() => (current ? incidentColumns(current.incidents.by_bucket, current.incidents.bucket, current.incidents.start, current.incidents.end) : []), [current])
  const categories = columns.map((column) => ({ label: bucketLabel(column.time, current.incidents.bucket), values: column.values }))
  const openNow = incidents ? Object.values(incidents.open_now).reduce((sum, count) => sum + count, 0) : 0

  return (
    <div className="space-y-5">
      {error && <div role="alert" className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Figure label="Servers online" value={`${online} of ${servers.length}`} />
        <Figure label="Containers running" value={`${running} of ${containerTotal}`} />
        <Figure label="Open incidents" value={incidents ? openNow : '–'} />
        <Figure label="Mean time to resolve" value={current ? formatDuration(current.incidents.mean_minutes_to_resolve) : '–'} detail={current ? `${current.incidents.resolved} resolved in this range` : undefined} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Donut title="Containers by state" subtitle="Right now, across all servers" slices={containerSlices} centreLabel="containers" empty="No containers are reported yet." />
        <Donut title="Open incidents by severity" subtitle="Right now" slices={SEVERITIES.map((severity) => ({ label: severity.label, color: severity.color, value: incidents?.open_now[severity.key] ?? 0 }))} centreLabel="open" empty="Nothing is open. All quiet." />
      </div>

      {current && (
        <ColumnChart
          title="Incidents opened"
          subtitle={`${current.incidents.opened} in this range, per ${current.incidents.bucket}${current.incidents.truncated ? ' (only the first part could be counted)' : ''}`}
          categories={categories}
          series={SEVERITIES}
          labelEvery={Math.max(1, Math.ceil(categories.length / 8))}
          empty="No incidents opened in this range."
        />
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {current && <BarList title="What the incidents were about" subtitle="Opened in this range" rows={current.incidents.by_kind.slice(0, 10).map((item) => ({ label: item.kind, value: item.count }))} color="#a78bfa" empty="No incidents opened in this range." />}
        <BarList title="Servers by disk usage" subtitle="Root disk, right now" rows={serversByDisk(servers)} max={100} unit="%" decimals={0} />
        <BarList title="Containers using the most memory" subtitle="Right now" rows={topContainers(servers, 'memory_used_mb')} unit=" MB" color="#a78bfa" />
        <BarList title="Containers using the most CPU" subtitle="Right now" rows={topContainers(servers, 'cpu_pct')} unit="%" decimals={1} color="#34d399" />
      </div>

      {current && (
        <BarList
          title="Availability"
          subtitle="Share of the expected check-ins that arrived. Approximate: it assumes each server's current check interval."
          rows={availabilityRows(current.availability)}
          max={100}
          unit="%"
          decimals={2}
          empty="No server has reported in this range."
        />
      )}
    </div>
  )
}
