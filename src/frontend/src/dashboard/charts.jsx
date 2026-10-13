// The Overview page's charts. All monochrome: colour is kept for status, so a chart never looks alarming just
// because of its palette.
import { useId } from 'react'

const clock = (time) => (time ? new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '')

// A small trend line with a dot on the latest value. ``max`` fixes the scale so rows can be compared.
export function Sparkline({ values, max = 100, width = 76, height = 18, tone = 'paper', className = '' }) {
  const known = (values || []).filter((value) => value != null)
  const color = tone === 'signal' ? 'var(--color-signal)' : 'var(--color-soft)'
  if (known.length < 2) return <svg width={width} height={height} className={className} aria-hidden="true"><line x1="0" x2={width - 3} y1={height - 2} y2={height - 2} stroke="var(--color-edge)" strokeWidth="1" /></svg>
  const top = Math.max(max, ...known)
  const usable = width - 3
  const y = (value) => 1.5 + (1 - Math.max(0, value) / top) * (height - 3.5)
  const points = values.map((value, index) => (value == null ? null : [(index / (values.length - 1)) * usable, y(value)])).filter(Boolean)
  const last = points[points.length - 1]
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={className} aria-hidden="true">
      <polyline points={points.map((point) => point.map((part) => part.toFixed(1)).join(',')).join(' ')} fill="none" stroke={color} strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="1.6" fill={color} />
    </svg>
  )
}

// A line over time with a labelled axis: ``points`` are [{ time, value }], ``max`` the top of the scale and
// ``ticks`` the values to label. ``area`` shades under the line; ``second`` adds a dashed series on the same scale.
export function LineChart({ points, second, max, ticks, format = (value) => String(value), area = false, height = 150, label, gutter = 40 }) {
  const gradient = useId()
  const usable = (points || []).filter((point) => point.value != null)
  const times = (points || []).map((point) => new Date(point.time).getTime())
  const start = times[0]
  const span = Math.max(1, (times[times.length - 1] ?? 0) - (start ?? 0))
  const x = (time) => ((new Date(time).getTime() - start) / span) * 100
  const y = (value) => (1 - Math.min(Math.max(value, 0), max) / max) * 100
  const path = (series) => {
    let drawing = false
    return series.map((point) => {
      if (point.value == null) { drawing = false; return '' }
      const command = `${drawing ? 'L' : 'M'}${x(point.time).toFixed(2)},${y(point.value).toFixed(2)}`
      drawing = true
      return command
    }).join(' ')
  }
  const line = usable.length > 1 ? path(points) : ''
  const fill = area && usable.length > 1 ? `M${x(usable[0].time).toFixed(2)},100 ${usable.map((point) => `L${x(point.time).toFixed(2)},${y(point.value).toFixed(2)}`).join(' ')} L${x(usable[usable.length - 1].time).toFixed(2)},100 Z` : ''
  return (
    <figure className="m-0" aria-label={label} role="img">
      <div className="relative" style={{ height, marginLeft: gutter }}>
        {ticks.map((tick) => <span key={tick} aria-hidden="true" className="absolute right-full -translate-y-1/2 whitespace-nowrap pr-2.5 text-[11px] leading-none text-muted" style={{ top: `${y(tick)}%` }}>{format(tick)}</span>)}
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
          <defs><linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--color-paper)" stopOpacity="0.2" /><stop offset="100%" stopColor="var(--color-paper)" stopOpacity="0.02" /></linearGradient></defs>
          {ticks.map((tick) => <line key={tick} x1="0" x2="100" y1={y(tick)} y2={y(tick)} stroke="var(--color-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
          {fill && <path d={fill} fill={`url(#${gradient})`} />}
          {second && <path d={path(second)} fill="none" stroke="var(--color-muted)" strokeWidth="1.2" strokeDasharray="3 3" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
          {line && <path d={line} fill="none" stroke="var(--color-paper)" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
        </svg>
        {usable.length < 2 && <span className="absolute inset-0 flex items-center justify-center text-[11px] text-dim">Waiting for a few check-ins</span>}
      </div>
      <div aria-hidden="true" className="mt-2 flex justify-between text-[11px] leading-none text-muted" style={{ marginLeft: gutter }}><span>{clock(points?.[0]?.time)}</span><span>{clock(points?.[points.length - 1]?.time)}</span></div>
    </figure>
  )
}

// A value's share of its limit as a thin bar. ``level``: 0 plain, 1 warning, 2 critical.
export function Meter({ percent, level = 0, label, className = '' }) {
  const value = Math.min(100, Math.max(0, percent ?? 0))
  return <span className={`meter ${level === 2 ? 'meter-signal' : level === 1 ? 'meter-warn' : ''} ${className}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}><i style={{ width: `${value}%`, minWidth: percent > 0 ? 3 : 0 }} /></span>
}
