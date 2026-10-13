// A ranked table instead of a card per container: the ones that matter float to the top. Selecting a row opens
// that container's own history on the same kind of timeline.
import { useMemo, useState } from 'react'
import { describeBucket } from '../chartMath.js'
import { Sparkline } from '../dashboard/charts.jsx'
import { usePolled } from '../dashboard/data.js'
import { containerUptime, formatMegabytes, formatPercent } from '../dashboard/logic.js'
import { Caret, Icon } from '../icons.jsx'
import { Timeline } from './Timeline.jsx'
import { findGaps } from './logic.js'

const COLUMNS = 'grid-cols-[38px_minmax(150px,1.2fr)_minmax(120px,1fr)_84px_84px_104px_112px_80px_76px_70px]'
const SORTS = [
  ['avg_cpu', 'average CPU'], ['max_cpu', 'peak CPU'], ['avg_memory_mb', 'average memory'], ['max_memory_mb', 'peak memory'], ['swap', 'swap'], ['restarts', 'restarts'], ['name', 'name'],
]
const LIVE = new Set(['1h', '6h', '24h'])
const SHOWN = 8
const megabytes = (value) => formatMegabytes(value)

function ContainerHistory({ serverId, container, range, compare }) {
  const base = `/monitoring/servers/${serverId}/containers/${encodeURIComponent(container.container_id)}/history?range=${range.id}&metrics=cpu,memory_mb,restarts`
  const { data, error } = usePolled(base, LIVE.has(range.id) ? 30 : null)
  const { data: before } = usePolled(compare && range.previous ? `${base}&previous=true` : null, null)
  // Memory is drawn in GB once it passes one, so the axis reads 4 GB rather than 4096 MB. The restart count is a
  // running total with no average, so its highest value in each period is the value.
  const inGb = useMemo(() => Math.max(0, ...(data?.points || []).map((point) => point.memory_mb?.max ?? 0)) >= 1024, [data])
  const shape = (point, shift = 0) => {
    const scale = (value) => (value == null ? null : inGb ? value / 1024 : value)
    return { ...point, t: new Date(point.time).getTime() + shift, memory: point.memory_mb ? { avg: scale(point.memory_mb.avg ?? null), max: scale(point.memory_mb.max ?? null) } : null, restarts: point.restarts?.max != null ? { avg: point.restarts.max, max: point.restarts.max } : null }
  }
  const points = useMemo(() => (data?.points || []).map((point) => shape(point)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, inGb])
  const previous = useMemo(() => {
    if (!before || !data) return null
    const shift = new Date(data.end).getTime() - new Date(before.end).getTime()
    return (before.points || []).map((point) => shape(point, shift))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [before, data, inGb])
  if (error) return <p className="m-0 border-l-2 border-warn bg-bg px-[18px] py-4 text-xs text-warn">{error}</p>
  if (!data) return <p className="m-0 border-l-2 border-paper bg-bg px-[18px] py-6 text-xs text-muted">Loading the history of {container.name}…</p>
  const start = new Date(data.start).getTime()
  const end = new Date(data.end).getTime()
  const utc = ['day', 'week', 'month'].includes(data.source)
  const rows = [
    { key: 'cpu', label: 'CPU', format: formatPercent, axis: (tick) => `${Math.round(tick)}%`, floor: 100 / 1.15, sub: 'Can pass 100% on more than one core' },
    { key: 'memory', label: 'Memory', format: (value) => megabytes(inGb ? value * 1024 : value), axis: (tick) => (inGb ? `${Number(tick.toFixed(1))} GB` : `${Math.round(tick)} MB`), sub: container.memory_limit_mb ? `limit ${megabytes(container.memory_limit_mb)}` : undefined },
    { key: 'restarts', label: 'Restart count', format: (value) => String(Math.round(value)), band: false, floor: 1, sub: 'Docker\'s running total' },
  ]
  // A container that was stopped has no CPU or memory figures: those periods are gaps too.
  const reporting = points.filter((point) => point.cpu?.avg != null || point.memory?.avg != null).map((point) => point.t)
  return (
    <div className="border-l-2 border-paper bg-bg">
      <Timeline compact title={container.name} note={`${container.image ? `${container.image} · ` : ''}one point per ${describeBucket(data.bucket_seconds)}`} rows={rows} points={points} previous={previous} start={start} end={end} bucketSeconds={data.bucket_seconds}
        gaps={findGaps(reporting, data.bucket_seconds, end)} utc={utc} legend={{ current: `Last ${range.words}`, previous: previous ? `The ${range.previous}` : null }} />
    </div>
  )
}

export function ContainersTable({ server, usage, range, compare, selectedId, onSelect }) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('avg_cpu')
  const [all, setAll] = useState(false)
  const current = useMemo(() => new Map((server.containers || []).map((container) => [container.container_id, container])), [server])
  const rows = useMemo(() => {
    // Swap is the average over the range; until a container has history with it, what it holds right now.
    const merged = (usage?.containers || []).map((item) => { const now = current.get(item.container_id) || null; return { ...item, now, swap: item.avg_swap_mb ?? now?.swap_mb ?? null } })
    // A container that exists now but has no history in this range yet still belongs in the list.
    for (const container of current.values()) if (!merged.some((item) => item.container_id === container.container_id)) merged.push({ container_id: container.container_id, name: container.name, avg_cpu: null, max_cpu: null, avg_memory_mb: null, max_memory_mb: null, restarts: null, sparkline: [], now: container, swap: container.swap_mb ?? null })
    const order = sort === 'name' ? (a, b) => a.name.localeCompare(b.name) : (a, b) => (b[sort] ?? -1) - (a[sort] ?? -1) || a.name.localeCompare(b.name)
    return merged.sort(order)
  }, [usage, current, sort])
  const needle = query.trim().toLowerCase()
  const matching = needle ? rows.filter((row) => `${row.name} ${row.now?.image || ''}`.toLowerCase().includes(needle)) : rows
  const visible = all || needle ? matching : matching.filter((row, index) => index < SHOWN || row.container_id === selectedId)
  const rank = new Map(rows.map((row, index) => [row.container_id, index + 1]))
  const top = Math.max(100, ...rows.flatMap((row) => row.sparkline || []).filter((value) => value != null))
  const swapKnown = rows.some((row) => row.swap != null)

  return (
    <section className="card overflow-hidden" aria-label="Containers">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 px-[18px] py-3">
        <h2 className="m-0 text-sm font-semibold text-paper">Containers</h2>
        <span className="mr-auto text-xs text-muted">Top consumers over the last {range.words} · {rows.length} on {server.hostname}</span>
        <label className="relative block w-full max-w-[220px]">
          <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-[9px] text-muted" />
          <input type="search" className="field pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search containers" aria-label={`Search containers on ${server.hostname}`} />
        </label>
        <select className="select" aria-label="Sort containers" value={sort} onChange={(event) => setSort(event.target.value)}>{SORTS.map(([key, label]) => <option key={key} value={key}>Sort: {label}</option>)}</select>
      </div>
      {!usage ? <p className="m-0 border-t border-line px-[18px] py-8 text-center text-xs text-muted">Loading…</p> : !rows.length ? <p className="m-0 border-t border-line px-[18px] py-8 text-center text-xs text-muted">No containers reported on {server.hostname} in this range.</p> : (
        <div className="custom-scroll overflow-x-auto"><div className="min-w-[1020px]">
          <div className={`grid ${COLUMNS} grid-head h-10 items-center border-t border-line px-[18px]`}>
            <span>#</span><span>Container</span><span>CPU · {range.label}</span><span className="text-right">Avg CPU</span><span className="text-right">Peak CPU</span><span className="text-right">Avg memory</span><span className="text-right">Peak memory</span><span className="text-right">Swap</span><span className="text-right">Restarts</span><span className="text-right">Uptime</span>
          </div>
          {visible.map((row) => {
            const open = row.container_id === selectedId
            const running = row.now?.status === 'running'
            return (
              <div key={row.container_id}>
                <button type="button" aria-expanded={open} onClick={() => onSelect(open ? '' : row.container_id)} className={`grid w-full ${COLUMNS} h-[42px] items-center border-0 border-t border-solid border-line px-[18px] text-left text-[13px] transition-colors hover:bg-raised ${open ? 'bg-raised' : 'bg-transparent'}`}>
                  <span className="mono text-dim">{String(rank.get(row.container_id)).padStart(2, '0')}</span>
                  <span className="flex min-w-0 items-center gap-2.5 pr-3"><span className={`dot ${!row.now ? '' : running ? (row.now.healthcheck_status === 'unhealthy' ? 'dot-signal' : 'dot-ok') : 'dot-warn'}`} style={{ width: 6, height: 6 }} title={row.now ? row.now.status : 'no longer on this server'} /><span className="truncate font-medium text-paper" title={row.name}>{row.name}</span><Caret open={open} className="text-dim" /></span>
                  <span className="pr-4"><Sparkline values={row.sparkline} width={150} height={18} max={top} /></span>
                  <span className="text-right text-paper">{formatPercent(row.avg_cpu)}</span>
                  <span className={`text-right ${row.max_cpu >= 100 ? 'text-signal' : 'text-paper'}`}>{formatPercent(row.max_cpu)}</span>
                  <span className="text-right text-paper">{megabytes(row.avg_memory_mb)}</span>
                  <span className="text-right text-paper">{megabytes(row.max_memory_mb)}</span>
                  <span className={`text-right ${row.swap >= 1 ? 'text-warn' : 'text-soft'}`} title={row.max_swap_mb != null ? `average over the range · peak ${megabytes(row.max_swap_mb)}` : undefined}>{row.swap == null ? '—' : row.swap < 1 ? '0' : megabytes(row.swap)}</span>
                  <span className={`text-right ${row.restarts ? 'text-warn' : 'text-paper'}`}>{row.restarts ?? '—'}</span>
                  <span className="text-right text-soft">{(running && containerUptime(row.now.started_at)) || '—'}</span>
                </button>
                {open && <div className="border-t border-line"><ContainerHistory serverId={server.id} container={{ ...row, image: row.now?.image, memory_limit_mb: row.now?.memory_limit_mb && row.now.memory_limit_mb < (server.total_ram_gb || 0) * 1024 * 0.98 ? row.now.memory_limit_mb : null }} range={range} compare={compare} /></div>}
              </div>
            )
          })}
          {!visible.length && <p className="m-0 border-t border-line px-[18px] py-6 text-center text-xs text-muted">No containers match “{query.trim()}”.</p>}
        </div></div>
      )}
      {rows.length > 0 && <p className="m-0 border-t border-line px-[18px] py-3 text-xs text-muted">
        Showing {visible.length} of {rows.length}{matching.length > visible.length || (all && rows.length > SHOWN) ? <> · <button type="button" className="link" onClick={() => setAll((value) => !value)}>{all ? 'show fewer' : 'show all'}</button></> : ''} · Select a row to open its own history with the same timeline.{swapKnown ? '' : ' Swap per container appears once this machine\'s collector is updated.'}
      </p>}
    </section>
  )
}
