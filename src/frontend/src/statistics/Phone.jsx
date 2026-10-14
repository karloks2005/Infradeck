// Statistics on a phone: the same data reordered for one thumb. Numbers before charts, the findings next, and the
// timeline on its own screen where touch-and-hold scrubs all four charts at once with the readout above the finger.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Sparkline } from '../dashboard/charts.jsx'
import { PhoneHeader } from '../shell.jsx'
import { GUTTER, HATCH, PAD_RIGHT, Plot, axisTicks, nearest, scaleTop } from './Timeline.jsx'
import { busiestAt, formatPeriod, percentile } from './logic.js'

const TONE = { paper: 'var(--color-paper)', warn: 'var(--color-warn)' }
const CARD_PAD = 12

export function Pills({ label, items, value, onChange }) {
  const row = useRef(null)
  useEffect(() => { row.current?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }, [value])
  return (
    <div ref={row} className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4" role="group" aria-label={label}>
      {items.map((item) => (
        <button key={item.id} type="button" aria-pressed={value === item.id} onClick={() => onChange(item.id)} className={`flex h-10 shrink-0 items-center gap-2 rounded-full border px-4 text-[13px] font-medium ${value === item.id ? 'border-paper bg-paper text-bg' : 'border-edge bg-transparent text-soft'}`}>
          {item.dot !== undefined && <span className={`dot ${item.dot}`} style={{ width: 7, height: 7 }} />}{item.label}
        </button>
      ))}
    </div>
  )
}

// The six headline numbers as a 2 × 3 grid of small cards.
export function PhoneKpis({ items }) {
  return (
    <section className="grid grid-cols-2 gap-2" aria-label="Headline figures">
      {items.map((item) => (
        <div key={item.label} className="min-w-0 rounded-md border border-line bg-ink px-3.5 pb-3 pt-3">
          <div className="truncate text-xs text-muted">{item.label}</div>
          <div className={`mt-1.5 text-[22px] font-semibold leading-7 ${item.tone === 'warn' ? 'text-warn' : 'text-paper'}`}>{item.value}</div>
          <div className={`mt-1 min-h-4 text-xs leading-4 ${item.subTone === 'warn' ? 'text-warn' : 'text-muted'}`}>{item.sub}</div>
        </div>
      ))}
    </section>
  )
}

export function PhoneFindings({ title, findings, empty, onShow }) {
  return (
    <section aria-label={title}>
      <div className="mb-2.5 flex items-baseline gap-2"><h2 className="m-0 text-[15px] font-semibold text-paper">{title}</h2><span className="text-xs text-muted">{findings.length}</span></div>
      {findings.length ? (
        <ol className="m-0 list-none divide-y divide-line overflow-hidden rounded-md border border-line bg-ink p-0">
          {findings.map((finding) => (
            <li key={finding.key} className="px-4 py-3.5">
              <div className="text-sm font-semibold text-paper">{finding.title}</div>
              <p className="mb-0 mt-1 text-[13px] leading-5 text-soft">{finding.text}</p>
              {finding.at != null && <button type="button" onClick={() => onShow(finding.at)} className="mt-2 inline-flex h-8 items-center border-0 bg-transparent p-0 text-[13px] font-medium text-paper">Show on timeline →</button>}
            </li>
          ))}
        </ol>
      ) : <p className="m-0 rounded-md border border-line bg-ink px-4 py-3.5 text-[13px] text-soft">{empty}</p>}
    </section>
  )
}

// On the main screen the timeline is a preview that opens its own screen.
export function TimelineEntry({ series, start, end, bucketSeconds, gaps, words, onOpen }) {
  return (
    <section className="rounded-md border border-line bg-ink" aria-label="Timeline">
      <div className="flex items-baseline gap-2 px-4 pt-3.5"><h2 className="m-0 text-[15px] font-semibold text-paper">Timeline</h2><span className="text-xs text-muted">CPU, memory, swap and load · last {words}</span></div>
      <div className="px-1 pt-4">{series.length ? <Plot series={series} start={start} end={end} top={100} ticks={[100, 0]} format={(tick) => `${tick}%`} bucketSeconds={bucketSeconds} gaps={gaps} height={64} /> : <p className="m-0 py-6 text-center text-xs text-muted">Nothing was recorded in this range.</p>}</div>
      <div className="p-3"><button type="button" onClick={onOpen} className="btn h-11 w-full text-sm">Open timeline</button></div>
    </section>
  )
}

