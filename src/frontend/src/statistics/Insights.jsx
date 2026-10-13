// The panels under the timeline: when the server is busy, how its time splits across load levels, how its disks
// are filling up (with the forecast drawn, not just written) and how fast Docker and the collector answer.
import { useMemo, useState } from 'react'
import { seriesFor } from '../chartMath.js'
import { forecastKey, usePolled } from '../dashboard/data.js'
import { forecastMonth, forecastPhrase, formatPercent, niceCeiling } from '../dashboard/logic.js'
import { MiniChart } from './Timeline.jsx'
import { busiestHour, findGaps, percentile, projection, timeAtLevels } from './logic.js'

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (value) => String(value).padStart(2, '0')
const LIVE = new Set(['1h', '6h', '24h'])

function MetricSwitch({ value, onChange, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      <button type="button" aria-pressed={value === 'cpu'} onClick={() => onChange('cpu')}>CPU</button>
      <button type="button" aria-pressed={value === 'ram'} onClick={() => onChange('ram')}>Memory</button>
    </div>
  )
}

// ``table``: [asTable, setAsTable] adds the small "View as table" button every panel has.
function CardHead({ title, detail, children, table }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-[18px] py-3">
      <h2 className="m-0 text-sm font-semibold text-paper">{title}</h2>
      {detail && <span className="min-w-0 text-xs text-muted">{detail}</span>}
      <span className="ml-auto flex flex-wrap items-center justify-end gap-3 text-xs text-soft">
        {children}
        {table && <button type="button" className="btn btn-sm" aria-pressed={table[0]} onClick={() => table[1]((value) => !value)}>{table[0] ? 'View as chart' : 'View as table'}</button>}
      </span>
    </div>
  )
}

