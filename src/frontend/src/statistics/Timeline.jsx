// One shared timeline: metrics stacked on the same time axis with one crosshair, so a CPU spike, a load spike and
// what was running then all line up. Missing data is hatched instead of being a silent break in the line.
import { useEffect, useMemo, useRef, useState } from 'react'
import { formatTime, splitRuns } from '../chartMath.js'
import { niceCeiling } from '../dashboard/logic.js'
import { busiestAt, formatPeriod } from './logic.js'

export const GUTTER = 44
export const PAD_RIGHT = 22
export const HATCH = 'repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--color-paper) 9%, transparent) 5px 6px)'
const TONE = { paper: 'var(--color-paper)', warn: 'var(--color-warn)' }
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function useWidth(ref, fallback = 900) {
  const [width, setWidth] = useState(fallback)
  useEffect(() => {
    const element = ref.current
    if (!element) return undefined
    const update = () => setWidth(Math.max(Math.round(element.getBoundingClientRect().width), 200))
    update()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return width
}

// The top of a row's scale: a fixed 100 for percentages, otherwise a round number above the highest value.
export function scaleTop(row, series, previous) {
  if (row.max) return row.max
  const peak = Math.max(0, row.floor || 0, ...series.map((point) => point.max ?? point.avg ?? 0), ...(previous || []).map((point) => point.avg ?? 0))
  return niceCeiling(peak * 1.15 || 1)
}

// The drawing itself: band up to each period's maximum, the average as a line, the period before as a dashed
// line, and hatching where nothing was reported. Sits in a relatively positioned box; x is time, y is 0..top.
export function Plot({ series, previous, start, end, top, ticks, format, bucketSeconds, gaps = [], tone = 'paper', band = true, height, children }) {
  const span = Math.max(1, end - start)
  const x = (time) => ((time - start) / span) * 100
  const y = (value) => (1 - Math.min(Math.max(value, 0), top) / top) * 100
  const runs = useMemo(() => splitRuns(series.filter((point) => point.avg != null), bucketSeconds), [series, bucketSeconds])
  const previousRuns = useMemo(() => splitRuns((previous || []).filter((point) => point.avg != null), bucketSeconds), [previous, bucketSeconds])
  const line = (run) => run.map((point, index) => `${index ? 'L' : 'M'}${x(point.t).toFixed(2)},${y(point.avg).toFixed(2)}`).join(' ')
  const area = (run) => `${run.map((point, index) => `${index ? 'L' : 'M'}${x(point.t).toFixed(2)},${y(point.max ?? point.avg).toFixed(2)}`).join(' ')} ${[...run].reverse().map((point) => `L${x(point.t).toFixed(2)},${y(point.avg).toFixed(2)}`).join(' ')} Z`
  const color = TONE[tone] || TONE.paper
  return (
    <div className="relative" style={{ height, marginLeft: GUTTER, marginRight: PAD_RIGHT }}>
      {ticks.map((tick) => <span key={tick} aria-hidden="true" className="absolute right-full -translate-y-1/2 whitespace-nowrap pr-2.5 text-[11px] leading-none text-muted" style={{ top: `${y(tick)}%` }}>{format(tick)}</span>)}
      {gaps.map((gap) => <span key={gap.from} aria-hidden="true" className="absolute inset-y-0" style={{ left: `${Math.max(0, x(gap.from))}%`, width: `${Math.max(0.3, Math.min(100, x(gap.to)) - Math.max(0, x(gap.from)))}%`, background: HATCH }} />)}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden="true">
        {ticks.map((tick) => <line key={tick} x1="0" x2="100" y1={y(tick)} y2={y(tick)} stroke="var(--color-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
        {band && runs.map((run, index) => run.length > 1 && <path key={`band-${index}`} d={area(run)} fill="var(--color-paper)" fillOpacity="0.16" />)}
        {previousRuns.map((run, index) => run.length > 1 && <path key={`previous-${index}`} d={line(run)} fill="none" stroke="var(--color-muted)" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />)}
        {runs.map((run, index) => (run.length > 1
          ? <path key={`line-${index}`} d={line(run)} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          : <line key={`line-${index}`} x1={x(run[0].t) - 0.15} x2={x(run[0].t) + 0.15} y1={y(run[0].avg)} y2={y(run[0].avg)} stroke={color} strokeWidth="3" strokeLinecap="round" vectorEffect="non-scaling-stroke" />))}
      </svg>
      {children?.({ x, y })}
    </div>
  )
}

export function nearest(points, time) {
  if (!points.length) return -1
  let low = 0
  let high = points.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (points[middle].t < time) low = middle + 1
    else high = middle
  }
  return low > 0 && time - points[low - 1].t < points[low].t - time ? low - 1 : low
}

// Time labels under the timeline: midnights for ranges of days, otherwise evenly spaced clock times.
export function axisTicks(start, end, utc) {
  const span = end - start
  const ticks = []
  if (span > 36 * 3600000 && span <= 40 * 86400000 && !utc) {
    const first = new Date(start)
    first.setHours(0, 0, 0, 0)
    const days = Math.ceil(span / 86400000)
    const every = Math.max(1, Math.ceil(days / 8))
    for (let day = 0, moment = first.getTime(); moment <= end; day += 1, moment = new Date(first.getFullYear(), first.getMonth(), first.getDate() + day).getTime()) {
      if (moment >= start && day % every === 0) ticks.push({ t: moment, label: `${days <= 8 ? `${WEEKDAYS[new Date(moment).getDay()]} ` : ''}${new Date(moment).getDate()} ${MONTHS[new Date(moment).getMonth()]}` })
    }
    return ticks
  }
  for (let index = 0; index <= 6; index += 1) { const moment = start + (span * index) / 6; ticks.push({ t: moment, label: formatTime(moment, span / 1000, utc) }) }
  return ticks
}

const tableValue = (value, row) => (value == null ? '–' : row.format(value))

// ``rows``: [{ key, label, format, max?, floor?, tone?, sub?, band?, limit?: { value, label }, note?: { t, text } }]
// ``points``: [{ t, samples, [key]: { avg, max } | null }]; ``previous`` the same for the period before, already
// shifted onto this period's time axis. ``focus``: { t } scrolls into view and pins the crosshair there.
export function Timeline({ title = 'Timeline', note, rows, points, previous, start, end, bucketSeconds, gaps = [], marks, busiest, utc = false, legend, focus, compact = false, action }) {
  const wrap = useRef(null)
  const width = useWidth(wrap)
  const [hover, setHover] = useState(null)
  const [pinned, setPinned] = useState(null)
  const [asTable, setAsTable] = useState(false)
  const labelWidth = compact ? 132 : width < 700 ? 104 : 170
  const plotLeft = labelWidth + GUTTER
  const plotWidth = Math.max(40, width - plotLeft - PAD_RIGHT)
  const span = Math.max(1, end - start)
  const series = useMemo(() => Object.fromEntries(rows.map((row) => [row.key, points.map((point) => ({ t: point.t, avg: point[row.key]?.avg ?? null, max: point[row.key]?.max ?? null, samples: point.samples }))])), [rows, points])
  const before = useMemo(() => Object.fromEntries(rows.map((row) => [row.key, (previous || []).map((point) => ({ t: point.t, avg: point[row.key]?.avg ?? null, max: null }))])), [rows, previous])

  // A finding's "Show on timeline" pins the crosshair on its moment.
  useEffect(() => {
    if (!focus?.t || !points.length) return
    setPinned(nearest(points, focus.t))
    wrap.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [focus, points])

  const active = hover ?? pinned
  const index = active != null && active >= 0 && active < points.length ? active : null
  const point = index != null ? points[index] : null
  const crossX = point ? plotLeft + ((point.t - start) / span) * plotWidth : null
  const move = (event) => {
    const box = wrap.current.getBoundingClientRect()
    const fraction = (event.clientX - box.left - plotLeft) / plotWidth
    if (fraction < -0.02 || fraction > 1.02) { setHover(null); return }
    setHover(nearest(points, start + Math.min(1, Math.max(0, fraction)) * span))
  }
  const onKeyDown = (event) => {
    if (!points.length) return
    const from = index ?? points.length - 1
    if (event.key === 'ArrowLeft') { event.preventDefault(); setPinned(Math.max(0, from - 1)) } else if (event.key === 'ArrowRight') { event.preventDefault(); setPinned(Math.min(points.length - 1, from + 1)) } else if (event.key === 'Home') { event.preventDefault(); setPinned(0) } else if (event.key === 'End') { event.preventDefault(); setPinned(points.length - 1) } else if (event.key === 'Escape') setPinned(null)
  }
  const ticks = axisTicks(start, end, utc)
  const who = point ? busiestAt(busiest, point.t) : null
  const tooltipLeft = crossX == null ? 0 : crossX > width - 310 ? crossX - 286 : crossX + 14

  return (
    <section className={compact ? '' : 'card overflow-hidden'} aria-label={title}>
      <div className={`flex flex-wrap items-center gap-x-3 gap-y-2 ${compact ? 'px-[18px] pb-2 pt-3' : 'border-b border-line px-[18px] py-3'}`}>
        <h2 className={`m-0 font-semibold text-paper ${compact ? 'text-[13px]' : 'text-sm'}`}>{title}</h2>
        {note && <span className="min-w-0 text-xs text-muted">{note}</span>}
        <span className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-soft">
          {legend?.current && <span className="flex items-center gap-2"><span className="inline-block h-0.5 w-3.5 bg-paper" />{legend.current}</span>}
          {legend?.previous && <span className="flex items-center gap-2"><span className="inline-block w-3.5 border-t border-dashed border-muted" />{legend.previous}</span>}
          {gaps.length > 0 && <span className="flex items-center gap-2"><span className="inline-block h-3 w-3.5" style={{ background: HATCH }} />No data</span>}
          {action}
          <button type="button" className="btn btn-sm" aria-pressed={asTable} onClick={() => setAsTable((value) => !value)}>{asTable ? 'View as chart' : 'View as table'}</button>
        </span>
      </div>

      {asTable ? (
        <div className="custom-scroll max-h-[420px] overflow-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 bg-ink"><tr className="grid-head h-9"><th className="pl-[18px] font-medium">Period</th>{rows.map((row) => <th key={row.key} className="pr-4 text-right font-medium">{row.label}{row.band === false ? '' : ' avg / max'}</th>)}</tr></thead>
            <tbody>{[...points].reverse().map((item) => <tr key={item.t} className="h-8 border-t border-line text-soft"><td className="whitespace-nowrap pl-[18px] text-muted">{formatPeriod(item.t, bucketSeconds, utc)}</td>{rows.map((row) => <td key={row.key} className="pr-4 text-right">{tableValue(item[row.key]?.avg, row)}{row.band === false ? '' : ` / ${tableValue(item[row.key]?.max, row)}`}</td>)}</tr>)}</tbody>
          </table>
          {!points.length && <p className="m-0 px-[18px] py-8 text-center text-xs text-muted">Nothing was recorded in this range.</p>}
        </div>
      ) : (
        <div ref={wrap} className="relative select-none outline-none focus-visible:ring-1 focus-visible:ring-paper" tabIndex={0} role="group" aria-label={`${title}. Use the left and right arrow keys to read each period.`} onPointerMove={move} onPointerLeave={() => setHover(null)} onPointerDown={(event) => { if (event.pointerType !== 'mouse') move(event) }} onKeyDown={onKeyDown}>
          {marks && (
            <div className="grid border-b border-line" style={{ gridTemplateColumns: `${labelWidth}px minmax(0, 1fr)` }}>
              <div className="flex items-center border-r border-line px-[18px] text-xs text-soft">Events</div>
              <div className="relative h-10 overflow-hidden" style={{ marginLeft: GUTTER, marginRight: PAD_RIGHT }}>
                {marks.map((mark, position) => {
                  const left = ((mark.t - start) / span) * 100
                  const room = ((marks[position + 1]?.t ?? end) - mark.t) / span * plotWidth
                  const text = `${mark.label}${mark.count > 1 ? ` ×${mark.count}` : ''}`
                  return <span key={`${mark.t}-${mark.kind}`} className="absolute top-1/2 flex -translate-y-1/2 items-center gap-1.5 whitespace-nowrap text-[11px] text-muted" style={{ left: `${left}%` }} title={`${formatPeriod(mark.t, 60)} · ${text}${mark.detail ? ` · ${mark.detail}` : ''}`}>
                    <span className="h-[5px] w-[5px] shrink-0" style={{ background: mark.tone === 'signal' ? 'var(--color-signal)' : mark.tone === 'warn' ? 'var(--color-warn)' : 'var(--color-dim)' }} />
                    {room > text.length * 6.2 + 16 && <span className={mark.tone === 'plain' ? '' : 'text-soft'}>{text}</span>}
                  </span>
                })}
                {!marks.length && <span className="absolute left-0 top-1/2 -translate-y-1/2 text-[11px] text-dim">No events recorded in this range</span>}
              </div>
            </div>
          )}

          {rows.map((row) => {
            const own = series[row.key]
            const known = own.filter((item) => item.avg != null)
            const top = scaleTop(row, own, before[row.key])
            const value = point ? point[row.key]?.avg : known.length ? known[known.length - 1].avg : null
            const height = compact ? 84 : 104
            return (
              <div key={row.key} className="grid border-b border-line last:border-b-0" style={{ gridTemplateColumns: `${labelWidth}px minmax(0, 1fr)` }}>
                <div className={`min-w-0 border-r border-line px-[18px] ${compact ? 'py-3' : 'py-4'}`}>
                  <div className="text-[13px] font-medium text-paper">{row.label}</div>
                  <div className={`mt-2 font-semibold leading-none text-paper ${compact ? 'text-lg' : 'text-[21px]'}`}>{value == null ? '–' : row.format(value)}</div>
                  {row.sub && <div className="mt-2.5 text-[11px] leading-4 text-muted">{row.sub}</div>}
                </div>
                <div className="min-w-0 pb-2 pt-[18px]">
                  <Plot series={own} previous={before[row.key]} start={start} end={end} top={top} ticks={[top, top / 2, 0]} format={row.axis || row.format} bucketSeconds={bucketSeconds} gaps={gaps} tone={row.tone} band={row.band !== false} height={height}>
                    {({ x, y }) => <>
                      {row.limit && row.limit.value <= top && <span aria-hidden="true" className="absolute inset-x-0 border-t border-dashed border-signal/70" style={{ top: `${y(row.limit.value)}%` }}><span className="absolute -top-[17px] right-0 text-[11px] text-signal">{row.limit.label}</span></span>}
                      {row.note && row.note.t >= start && row.note.t <= end && <span aria-hidden="true" className="absolute top-0 bottom-0 border-l border-dashed border-warn/70" style={{ left: `${x(row.note.t)}%` }}><span className="absolute -top-[5px] left-[-3px] flex items-center gap-1.5 whitespace-nowrap text-[11px] text-warn"><span className="h-[6px] w-[6px] bg-warn" />{row.note.text}</span></span>}
                      {point && point[row.key]?.avg != null && <span aria-hidden="true" className="absolute h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px] bg-ink" style={{ left: `${x(point.t)}%`, top: `${y(point[row.key].avg)}%`, borderColor: TONE[row.tone] || TONE.paper }} />}
                    </>}
                  </Plot>
                </div>
              </div>
            )
          })}

          <div className="grid" style={{ gridTemplateColumns: `${labelWidth}px minmax(0, 1fr)` }}>
            <div className="border-r border-line" />
            <div className="relative h-7" style={{ marginLeft: GUTTER, marginRight: PAD_RIGHT }} aria-hidden="true">
              {ticks.map((tick, position) => <span key={tick.t} className={`absolute top-1.5 whitespace-nowrap text-[11px] text-muted ${position === ticks.length - 1 && (tick.t - start) / span > 0.93 ? '-translate-x-full' : position === 0 ? '' : '-translate-x-1/2'}`} style={{ left: `${((tick.t - start) / span) * 100}%` }}>{tick.label}</span>)}
            </div>
          </div>

          {point && <span aria-hidden="true" className="pointer-events-none absolute w-px bg-paper/70" style={{ left: crossX, top: marks ? 41 : 0, bottom: 28 }} />}
          {point && (
            <div role="status" className="pointer-events-none absolute z-10 w-[272px] rounded-md border border-edge bg-bg px-3.5 py-3 text-xs shadow-[0_8px_24px_rgba(0,0,0,0.5)]" style={{ left: Math.max(4, tooltipLeft), top: marks ? 58 : 14 }}>
              <div className="mb-2 text-[13px] font-semibold text-paper">{formatPeriod(point.t, bucketSeconds, utc)}</div>
              {rows.map((row) => <div key={row.key} className="flex justify-between gap-4 py-[3px]"><span className="text-muted">{row.short || row.label}{row.pair ? ' avg / max' : ''}</span><span className="text-paper">{point[row.key]?.avg == null ? 'no data' : `${row.format(point[row.key].avg)}${row.pair && point[row.key].max != null ? ` / ${row.format(point[row.key].max)}` : ''}`}</span></div>)}
              {who && <div className="mt-2 flex justify-between gap-4 border-t border-line pt-2"><span className="text-muted">Busiest</span><span className="mono truncate text-paper">{who.name} {Math.round(who.cpu)}%</span></div>}
            </div>
          )}
          {!points.length && <p className="pointer-events-none absolute inset-x-0 top-1/2 m-0 -translate-y-1/2 text-center text-xs text-muted" style={{ paddingLeft: labelWidth }}>Nothing was recorded in this range.</p>}
        </div>
      )}
    </section>
  )
}

// One small chart in its own card body: used for response times and the disk forecasts.
export function MiniChart({ series, start, end, bucketSeconds, gaps = [], top, format, height = 132, utc = false, labels, children, band = true }) {
  const scale = top ?? niceCeiling(Math.max(1, ...series.map((point) => point.max ?? point.avg ?? 0)) * 1.1)
  const span = Math.max(1, end - start)
  const edge = labels || [{ t: start, label: formatTime(start, span / 1000, utc) }, { t: end, label: formatTime(end, span / 1000, utc) }]
  return (
    <div className="px-[18px] pb-3 pt-6">
      <Plot series={series} start={start} end={end} top={scale} ticks={[scale, scale / 2, 0]} format={format} bucketSeconds={bucketSeconds} gaps={gaps} height={height} band={band}>{children}</Plot>
      <div aria-hidden="true" className="relative mt-2 h-4 text-[11px] text-muted" style={{ marginLeft: GUTTER, marginRight: PAD_RIGHT }}>
        {edge.map((tick, index) => <span key={tick.t} className={`absolute whitespace-nowrap ${index === edge.length - 1 ? '-translate-x-full' : index ? '-translate-x-1/2' : ''}`} style={{ left: `${((tick.t - start) / span) * 100}%` }}>{tick.label}</span>)}
      </div>
    </div>
  )
}