// The timeline screen. ``rows`` as for the desktop Timeline, plus ``phone``: the note on the right of each chart.
export function PhoneTimeline({ rows, points, previous, start, end, bucketSeconds, gaps, marks, busiest, utc, focusT, legend, back, rangeControl }) {
  const [index, setIndex] = useState(() => (focusT && points.length ? nearest(points, focusT) : null))
  const [asTable, setAsTable] = useState(false)
  const box = useRef(null)
  const hold = useRef({ timer: null, active: false, start: null })
  const span = Math.max(1, end - start)
  const series = useMemo(() => Object.fromEntries(rows.map((row) => [row.key, points.map((point) => ({ t: point.t, avg: point[row.key]?.avg ?? null, max: point[row.key]?.max ?? null, samples: point.samples }))])), [rows, points])
  const before = useMemo(() => Object.fromEntries(rows.map((row) => [row.key, (previous || []).map((point) => ({ t: point.t, avg: point[row.key]?.avg ?? null, max: null }))])), [rows, previous])

  useEffect(() => { if (focusT && points.length) setIndex(nearest(points, focusT)) }, [focusT, points])

  const at = (event) => {
    const rect = box.current.getBoundingClientRect()
    const fraction = (event.clientX - rect.left - CARD_PAD - GUTTER) / (rect.width - 2 * CARD_PAD - GUTTER - PAD_RIGHT)
    return nearest(points, start + Math.min(1, Math.max(0, fraction)) * span)
  }
  const handlers = {
    onPointerDown: (event) => {
      if (!points.length) return
      if (event.pointerType === 'mouse') { setIndex(at(event)); return }
      const next = at(event)
      hold.current.start = [event.clientX, event.clientY]
      clearTimeout(hold.current.timer)
      hold.current.timer = setTimeout(() => { hold.current.active = true; setIndex(next) }, 200)
    },
    onPointerMove: (event) => {
      if (!points.length) return
      if (event.pointerType === 'mouse' || hold.current.active) setIndex(at(event))
      else if (hold.current.start && Math.hypot(event.clientX - hold.current.start[0], event.clientY - hold.current.start[1]) > 8) clearTimeout(hold.current.timer)
    },
    onPointerUp: () => { clearTimeout(hold.current.timer); hold.current.active = false },
    onPointerCancel: () => { clearTimeout(hold.current.timer); hold.current.active = false },
    onContextMenu: (event) => event.preventDefault(),
  }

  const point = index != null ? points[index] : points[points.length - 1] || null
  const who = point ? busiestAt(busiest, point.t) : null
  const ticks = axisTicks(start, end, utc)
  const edge = ticks.length > 2 ? [ticks[0], ticks[Math.floor(ticks.length / 2)], ticks[ticks.length - 1]] : ticks

  return (
    <>
      <PhoneHeader back={back} title="Timeline" action={rangeControl} />
      <div className="space-y-3 px-4 pt-3">
        <div role="status" className="sticky top-2 z-10 rounded-md border border-edge bg-bg px-3.5 py-3 shadow-[0_8px_24px_rgba(0,0,0,0.45)]">
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="min-w-0 truncate font-semibold text-paper">{point ? `${index == null ? 'Latest · ' : ''}${formatPeriod(point.t, bucketSeconds, utc)}` : 'No data'}</span>
            {who && <span className="shrink-0 text-xs text-soft">busiest: <span className="text-paper">{who.name}</span></span>}
          </div>
          <div className="mt-2 grid grid-cols-4 gap-2">
            {rows.map((row) => (
              <div key={row.key} className="min-w-0">
                <div className="truncate text-[11px] text-muted">{row.short || row.label}</div>
                <div className={`mt-0.5 text-[17px] font-semibold ${row.tone === 'warn' && (point?.[row.key]?.avg ?? 0) > 0 ? 'text-warn' : 'text-paper'}`}>{point?.[row.key]?.avg == null ? '–' : row.format(point[row.key].avg)}</div>
                {row.pair && point?.[row.key]?.max != null && <div className="text-[11px] text-muted">max {row.format(point[row.key].max)}</div>}
              </div>
            ))}
          </div>
        </div>

        {asTable ? (
          <div className="custom-scroll max-h-[60vh] overflow-auto rounded-md border border-line bg-ink">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 bg-ink"><tr className="grid-head h-9"><th className="pl-3 font-medium">Period</th>{rows.map((row) => <th key={row.key} className="pr-3 text-right font-medium">{row.short || row.label}</th>)}</tr></thead>
              <tbody>{[...points].reverse().map((item) => <tr key={item.t} className="h-9 border-t border-line text-soft"><td className="whitespace-nowrap pl-3 text-muted">{formatPeriod(item.t, bucketSeconds, utc).replace(/^[A-Z][a-z]{2} /, '')}</td>{rows.map((row) => <td key={row.key} className="pr-3 text-right">{item[row.key]?.avg == null ? '–' : row.format(item[row.key].avg)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        ) : (
          <div ref={box} className="select-none overflow-hidden rounded-md border border-line bg-ink" style={{ touchAction: 'pan-y', WebkitTouchCallout: 'none' }} {...handlers} aria-label="Timeline. Touch and hold to read every metric at one moment.">
            {marks && <div className="border-b border-line pb-2.5 pt-3" style={{ paddingLeft: CARD_PAD, paddingRight: CARD_PAD }}>
              <div className="flex items-baseline justify-between text-[13px] font-medium text-paper"><span>Events</span><span className="text-xs font-normal text-muted">{marks.length}</span></div>
              <div className="relative mt-2 h-3" style={{ marginLeft: GUTTER, marginRight: PAD_RIGHT }}>
                {marks.map((mark) => <span key={`${mark.t}-${mark.kind}`} title={mark.label} className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2" style={{ left: `${((mark.t - start) / span) * 100}%`, background: mark.tone === 'signal' ? 'var(--color-signal)' : mark.tone === 'warn' ? 'var(--color-warn)' : 'var(--color-dim)' }} />)}
                {point && <span aria-hidden="true" className="absolute -bottom-2.5 -top-1 w-px bg-paper/70" style={{ left: `${((point.t - start) / span) * 100}%` }} />}
              </div>
            </div>}
            {rows.map((row, position) => {
              const own = series[row.key]
              const top = scaleTop(row, own, before[row.key])
              return (
                <div key={row.key} className={`pb-2 pt-3 ${position ? 'border-t border-line' : ''}`} style={{ paddingLeft: CARD_PAD, paddingRight: CARD_PAD }}>
                  <div className="mb-3 flex items-baseline justify-between text-[13px]"><span className="font-medium text-paper">{row.label}</span><span className="text-xs text-muted">{row.phone}</span></div>
                  <Plot series={own} previous={before[row.key]} start={start} end={end} top={top} ticks={[top, 0]} format={row.axis || row.format} bucketSeconds={bucketSeconds} gaps={gaps} tone={row.tone} band={row.band !== false} height={78}>
                    {({ x, y }) => <>
                      {row.limit && row.limit.value <= top && <span aria-hidden="true" className="absolute inset-x-0 border-t border-dashed border-signal/70" style={{ top: `${y(row.limit.value)}%` }} />}
                      {point && <span aria-hidden="true" className="absolute -bottom-2 -top-3 w-px bg-paper/70" style={{ left: `${x(point.t)}%` }} />}
                      {point && point[row.key]?.avg != null && <span aria-hidden="true" className="absolute h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px] bg-ink" style={{ left: `${x(point.t)}%`, top: `${y(point[row.key].avg)}%`, borderColor: TONE[row.tone] || TONE.paper }} />}
                    </>}
                  </Plot>
                </div>
              )
            })}
            <div className="relative mb-3 h-4 text-[11px] text-muted" style={{ marginLeft: CARD_PAD + GUTTER, marginRight: CARD_PAD + PAD_RIGHT }} aria-hidden="true">
              {edge.map((tick, position) => <span key={tick.t} className={`absolute whitespace-nowrap ${position === edge.length - 1 ? '-translate-x-full' : position ? '-translate-x-1/2' : ''}`} style={{ left: `${((tick.t - start) / span) * 100}%` }}>{tick.label}</span>)}
            </div>
            {!points.length && <p className="m-0 pb-4 text-center text-xs text-muted">Nothing was recorded in this range.</p>}
          </div>
        )}

        <div className="flex flex-wrap justify-center gap-x-5 gap-y-1 text-xs text-soft">
          <span className="flex items-center gap-2"><span className="inline-block h-0.5 w-3.5 bg-paper" />{legend.current}</span>
          {legend.previous && <span className="flex items-center gap-2"><span className="inline-block w-3.5 border-t border-dashed border-muted" />{legend.previous}</span>}
          {gaps.length > 0 && <span className="flex items-center gap-2"><span className="inline-block h-3 w-3.5" style={{ background: HATCH }} />No data</span>}
        </div>
        <button type="button" className="btn h-12 w-full text-sm" aria-pressed={asTable} onClick={() => setAsTable((value) => !value)}>{asTable ? 'View as chart' : 'View as table'}</button>
      </div>
    </>
  )
}

const milliseconds = (value) => (value == null ? '–' : value >= 10000 ? `${Number((value / 1000).toFixed(1))} s` : `${Math.round(value)} ms`)

// Response times as two rows with a small line each, instead of two full charts.
export function PhoneReliability({ docker, collect, interval }) {
  if (!docker.length && !collect.length) return null
  const row = (title, detail, series) => (
    <div className="flex items-center gap-4 px-4 py-3">
      <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-paper">{title}</span><span className="mt-0.5 block text-xs text-muted">{detail}</span></span>
      <Sparkline values={series.map((point) => point.avg)} width={96} height={20} max={Math.max(1, ...series.map((point) => point.avg ?? 0))} />
    </div>
  )
  return (
    <section aria-label="Reliability">
      <h2 className="m-0 mb-2.5 text-[15px] font-semibold text-paper">Reliability</h2>
      <div className="divide-y divide-line overflow-hidden rounded-md border border-line bg-ink">
        {docker.length > 0 && row('Docker API response', `median ${milliseconds(percentile(docker.map((point) => point.avg), 50))} · p95 ${milliseconds(percentile(docker.map((point) => point.max ?? point.avg), 95))}`, docker)}
        {collect.length > 0 && row('Collector per check-in', `median ${milliseconds(percentile(collect.map((point) => point.avg), 50))} · budget ${interval >= 60 ? `${Math.round(interval / 60)} min` : `${interval} s`}`, collect)}
      </div>
    </section>
  )
}
