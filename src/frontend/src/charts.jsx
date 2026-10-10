// Chart components drawn with plain SVG: no charting library, so there is nothing extra to trust or update, and
// every label from the server is rendered as text by React, never as HTML.
import { useEffect, useMemo, useRef, useState } from 'react'
import { formatDateTime, formatTime, niceTicks, splitRuns, summarise } from './chartMath.js'

const MARGIN = { top: 10, right: 12, bottom: 24, left: 40 }

// The width of an element in pixels, kept up to date, so SVG text stays crisp at any screen size.
function useElementWidth(ref, fallback = 640) {
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

const FIXED_DOMAIN_FALLBACK = [0, 1]

// An average line with a lighter band up to the maximum, over time. Hover or touch shows the exact figures; the
// whole chart is also available as a table.
export function TimeSeriesChart({ title, series, bucketSeconds, start, end, unit = '%', color = '#7dd3fc', domain = null, height = 200, utc = false, decimals = 1, limit = null }) {
  const wrapper = useRef(null)
  const width = useElementWidth(wrapper)
  const [hover, setHover] = useState(null)
  const [asTable, setAsTable] = useState(false)

  const layout = useMemo(() => {
    const tStart = start ?? (series[0]?.t ?? 0)
    const tEnd = Math.max(end ?? (series[series.length - 1]?.t ?? 1), tStart + 1)
    const values = series.flatMap((point) => [point.avg, point.max]).filter((value) => value !== null)
    if (limit !== null && !domain) values.push(limit / 1.1)   // keep the limit line inside the chart
    const [fixedLow, fixedHigh] = domain || FIXED_DOMAIN_FALLBACK
    const yTicks = domain
      ? niceTicks(fixedLow, fixedHigh, 4)
      : niceTicks(0, values.length ? Math.max(...values) * 1.1 : 1, 4)
    const yMax = yTicks[yTicks.length - 1] || fixedHigh
    const plotWidth = Math.max(width - MARGIN.left - MARGIN.right, 50)
    const plotHeight = height - MARGIN.top - MARGIN.bottom
    const x = (t) => MARGIN.left + ((t - tStart) / (tEnd - tStart)) * plotWidth
    const y = (value) => MARGIN.top + plotHeight - (Math.min(Math.max(value, 0), yMax) / yMax) * plotHeight
    const spanSeconds = (tEnd - tStart) / 1000
    const tickCount = Math.max(2, Math.min(7, Math.floor(plotWidth / 110)))
    const xTicks = Array.from({ length: tickCount }, (_, index) => tStart + ((tEnd - tStart) * index) / (tickCount - 1))
    return { tStart, tEnd, yTicks, yMax, plotWidth, plotHeight, x, y, spanSeconds, xTicks }
  }, [series, start, end, width, height, domain, limit])

  const runs = useMemo(() => splitRuns(series, bucketSeconds), [series, bucketSeconds])
  const format = (value) => (value === null || value === undefined ? '–' : `${value.toFixed(decimals)}${unit === '%' ? '%' : ` ${unit}`.trimEnd()}`)

  if (!series.length) {
    return (
      <figure className="min-w-0 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 p-4">
        <figcaption className="text-sm font-medium text-white">{title}</figcaption>
        <div className="mt-3 flex items-center justify-center rounded-xl border border-dashed border-neutral-800 text-xs text-[#7f8589]" style={{ height }}>No data for this range yet</div>
      </figure>
    )
  }

  const move = (event) => {
    const box = wrapper.current?.getBoundingClientRect()
    if (!box) return
    const px = event.clientX - box.left
    let best = null
    for (const point of series) {
      const distance = Math.abs(layout.x(point.t) - px)
      if (best === null || distance < best.distance) best = { point, distance }
    }
    setHover(best && best.distance < 60 ? best.point : null)
  }
  const label = `${title}: average ${format(summarise(series).average)}, highest ${format(summarise(series).maximum)}`

  return (
    <figure className="min-w-0 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 p-4">
      <div className="flex items-center justify-between gap-3">
        <figcaption className="text-sm font-medium text-white">{title}</figcaption>
        <button type="button" onClick={() => setAsTable((value) => !value)} className="rounded-lg border border-white/10 px-2 py-1 text-[11px] text-[#9aa0a6] hover:text-white">{asTable ? 'View as chart' : 'View as table'}</button>
      </div>
      {asTable ? (
        <div className="mt-3 max-h-64 overflow-auto rounded-xl border border-[#1f2326]">
          <table className="w-full text-left text-xs text-[#bcbcbc]">
            <thead className="sticky top-0 bg-[#111417] text-[#9aa0a6]"><tr><th className="px-3 py-2 font-medium">Time</th><th className="px-3 py-2 font-medium">Average</th><th className="px-3 py-2 font-medium">Highest</th><th className="px-3 py-2 font-medium">Check-ins</th></tr></thead>
            <tbody>{series.map((point) => <tr key={point.t} className="border-t border-[#1f2326]"><td className="px-3 py-1.5">{formatDateTime(point.t, utc)}</td><td className="px-3 py-1.5">{format(point.avg)}</td><td className="px-3 py-1.5">{format(point.max)}</td><td className="px-3 py-1.5">{point.samples}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div ref={wrapper} className="relative mt-2 w-full min-w-0 touch-pan-y select-none overflow-hidden" onPointerMove={move} onPointerLeave={() => setHover(null)} onPointerDown={move}>
          <svg width={width} height={height} role="img" aria-label={label} className="block">
            {layout.yTicks.map((tick) => (
              <g key={tick}>
                <line x1={MARGIN.left} x2={width - MARGIN.right} y1={layout.y(tick)} y2={layout.y(tick)} stroke="#ffffff" strokeOpacity="0.07" />
                <text x={MARGIN.left - 6} y={layout.y(tick) + 4} textAnchor="end" fontSize="10" fill="#7f8589">{tick}</text>
              </g>
            ))}
            {layout.xTicks.map((tick, index) => (
              <text key={tick} x={layout.x(tick)} y={height - 6} textAnchor={index === 0 ? 'start' : index === layout.xTicks.length - 1 ? 'end' : 'middle'} fontSize="10" fill="#7f8589">{formatTime(tick, layout.spanSeconds, utc)}</text>
            ))}
            {runs.map((run) => {
              const withMax = run.filter((point) => point.max !== null && point.avg !== null)
              const band = withMax.length > 1
                ? `${withMax.map((point) => `${layout.x(point.t)},${layout.y(point.max)}`).join(' ')} ${[...withMax].reverse().map((point) => `${layout.x(point.t)},${layout.y(point.avg)}`).join(' ')}`
                : null
              const line = run.filter((point) => point.avg !== null)
              return (
                <g key={run[0].t}>
                  {band && <polygon points={band} fill={color} fillOpacity="0.22" />}
                  {line.length > 1 && <polyline points={line.map((point) => `${layout.x(point.t)},${layout.y(point.avg)}`).join(' ')} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />}
                  {line.length === 1 && <circle cx={layout.x(line[0].t)} cy={layout.y(line[0].avg)} r="3" fill={color} />}
                </g>
              )
            })}
            {limit !== null && (
              <g pointerEvents="none">
                <line x1={MARGIN.left} x2={width - MARGIN.right} y1={layout.y(limit)} y2={layout.y(limit)} stroke="#f87171" strokeOpacity="0.8" strokeDasharray="5 4" />
                <text x={width - MARGIN.right - 2} y={layout.y(limit) < MARGIN.top + 14 ? layout.y(limit) + 12 : layout.y(limit) - 4} textAnchor="end" fontSize="10" fill="#f87171">limit {limit.toFixed(0)} {unit}</text>
              </g>
            )}
            {hover && (
              <g pointerEvents="none">
                <line x1={layout.x(hover.t)} x2={layout.x(hover.t)} y1={MARGIN.top} y2={height - MARGIN.bottom} stroke="#ffffff" strokeOpacity="0.35" />
                {hover.avg !== null && <circle cx={layout.x(hover.t)} cy={layout.y(hover.avg)} r="4" fill={color} stroke="#0d1013" strokeWidth="2" />}
              </g>
            )}
          </svg>
          {hover && (
            <div role="status" className="pointer-events-none absolute z-10 rounded-xl border border-white/10 bg-[#111417]/95 px-3 py-2 text-xs text-[#e6e7ea] shadow-lg" style={{ top: 0, left: Math.min(Math.max(layout.x(hover.t) - 70, 0), Math.max(width - 150, 0)) }}>
              <div className="text-[#9aa0a6]">{formatDateTime(hover.t, utc)}</div>
              <div>Average <span className="font-medium text-white">{format(hover.avg)}</span></div>
              <div>Highest <span className="font-medium text-white">{format(hover.max)}</span></div>
              <div className="text-[#7f8589]">{hover.samples} check-ins</div>
            </div>
          )}
        </div>
      )}
    </figure>
  )
}
