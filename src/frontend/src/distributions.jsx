// More chart types in plain SVG and HTML: stacked columns (also used for histograms), donut, ranked bars and a
// heatmap. Everything that comes from the server (names, labels) is rendered as text by React, never as HTML.
import { useEffect, useRef, useState } from 'react'
import { donutSlices } from './chartMath.js'

// The frame every chart sits in: a title, the "view as table" switch and the table itself.
function ChartFrame({ title, subtitle, table, children, empty }) {
  const [asTable, setAsTable] = useState(false)
  return (
    <figure className="min-w-0 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <figcaption className="text-sm font-medium text-white">{title}</figcaption>
          {subtitle && <div className="text-xs text-[#7f8589]">{subtitle}</div>}
        </div>
        {!empty && table && <button type="button" onClick={() => setAsTable((value) => !value)} className="shrink-0 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-[#9aa0a6] hover:text-white">{asTable ? 'View as chart' : 'View as table'}</button>}
      </div>
      {empty ? (
        <div className="mt-3 flex min-h-32 items-center justify-center rounded-xl border border-dashed border-neutral-800 px-3 text-center text-xs text-[#7f8589]">{empty}</div>
      ) : asTable && table ? (
        <div className="mt-3 max-h-64 overflow-auto rounded-xl border border-[#1f2326]">
          <table className="w-full text-left text-xs text-[#bcbcbc]">
            <thead className="sticky top-0 bg-[#111417] text-[#9aa0a6]"><tr>{table.head.map((cell) => <th key={cell} className="px-3 py-2 font-medium">{cell}</th>)}</tr></thead>
            <tbody>{table.rows.map((row, index) => <tr key={index} className="border-t border-[#1f2326]">{row.map((cell, cellIndex) => <td key={cellIndex} className="px-3 py-1.5">{cell}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : <div className="mt-3">{children}</div>}
    </figure>
  )
}

function Legend({ items }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#bcbcbc]">
      {items.map((item) => <li key={item.label} className="flex items-center gap-1.5"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} />{item.label}{item.value !== undefined && <span className="text-[#7f8589]">{item.value}</span>}</li>)}
    </ul>
  )
}

function useWidth(ref, fallback = 480) {
  const [width, setWidth] = useState(fallback)
  useEffect(() => {
    const element = ref.current
    if (!element) return undefined
    const update = () => setWidth(Math.max(Math.round(element.getBoundingClientRect().width), 160))
    update()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return width
}

// Columns over categories (days, bins, ...), optionally stacked. categories: [{label, values: {key: number}}].
export function ColumnChart({ title, subtitle, categories, series, height = 190, unit = '', empty = 'Nothing to show for this range yet', labelEvery = 1 }) {
  const wrapper = useRef(null)
  const width = useWidth(wrapper)
  const [hover, setHover] = useState(null)
  const margin = { top: 8, right: 8, bottom: 26, left: 34 }
  const totals = categories.map((category) => series.reduce((sum, item) => sum + (category.values[item.key] || 0), 0))
  const top = Math.max(1, ...totals)
  const niceTop = (() => { const magnitude = 10 ** Math.floor(Math.log10(top)); return [1, 2, 5, 10].map((f) => f * magnitude).find((value) => value >= top) })()
  const plotWidth = Math.max(width - margin.left - margin.right, 40)
  const plotHeight = height - margin.top - margin.bottom
  const slot = plotWidth / Math.max(categories.length, 1)
  const barWidth = Math.max(Math.min(slot * 0.72, 40), 1)
  const y = (value) => margin.top + plotHeight - (value / niceTop) * plotHeight
  const isEmpty = !categories.length || totals.every((total) => total === 0)
  const table = { head: ['', ...series.map((item) => item.label)], rows: categories.map((category) => [category.label, ...series.map((item) => category.values[item.key] || 0)]) }

  return (
    <ChartFrame title={title} subtitle={subtitle} table={table} empty={isEmpty ? empty : null}>
      <div ref={wrapper} className="relative w-full min-w-0 overflow-hidden" onPointerLeave={() => setHover(null)}>
        <svg width={width} height={height} role="img" aria-label={`${title}: ${categories.length} columns, highest ${top}${unit}`} className="block">
          {[0, niceTop / 2, niceTop].map((tick) => (
            <g key={tick}>
              <line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} stroke="#ffffff" strokeOpacity="0.07" />
              <text x={margin.left - 6} y={y(tick) + 4} textAnchor="end" fontSize="10" fill="#7f8589">{Number(tick.toFixed(2))}</text>
            </g>
          ))}
          {categories.map((category, index) => {
            const x = margin.left + slot * index + (slot - barWidth) / 2
            let base = 0
            return (
              <g key={category.label} onPointerEnter={() => setHover({ index, x: x + barWidth / 2 })} onPointerDown={() => setHover({ index, x: x + barWidth / 2 })}>
                <rect x={margin.left + slot * index} y={margin.top} width={slot} height={plotHeight} fill="transparent" />
                {series.map((item) => {
                  const value = category.values[item.key] || 0
                  if (!value) return null
                  const rect = <rect key={item.key} x={x} y={y(base + value)} width={barWidth} height={Math.max(y(base) - y(base + value), 1)} fill={item.color} rx="2" opacity={hover && hover.index !== index ? 0.55 : 1} />
                  base += value
                  return rect
                })}
                {index % labelEvery === 0 && <text x={x + barWidth / 2} y={height - 8} textAnchor="middle" fontSize="10" fill="#7f8589">{category.label}</text>}
              </g>
            )
          })}
        </svg>
        {hover && (
          <div role="status" className="pointer-events-none absolute z-10 rounded-xl border border-white/10 bg-[#111417]/95 px-3 py-2 text-xs text-[#e6e7ea] shadow-lg" style={{ top: 0, left: Math.min(Math.max(hover.x - 60, 0), Math.max(width - 140, 0)) }}>
            <div className="text-[#9aa0a6]">{categories[hover.index].label}</div>
            {series.map((item) => <div key={item.key}><span aria-hidden="true" className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: item.color }} />{item.label} <span className="font-medium text-white">{categories[hover.index].values[item.key] || 0}{unit}</span></div>)}
          </div>
        )}
      </div>
      {series.length > 1 && <Legend items={series} />}
    </ChartFrame>
  )
}

// A donut with the total in the middle and a legend that also says the numbers, so colour is never the only cue.
export function Donut({ title, subtitle, slices, centreLabel = 'total', empty = 'Nothing to show yet' }) {
  const parts = donutSlices(slices)
  const total = parts.reduce((sum, slice) => sum + slice.value, 0)
  const radius = 54
  const circumference = 2 * Math.PI * radius
  const table = { head: ['', 'Count', 'Share'], rows: parts.map((slice) => [slice.label, slice.value, `${(slice.fraction * 100).toFixed(1)}%`]) }
  return (
    <ChartFrame title={title} subtitle={subtitle} table={table} empty={total === 0 ? empty : null}>
      <div className="flex flex-wrap items-center gap-5">
        <svg width="140" height="140" viewBox="0 0 140 140" role="img" aria-label={`${title}: ${parts.map((slice) => `${slice.label} ${slice.value}`).join(', ')}`} className="shrink-0">
          <g transform="rotate(-90 70 70)">
            {parts.map((slice) => (
              <circle key={slice.label} cx="70" cy="70" r={radius} fill="none" stroke={slice.color} strokeWidth="18" strokeDasharray={`${Math.max(slice.fraction * circumference - (parts.length > 1 ? 1.5 : 0), 0.5)} ${circumference}`} strokeDashoffset={-slice.start * circumference} />
            ))}
          </g>
          <text x="70" y="68" textAnchor="middle" fontSize="24" fontWeight="600" fill="#ffffff">{total}</text>
          <text x="70" y="86" textAnchor="middle" fontSize="10" fill="#7f8589">{centreLabel}</text>
        </svg>
        <Legend items={parts.map((slice) => ({ label: slice.label, color: slice.color, value: `${slice.value}` }))} />
      </div>
    </ChartFrame>
  )
}

// Ranked horizontal bars (top containers, servers by disk usage ...). rows: [{label, value, detail?}].
export function BarList({ title, subtitle, rows, max = null, unit = '', color = '#7dd3fc', decimals = 0, empty = 'Nothing to show yet' }) {
  const scale = max ?? Math.max(1, ...rows.map((row) => row.value))
  const table = { head: ['', `Value${unit ? ` (${unit.trim()})` : ''}`], rows: rows.map((row) => [row.label, row.value.toFixed(decimals)]) }
  return (
    <ChartFrame title={title} subtitle={subtitle} table={table} empty={rows.length === 0 ? empty : null}>
      <ul className="space-y-2">
        {rows.map((row) => (
          <li key={row.label}>
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="min-w-0 truncate text-[#cfcfcf]">{row.label}</span>
              <span className="shrink-0 text-white">{row.value.toFixed(decimals)}{unit}{row.detail ? <span className="text-[#7f8589]"> {row.detail}</span> : null}</span>
            </div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full" style={{ width: `${Math.min(Math.max((row.value / scale) * 100, 0), 100)}%`, background: row.color || color }} /></div>
          </li>
        ))}
      </ul>
    </ChartFrame>
  )
}

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Average by hour of day (columns) and weekday (rows). grid: 7 rows of 24 numbers or null.
export function Heatmap({ title, subtitle, grid, unit = '%', color = '#7dd3fc', empty = 'Not enough history yet' }) {
  const [hover, setHover] = useState(null)
  const values = grid.flat().filter((value) => value !== null)
  const top = Math.max(1, ...values)
  const table = { head: ['', ...Array.from({ length: 24 }, (_, hour) => `${hour}`)], rows: grid.map((row, day) => [DAY_NAMES[day], ...row.map((value) => (value === null ? '–' : value.toFixed(0)))]) }
  return (
    <ChartFrame title={title} subtitle={subtitle} table={table} empty={values.length === 0 ? empty : null}>
      <div className="relative">
        <div className="overflow-x-auto">
          <div className="min-w-[420px]">
            {grid.map((row, day) => (
              <div key={DAY_NAMES[day]} className="flex items-center gap-1 py-px">
                <span className="w-8 shrink-0 text-[10px] text-[#7f8589]">{DAY_NAMES[day]}</span>
                {row.map((value, hour) => (
                  <button
                    key={hour}
                    type="button"
                    aria-label={`${DAY_NAMES[day]} ${hour}:00, ${value === null ? 'no data' : `${value.toFixed(0)}${unit}`}`}
                    onPointerEnter={() => setHover({ day, hour, value })}
                    onFocus={() => setHover({ day, hour, value })}
                    onPointerLeave={() => setHover(null)}
                    className="h-5 flex-1 rounded-[3px]"
                    style={{ background: value === null ? 'rgba(255,255,255,0.04)' : color, opacity: value === null ? 1 : 0.15 + 0.85 * (value / top) }}
                  />
                ))}
              </div>
            ))}
            <div className="mt-1 flex gap-1 pl-9 text-[10px] text-[#7f8589]">{[0, 6, 12, 18, 23].map((hour) => <span key={hour} style={{ position: 'relative', left: `${(hour / 24) * 100 - (hour === 23 ? 3 : 0)}%`, width: 0 }}>{hour}h</span>)}</div>
          </div>
        </div>
        <div className="mt-2 text-xs text-[#9aa0a6]" role="status">{hover ? `${DAY_NAMES[hover.day]} ${hover.hour}:00 · ${hover.value === null ? 'no data' : `${hover.value.toFixed(1)}${unit} on average`}` : `Darker is busier, up to ${top.toFixed(0)}${unit}.`}</div>
      </div>
    </ChartFrame>
  )
}