function DataTable({ head, rows }) {
  return (
    <div className="custom-scroll max-h-[300px] overflow-auto">
      <table className="w-full border-collapse text-left text-xs">
        <thead className="sticky top-0 bg-ink"><tr className="grid-head h-9">{head.map((cell, index) => <th key={`${cell}-${index}`} className={`font-medium ${index ? 'px-2 text-right' : 'pl-[18px]'} ${index === head.length - 1 ? 'pr-[18px]' : ''}`}>{cell}</th>)}</tr></thead>
        <tbody>{rows.map((row, position) => <tr key={position} className="h-8 border-t border-line text-soft">{row.map((cell, index) => <td key={index} className={`whitespace-nowrap ${index ? 'px-2 text-right' : 'pl-[18px] text-muted'} ${index === row.length - 1 ? 'pr-[18px]' : ''}`}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

const stamp = (time, utc) => { const date = new Date(time); return utc ? `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}` : `${date.getDate()} ${MONTHS[date.getMonth()]} ${pad(date.getHours())}:${pad(date.getMinutes())}` }

export function BusyHours({ serverId }) {
  const [metric, setMetric] = useState('cpu')
  const [hover, setHover] = useState(null)
  const table = useState(false)
  const offset = -new Date().getTimezoneOffset()
  const { data, error } = usePolled(`/stats/servers/${serverId}/heatmap?metric=${metric}&offset=${offset}`, null)
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'your time zone'
  const grid = data?.cells
  const values = grid ? grid.flat().filter((value) => value != null) : []
  // The scale stops where most hours are, so one extreme hour doesn't turn everything else black.
  const cap = values.length ? Math.max(5, niceCeiling(percentile(values, 90) * 1.4)) : 100
  const peak = grid ? busiestHour(grid) : null
  const name = metric === 'cpu' ? 'CPU' : 'memory'
  return (
    <section className="card overflow-hidden" aria-label="When it's busy">
      <CardHead title="When it's busy" detail={`Average ${name} by hour, last 30 days, ${zone}`} table={grid && values.length ? table : null}><MetricSwitch value={metric} onChange={setMetric} label="Metric for the busy hours" /></CardHead>
      {table[0] && grid && values.length > 0 ? <DataTable head={['', ...Array.from({ length: 24 }, (_, hour) => pad(hour))]} rows={grid.map((row, day) => [DAY_NAMES[day], ...row.map((value) => (value == null ? '–' : Math.round(value)))])} /> : <div className="px-[18px] pb-4 pt-4">
        {error && <p className="m-0 text-xs text-warn">{error}</p>}
        {!error && !grid && <p className="m-0 py-10 text-center text-xs text-muted">Loading…</p>}
        {grid && !values.length && <p className="m-0 py-10 text-center text-xs text-muted">Not enough history yet. This fills in as hourly summaries are built.</p>}
        {grid && values.length > 0 && <>
          <div className="custom-scroll overflow-x-auto"><div className="min-w-[520px]">
            {grid.map((row, day) => (
              <div key={DAY_NAMES[day]} className="flex items-center gap-[3px] py-[1.5px]">
                <span className="w-9 shrink-0 text-[11px] text-muted">{DAY_NAMES[day]}</span>
                {row.map((value, hour) => (
                  <button key={hour} type="button" aria-label={`${DAY_NAMES[day]} ${pad(hour)}:00, ${value == null ? 'no data' : formatPercent(value)}`}
                    onPointerEnter={() => setHover({ day, hour, value })} onFocus={() => setHover({ day, hour, value })} onPointerLeave={() => setHover(null)} onBlur={() => setHover(null)}
                    className="h-[22px] flex-1 cursor-default rounded-[3px] border-0 p-0"
                    style={{ background: value == null ? 'var(--color-sunken)' : `color-mix(in srgb, var(--color-paper) ${Math.round(14 + 86 * Math.min(1, value / cap))}%, var(--color-ink))` }} />
                ))}
              </div>
            ))}
            <div className="flex gap-[3px] pt-1.5" aria-hidden="true"><span className="w-9 shrink-0" />{Array.from({ length: 24 }, (_, hour) => <span key={hour} className="flex-1 text-center text-[11px] text-muted">{hour % 3 === 0 ? pad(hour) : ''}</span>)}</div>
          </div></div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-muted">
            <span className="flex items-center gap-2.5" aria-hidden="true">0%<span className="h-2 w-[140px] rounded-sm" style={{ background: 'linear-gradient(90deg, color-mix(in srgb, var(--color-paper) 14%, var(--color-ink)), var(--color-paper))' }} />{cap}%+</span>
            <span role="status">{hover ? `${DAY_NAMES[hover.day]} ${pad(hover.hour)}:00 · ${hover.value == null ? 'no data' : `${formatPercent(hover.value)} on average`}` : `Brighter is busier.${peak ? ` The ${pad(peak.hour)}:00 column is the busiest hour, ${formatPercent(peak.value)} on average.` : ''}`}</span>
          </div>
        </>}
      </div>}
    </section>
  )
}

export function TimeAtLevels({ cpu, ram, words }) {
  const [metric, setMetric] = useState('cpu')
  const table = useState(false)
  const { bands, sentence } = useMemo(() => timeAtLevels(metric === 'cpu' ? cpu : ram, metric === 'cpu' ? 'CPU' : 'memory'), [metric, cpu, ram])
  const widest = Math.max(1, ...bands.map((band) => band.share))
  return (
    <section className="card overflow-hidden" aria-label="Time spent at each level">
      <CardHead title="Time spent at each level" table={sentence ? table : null}><MetricSwitch value={metric} onChange={setMetric} label="Metric for the levels" /></CardHead>
      {table[0] && sentence ? <DataTable head={['Level', 'Share of the time']} rows={bands.map((band) => [band.label, formatPercent(band.share)])} /> : <div className="px-[18px] pb-4 pt-4">
        {sentence ? <>
          <ul className="m-0 list-none space-y-[11px] p-0">
            {bands.map((band) => (
              <li key={band.label} className="grid grid-cols-[62px_minmax(0,1fr)_48px] items-center gap-3 text-xs">
                <span className="text-soft">{band.label}</span>
                <span className="block h-[9px]"><span className="block h-full rounded-[2px]" style={{ width: `${(band.share / widest) * 100}%`, minWidth: band.share > 0 ? 2 : 0, background: band.hot ? 'var(--color-signal)' : 'var(--color-soft)' }} /></span>
                <span className="text-right text-paper">{formatPercent(band.share)}</span>
              </li>
            ))}
          </ul>
          <p className="mb-0 mt-5 text-xs leading-[19px] text-muted">{sentence.replace('of the time', `of the ${words}`)}</p>
        </> : <p className="m-0 py-10 text-center text-xs text-muted">Not enough data in this range.</p>}
      </div>}
    </section>
  )
}

function monthLabel(time, approximate = false) {
  const date = new Date(time)
  const part = date.getDate() <= 10 ? 'early-' : date.getDate() <= 20 ? 'mid-' : 'late-'
  return `${approximate ? part : ''}${approximate ? ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][date.getMonth()] : MONTHS[date.getMonth()]} ${date.getFullYear()}`
}

function DiskCard({ server, mount, range, days, horizon }) {
  const { data } = usePolled(`/monitoring/servers/${server.id}/mounts/history?mount=${encodeURIComponent(mount.mount)}&range=${range.id}&metrics=used_pct`, LIVE.has(range.id) ? 60 : null)
  const series = useMemo(() => seriesFor(data?.points, 'used_pct'), [data])
  const table = useState(false)
  const start = data ? new Date(data.start).getTime() : null
  const now = data ? new Date(data.end).getTime() : null
  const last = series.length ? series[series.length - 1] : null
  const ahead = last && now ? projection(now, last.avg, days, Math.max(1, (horizon - now) / 86400000)) : null
  const end = ahead ? Math.max(horizon, now + 1) : now
  const phrase = forecastPhrase(days)
  const when = forecastMonth(days)
  return (
    <section className="card overflow-hidden" aria-label={`Disk ${mount.mount}`}>
      <CardHead table={series.length ? table : null} title={<>Disk · <span className="mono">{mount.mount}</span></>} detail={mount.total_gb != null ? `${Number(mount.used_gb?.toFixed(1))} of ${Number(mount.total_gb.toFixed(1))} GB` : formatPercent(mount.used_pct)}>
        {when && days < 730 ? <span>Full around <span className="font-semibold text-paper">{when.replace('full around ', '')}</span></span> : phrase ? <span>{phrase.replace(/about .*/, 'about')} <span className="font-semibold text-paper">{phrase.replace(/^Full in about /, '')}</span></span> : <span className="text-muted">No clear growth trend</span>}
      </CardHead>
      {table[0] && series.length ? <DataTable head={['Period', 'Used']} rows={[...series].reverse().map((point) => [stamp(point.t, data.bucket_seconds >= 86400), formatPercent(point.avg)])} /> : data ? (
        <MiniChart series={series} start={start} end={end} bucketSeconds={data.bucket_seconds} gaps={findGaps(series.map((point) => point.t), data.bucket_seconds, now)} top={100} format={(tick) => `${tick}%`} band={false}
          labels={ahead ? [{ t: start, label: monthDay(start) }, { t: now, label: monthDay(now) }, { t: end, label: monthLabel(end) }] : [{ t: start, label: monthDay(start) }, { t: now, label: monthDay(now) }]}>
          {({ x, y }) => ahead && <>
            <span aria-hidden="true" className="absolute inset-y-0 border-l border-line bg-paper/[0.03]" style={{ left: `${x(now)}%`, right: 0 }} />
            <span aria-hidden="true" className="absolute top-1 -translate-x-full whitespace-nowrap pr-2 text-[11px] text-muted" style={{ left: `${x(now)}%` }}>last {range.words}</span>
            <span aria-hidden="true" className="absolute top-1 whitespace-nowrap pl-2 text-[11px] text-muted" style={{ left: `${x(now)}%` }}>projection</span>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden="true">
              <line x1={x(ahead.from.t)} y1={y(ahead.from.value)} x2={x(ahead.to.t)} y2={y(ahead.to.value)} stroke="var(--color-soft)" strokeWidth="1.2" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
            </svg>
            <span aria-hidden="true" className="absolute h-[7px] w-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full" style={{ left: `${x(ahead.to.t)}%`, top: `${y(ahead.to.value)}%`, background: ahead.full ? 'var(--color-signal)' : 'var(--color-muted)' }} />
            <span className={`absolute -translate-x-full whitespace-nowrap pr-3 text-[11px] ${ahead.full ? 'text-signal' : 'text-soft'}`} style={{ left: `${x(ahead.to.t)}%`, top: ahead.full ? `calc(${y(ahead.to.value)}% + 26px)` : `calc(${y(ahead.to.value)}% - 22px)` }}>
              {ahead.full ? `100% around ${monthLabel(ahead.to.t, true)}` : `${formatPercent(ahead.to.value)} by ${['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][new Date(ahead.to.t).getMonth()]} · ${forecastPhrase(days, { short: true })}`}
            </span>
          </>}
        </MiniChart>
      ) : <p className="m-0 px-[18px] py-12 text-center text-xs text-muted">Loading…</p>}
    </section>
  )
}

const monthDay = (time) => { const date = new Date(time); return `${date.getDate()} ${MONTHS[date.getMonth()]}` }

// Each filesystem with its forecast continued as a dashed line. All of them share one horizon (the day the
// first one fills up), so the charts can be compared.
export function Disks({ server, mounts, range, forecasts }) {
  const now = Date.now()
  const soonest = Math.min(...mounts.map((mount) => forecasts[forecastKey(server.id, mount.mount)]).filter((days) => days != null && days > 0 && days < 3650))
  const horizon = now + (Number.isFinite(soonest) ? soonest : 180) * 86400000
  if (!mounts.length) return null
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      {mounts.map((mount) => <DiskCard key={mount.mount} server={server} mount={mount} range={range} days={forecasts[forecastKey(server.id, mount.mount)]} horizon={horizon} />)}
    </div>
  )
}

const milliseconds = (value) => (value >= 10000 ? `${Number((value / 1000).toFixed(1))} s` : `${Math.round(value)} ms`)

function ResponseCard({ title, detail, series, start, end, bucketSeconds, gaps, utc, summary, since }) {
  const first = series.length ? series[0].t : null
  const table = useState(false)
  // A stretch at the start with no figures at all means the collector then didn't measure this yet.
  const unmeasured = first != null && first - start > (end - start) * 0.05 && !gaps.some((gap) => gap.to <= first)
  return (
    <section className="card overflow-hidden" aria-label={title}>
      <CardHead title={title} detail={detail} table={series.length ? table : null}>{summary}</CardHead>
      {table[0] && series.length ? <DataTable head={['Period', 'Average', 'Highest']} rows={[...series].reverse().map((point) => [stamp(point.t, utc), point.avg == null ? '–' : milliseconds(point.avg), point.max == null ? '–' : milliseconds(point.max)])} /> : series.length ? (
        <MiniChart series={series} start={start} end={end} bucketSeconds={bucketSeconds} gaps={gaps.filter((gap) => first == null || gap.from >= first)} format={(tick) => String(Math.round(tick))} utc={utc}>
          {({ x }) => unmeasured && <span className="absolute inset-y-0 left-0 z-[1] flex items-center pl-2" style={{ width: `${x(first)}%`, background: 'repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--color-paper) 9%, transparent) 5px 6px)' }}><span className="whitespace-nowrap rounded bg-ink px-2 py-0.5 text-[11px] text-soft">Not measured before {since || monthDay(first)}</span></span>}
        </MiniChart>
      ) : <p className="m-0 px-[18px] py-12 text-center text-xs text-muted">Not measured in this range.</p>}
    </section>
  )
}

export function Reliability({ docker, collect, start, end, bucketSeconds, gaps, utc, interval }) {
  if (!docker.length && !collect.length) return null
  const figure = (label, value) => <span>{label} <span className="font-semibold text-paper">{value == null ? '–' : milliseconds(value)}</span></span>
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <ResponseCard title="Docker API response time" detail="listing containers · ms" series={docker} start={start} end={end} bucketSeconds={bucketSeconds} gaps={gaps} utc={utc}
        summary={<>{figure('median', percentile(docker.map((point) => point.avg), 50))}<span className="text-dim">·</span>{figure('p95', percentile(docker.map((point) => point.max ?? point.avg), 95))}</>} />
      <ResponseCard title="Collector time per check-in" detail="ms" series={collect} start={start} end={end} bucketSeconds={bucketSeconds} gaps={gaps} utc={utc}
        summary={<>{figure('median', percentile(collect.map((point) => point.avg), 50))}<span className="text-dim">·</span><span>budget {interval >= 60 ? `${Math.round(interval / 60)} min` : `${interval} s`}</span></>} />
    </div>
  )
}
