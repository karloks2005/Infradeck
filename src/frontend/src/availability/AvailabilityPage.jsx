// Availability: checks that your services answer, run by the collectors on your own servers (Milestone 11).
// A summary strip, the checks that are down pulled out of the rack, one row per check grouped by tag with its
// last 30 results, and the selected check's details below the list. Admins add checks in the New check drawer.
import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { Icon } from '../icons.jsx'
import { AppShell, PhoneHeader } from '../shell.jsx'
import { Drawer, Notice, Soon, ago, useAction, usePhone } from '../ui.jsx'
import { SectionHeading } from '../dashboard/NeedsAttention.jsx'
import { CheckForm } from './CheckForm.jsx'

const REFRESH_SECONDS = 15
const TYPE_LABEL = { http: 'HTTP(s)', keyword: 'Keyword', json: 'JSON query', tcp: 'TCP port', tls: 'TLS cert', dns: 'DNS', ping: 'Ping' }
const SLOT_CLASS = { up: 'bg-ok/80', slow: 'bg-[#8a7a3e]', retrying: 'bg-warn', down: 'bg-signal', paused: 'bg-edge', empty: 'bg-line' }
const LEGEND = [['up', 'up'], ['slow', 'slow'], ['retrying', 'retrying'], ['down', 'down'], ['paused', 'paused']]
const FILTERS = [['all', 'All'], ['down', 'Down'], ['slow', 'Slow'], ['paused', 'Paused']]

const percent = (value) => (value == null ? '—' : `${value >= 99.995 ? 100 : value.toFixed(value >= 99 ? 2 : 1)}%`)
const every = (seconds) => (seconds % 86400 === 0 ? `${seconds / 86400} d` : seconds % 3600 === 0 ? `${seconds / 3600} h` : seconds % 60 === 0 && seconds >= 300 ? `${seconds / 60} min` : `${seconds} s`)
const isSlow = (check) => !check.paused && check.state !== 'down' && check.strip.slice(-3).some((slot) => slot.status === 'slow')
const JSON_OP_LABEL = { equals: 'equals', contains: 'contains', gt: '>', lt: '<', exists: 'exists' }
const shortTarget = (check) => {
  const options = check.options || {}
  if (check.type === 'keyword') return `${check.target} · expects “${check.keyword}”`
  if (check.type === 'json') return `${check.target} · ${options.json_path} ${JSON_OP_LABEL[options.json_op] || ''} ${options.json_op === 'exists' ? '' : options.json_expect || ''}`.trim()
  if (check.type === 'dns') return `${options.record_type || 'A'} ${check.target}${options.resolver ? ` @${options.resolver}` : ''}`
  return check.target
}

function useAvailability() {
  const [state, setState] = useState({ data: null, error: '', loadedAt: 0 })
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let active = true
    const load = () => apiRequest('/availability', { method: 'GET' })
      .then((data) => { if (active) setState({ data, error: '', loadedAt: Date.now() }) })
      .catch((error) => { if (active) setState((current) => ({ ...current, error: error.message || 'Could not load the checks.' })) })
    load()
    const timer = setInterval(load, REFRESH_SECONDS * 1000)
    return () => { active = false; clearInterval(timer) }
  }, [version])
  return { ...state, reload: () => setVersion((value) => value + 1) }
}

// The last 30 results, oldest on the left; missing slots (a new check) are drawn empty in front.
function Strip({ check, size = 'md' }) {
  const slots = check.strip.length >= 30 ? check.strip.slice(-30) : [...Array(30 - check.strip.length).fill(null), ...check.strip]
  const height = size === 'sm' ? 'h-4' : 'h-5'
  return (
    <span className="flex items-center gap-[2px]" role="img" aria-label={`Last ${check.strip.length} checks: ${check.strip.filter((slot) => slot.status === 'down').length} down`}>
      {slots.map((slot, index) => <span key={index} title={slot ? `${new Date(slot.start).toLocaleString()} · ${slot.status}${slot.response_ms != null ? ` · ${slot.response_ms} ms` : ''}` : 'no result yet'}
        className={`${height} w-[4px] shrink-0 rounded-[1px] ${SLOT_CLASS[check.paused ? 'paused' : slot ? slot.status : 'empty']}`} />)}
    </span>
  )
}

function Sparkline({ check }) {
  const values = check.strip.map((slot) => slot.response_ms).filter((value) => value != null)
  if (values.length < 2) return <span className="inline-block w-14" />
  const max = Math.max(...values), min = Math.min(...values)
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 54},${14 - ((value - min) / (max - min || 1)) * 10}`).join(' ')
  return <svg width="56" height="16" aria-hidden="true" className={check.state === 'down' ? 'text-signal' : 'text-soft'}><polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.2" /></svg>
}

function Summary({ data }) {
  const { counts, uptime_30d: uptime, expiring } = data
  const soonest = [...expiring].sort((a, b) => a.days - b.days)[0]
  const cells = [
    { label: 'Up', value: <>{counts.up}<span className="ml-1 text-xs font-normal text-muted">of {counts.total} checks</span></> },
    { label: 'Down', value: <span className={counts.down ? 'text-signal' : ''}>{counts.down}</span> },
    { label: 'Paused', value: counts.paused },
    { label: 'Uptime, all checks · 30 d', value: percent(uptime) },
    { label: 'Certificates expiring in 21 d', value: <><span className={expiring.length ? 'text-warn' : ''}>{expiring.length}</span>{soonest && <span className="ml-1.5 text-xs font-normal text-muted">{soonest.name}, {soonest.days} days</span>}</> },
  ]
  return (
    <div className="card grid grid-cols-2 divide-line overflow-hidden md:grid-cols-5 md:divide-x">
      {cells.map(({ label, value }, index) => (
        <div key={label} className={`min-w-0 px-[18px] py-3.5 ${index === 4 ? 'col-span-2 md:col-span-1' : ''} max-md:border-b max-md:border-line`}>
          <div className="truncate text-xs text-muted">{label}</div>
          <div className="mt-1.5 truncate text-[21px] font-semibold leading-7 text-paper">{value}</div>
        </div>
      ))}
    </div>
  )
}

function DownNow({ checks, canEdit, onOpen, onPause }) {
  if (!checks.length) return null
  return (
    <section aria-label="Down now">
      <SectionHeading title="Down now" detail={`${checks.length} ${checks.length === 1 ? 'check' : 'checks'}`} />
      <div className="rack">
        {checks.map((check) => {
          const critical = check.severity === 'CRITICAL'
          const failing = check.servers.map((server) => server.hostname).join(' and ')
          return (
            <div key={check.id} className={`drawer ${critical ? 'drawer-critical drawer-out' : 'drawer-alert'} grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-2 md:grid-cols-[110px_minmax(0,1fr)_auto]`}>
              <span className={`col-span-2 text-[11px] font-bold tracking-[0.08em] md:col-span-1 ${critical ? 'text-bg' : 'text-warn'}`}>DOWN {ago(check.state_since).replace(' ago', '').toUpperCase()}</span>
              <span className="min-w-0">
                <span className={`block truncate text-[13px] font-semibold ${critical ? 'text-bg' : 'text-paper'}`}>{check.name} · {check.target.replace(/^https?:\/\//, '')}</span>
                <span className={`block truncate text-xs ${critical ? 'text-bg/80' : 'text-muted'}`}>{check.last_detail || 'No answer'} from {failing} · failed {check.retries + 1} {check.retries ? 'checks in a row' : 'check'}{check.quiet_until ? ' · new check, no alert yet' : ''}</span>
              </span>
              <span className="col-span-2 mt-2 flex gap-2 md:col-span-1 md:mt-0">
                <button type="button" onClick={() => onOpen(check)} className={`btn btn-sm ${critical ? 'border-[#160800] bg-[#160800] text-paper hover:!border-black hover:!bg-black' : ''}`}>Open check</button>
                {canEdit && <button type="button" onClick={() => onPause(check, true)} className={`btn btn-sm ${critical ? 'border-[#160800] bg-[#160800] text-paper hover:!border-black hover:!bg-black' : ''}`}>Pause</button>}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function CheckRow({ check, selected, onSelect }) {
  const down = check.state === 'down'
  const lowUptime = check.uptime_24h != null && check.uptime_24h < 99
  return (
    <tr onClick={() => onSelect(check)} className={`cursor-pointer border-t border-line hover:bg-raised/60 ${selected ? 'bg-raised/70' : ''}`}>
      <td className={`max-w-[260px] py-2.5 pl-[18px] pr-3 ${down ? 'shadow-[inset_3px_0_0_var(--color-signal)]' : selected ? 'shadow-[inset_3px_0_0_var(--color-paper)]' : ''}`}>
        <button type="button" onClick={(event) => { event.stopPropagation(); onSelect(check) }} className="block max-w-full truncate text-left text-[13px] font-medium text-paper">{check.name}</button>
        <span className="mono block truncate text-[11px] text-muted" title={check.target}>{check.target}</span>
      </td>
      <td className="px-3"><span className="chip">{TYPE_LABEL[check.type]}</span></td>
      <td className="px-3"><Strip check={check} /></td>
      <td className={`px-3 text-right text-[13px] tabular-nums ${check.paused ? 'text-muted' : lowUptime || down ? 'text-signal' : 'text-paper'}`}>{check.paused ? 'paused' : percent(check.uptime_24h)}</td>
      <td className="px-3 text-right text-[13px] tabular-nums text-paper">{check.paused ? '—' : percent(check.uptime_30d)}</td>
      <td className="px-3">
        <span className="flex items-center gap-2.5">{!check.paused && <Sparkline check={check} />}
          <span className={`text-[13px] tabular-nums ${down ? 'text-signal' : check.state === 'stale' ? 'text-warn' : isSlow(check) ? 'text-warn' : 'text-paper'}`}>{check.paused ? '—' : down ? (check.last_detail?.replace(/^HTTP /, '') || 'down') : check.state === 'stale' ? 'no results' : check.response_ms != null ? `${check.response_ms} ms` : '—'}</span>
        </span>
      </td>
      <td className={`px-3 text-[13px] ${check.cert_days != null && check.cert_days <= 21 ? 'text-warn' : 'text-paper'}`}>{check.cert_days != null ? `${check.cert_days} days` : check.target.startsWith('http://') ? <span className="text-muted">no TLS</span> : <span className="text-muted">—</span>}</td>
      <td className="px-3"><span className="flex flex-wrap gap-1">{check.servers.map((server) => <span key={server.id} className="chip mono">{server.hostname}</span>)}</span></td>
      <td className="pl-3 pr-[18px] text-right text-[13px] tabular-nums text-paper">{every(check.interval_seconds)}</td>
    </tr>
  )
}

function PhoneCheck({ check, onSelect }) {
  const down = check.state === 'down'
  return (
    <button type="button" onClick={() => onSelect(check)} className={`block w-full border-t border-line px-4 py-3 text-left ${down ? 'shadow-[inset_3px_0_0_var(--color-signal)]' : ''}`}>
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate text-[15px] font-medium text-paper">{check.name}</span>
        <span className={`shrink-0 text-[13px] tabular-nums ${check.paused ? 'text-muted' : down ? 'text-signal' : check.state === 'stale' ? 'text-warn' : 'text-paper'}`}>{check.paused ? 'paused' : down ? (check.last_detail || 'down') : check.state === 'stale' ? 'no results' : percent(check.uptime_24h)}</span>
      </span>
      <span className="mono mt-0.5 block truncate text-[11px] text-muted">{TYPE_LABEL[check.type]} · {check.target}</span>
      <span className="mt-2 flex items-center justify-between gap-3"><Strip check={check} size="sm" /><span className="shrink-0 text-xs tabular-nums text-muted">{check.response_ms != null && !check.paused ? `${check.response_ms} ms` : ''}</span></span>
    </button>
  )
}

function ChecksTable({ checks, selectedId, onSelect, phone }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const counts = { all: checks.length, down: checks.filter((check) => check.state === 'down').length, slow: checks.filter(isSlow).length, paused: checks.filter((check) => check.paused).length }
  const shown = checks.filter((check) => (filter === 'all' || (filter === 'down' && check.state === 'down') || (filter === 'slow' && isSlow(check)) || (filter === 'paused' && check.paused))
    && (!query || `${check.name} ${check.target}`.toLowerCase().includes(query.toLowerCase())))
  const groups = useMemo(() => {
    const byTag = new Map()
    for (const check of shown) {
      const tag = check.tags[0] || ''
      if (!byTag.has(tag)) byTag.set(tag, [])
      byTag.get(tag).push(check)
    }
    return [...byTag.entries()].sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)))
  }, [shown])

  return (
    <section className="card overflow-hidden" aria-labelledby="checks-title">
      <div className="flex flex-wrap items-center gap-3 px-[18px] py-3.5">
        <h2 id="checks-title" className="m-0 text-sm font-semibold text-paper">Checks</h2>
        <span className="text-xs text-muted">{checks.length} · grouped by tag</span>
        <span className="flex w-full flex-wrap items-center gap-2 md:ml-auto md:w-auto">
          <label className="relative max-md:w-full"><span className="sr-only">Filter by name or address</span>
            <Icon name="search" size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter by name or address" className="field h-8 pl-8 text-[13px] md:w-56" />
          </label>
          <span className="seg" role="group" aria-label="Show">
            {FILTERS.map(([id, label]) => <button key={id} type="button" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label} {counts[id]}</button>)}
          </span>
        </span>
      </div>
      {phone ? <div>
        {groups.map(([tag, rows]) => (
          <Fragment key={tag || '-'}>
            <div className="border-t border-line px-4 py-2 text-[13px] font-semibold text-paper">{tag || 'No tag'} <span className="ml-1.5 text-xs font-normal text-muted">{rows.length}</span></div>
            {rows.map((check) => <PhoneCheck key={check.id} check={check} onSelect={onSelect} />)}
          </Fragment>
        ))}
        {!shown.length && <p className="m-0 border-t border-line px-4 py-6 text-center text-[13px] text-muted">No check matches.</p>}
      </div> : <div className="custom-scroll overflow-x-auto">
        <table className="w-full min-w-[1080px] border-collapse text-left">
          <thead><tr className="grid-head">
            <th className="py-2 pl-[18px] pr-3 font-medium">Check</th><th className="px-3 font-medium">Type</th><th className="px-3 font-medium">Last 30 checks</th>
            <th className="px-3 text-right font-medium">24 h</th><th className="px-3 text-right font-medium">30 d</th><th className="px-3 font-medium">Response</th>
            <th className="px-3 font-medium">Cert</th><th className="px-3 font-medium">Checked from</th><th className="pl-3 pr-[18px] text-right font-medium">Every</th>
          </tr></thead>
          <tbody>
            {groups.map(([tag, rows]) => (
              <Fragment key={tag || '-'}>
                <tr className="border-t border-line"><td colSpan={9} className="px-[18px] py-2 text-[13px] font-semibold text-paper">{tag || 'No tag'} <span className="ml-1.5 text-xs font-normal text-muted">{rows.length} {rows.length === 1 ? 'check' : 'checks'}</span></td></tr>
                {rows.map((check) => <CheckRow key={check.id} check={check} selected={check.id === selectedId} onSelect={onSelect} />)}
              </Fragment>
            ))}
            {!shown.length && <tr className="border-t border-line"><td colSpan={9} className="px-[18px] py-6 text-center text-[13px] text-muted">No check matches.</td></tr>}
          </tbody>
        </table>
      </div>}
      <div className="flex flex-wrap gap-4 border-t border-line px-[18px] py-2.5 text-[11px] text-muted">
        {LEGEND.map(([status, label]) => <span key={status} className="flex items-center gap-1.5"><span className={`h-2 w-2 rounded-[1px] ${SLOT_CLASS[status]}`} />{label}</span>)}
      </div>
    </section>
  )
}

const RANGE_LABELS = [['1h', '1 h'], ['24h', '24 h'], ['7d', '7 d'], ['30d', '30 d']]

// Response time per server: the first server a solid line, the others dashed.
function ResponseChart({ series, range, compact }) {
  const points = series.flatMap((line) => line.points)
  if (!points.length) return <p className="m-0 flex h-[150px] items-center justify-center text-xs text-muted">No answers in this range yet.</p>
  const times = points.map((point) => new Date(point.time).getTime())
  const spanMs = { '1h': 3600e3, '24h': 86400e3, '7d': 7 * 86400e3, '30d': 30 * 86400e3 }[range]
  // A check younger than the range starts where its data starts, rather than squeezed against the right edge.
  const end = Math.max(...times), start = Math.max(end - spanMs, Math.min(...times))
  const top = Math.max(...points.map((point) => point.ms), 1)
  const scale = top <= 10 ? 10 : 10 ** Math.floor(Math.log10(top)) * Math.ceil(top / 10 ** Math.floor(Math.log10(top)))
  // On a phone the drawing is narrower, so the labels keep a readable size.
  const width = compact ? 340 : 600, height = compact ? 120 : 140, left = 34
  const x = (time) => left + ((new Date(time).getTime() - start) / Math.max(1, end - start)) * (width - left)
  const y = (ms) => 6 + (1 - ms / scale) * (height - 12)
  const label = (time) => new Date(time).toLocaleString([], range === '1h' || range === '24h' ? { hour: '2-digit', minute: '2-digit' } : { day: 'numeric', month: 'short' })
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height + 16}`} className="h-auto w-full" role="img" aria-label="Response time per server">
        {[0, 0.5, 1].map((share) => <g key={share}>
          <line x1={left} x2={width} y1={y(scale * share)} y2={y(scale * share)} stroke="var(--color-line)" />
          <text x={left - 6} y={y(scale * share) + 3} textAnchor="end" fontSize="9" fill="var(--color-muted)">{Math.round(scale * share)}</text>
        </g>)}
        {series.map((line, index) => <polyline key={line.hostname} points={line.points.map((point) => `${x(point.time)},${y(point.ms)}`).join(' ')} fill="none"
          stroke={index === 0 ? 'var(--color-paper)' : 'var(--color-muted)'} strokeWidth="1.3" strokeDasharray={index === 0 ? undefined : '3 3'} strokeLinejoin="round" />)}
        <text x={left} y={height + 13} fontSize="9" fill="var(--color-muted)">{label(start)}</text>
        <text x={width} y={height + 13} fontSize="9" textAnchor="end" fill="var(--color-muted)">now</text>
      </svg>
      <div className="mt-1 flex flex-wrap gap-4 text-[11px] text-muted">
        {series.map((line, index) => <span key={line.hostname} className="flex items-center gap-1.5"><span className={`inline-block w-4 border-t ${index === 0 ? 'border-paper' : 'border-dashed border-muted'}`} />from {line.hostname}</span>)}
      </div>
    </div>
  )
}

function Days({ days }) {
  const known = days.filter((day) => day.uptime != null)
  const tone = (uptime) => (uptime == null ? 'bg-line' : uptime >= 99.9 ? 'bg-ok/80' : uptime >= 99 ? 'bg-warn' : 'bg-signal')
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="m-0 text-[13px] font-semibold text-paper">Last 90 days <span className="ml-1.5 text-xs font-normal text-muted">one bar per day</span></h3>
        <span className="text-xs tabular-nums text-muted">{known.length ? percent(known.reduce((sum, day) => sum + day.uptime, 0) / known.length) : '—'}</span>
      </div>
      <div className="mt-2.5 flex h-6 items-stretch gap-[2px]">
        {days.map((day) => <span key={day.day} title={`${new Date(day.day).toLocaleDateString([], { day: 'numeric', month: 'short' })} · ${day.uptime == null ? 'no data' : percent(day.uptime)}`} className={`min-w-0 flex-1 rounded-[1px] ${tone(day.uptime)}`} />)}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted"><span>{new Date(days[0].day).toLocaleDateString([], { day: 'numeric', month: 'short' })}</span><span>today</span></div>
    </div>
  )
}

function useCheckDetail(checkId, range, refreshedAt) {
  const [detail, setDetail] = useState(null)
  useEffect(() => {
    let active = true
    apiRequest(`/availability/checks/${checkId}?range=${range}`, { method: 'GET' }).then((data) => { if (active) setDetail(data) }).catch(() => {})
    return () => { active = false }
  }, [checkId, range, refreshedAt])
  return detail?.range === range ? detail : detail && { ...detail, series: null }
}

function Detail({ check, canEdit, onEdit, onPause, onDelete, refreshedAt }) {
  const phone = usePhone()
  const [range, setRange] = useState('24h')
  const detail = useCheckDetail(check.id, range, refreshedAt)
  const options = check.options || {}
  const settings = [
    ['Checked from', `${check.servers.map((server) => server.hostname).join(', ')}${check.servers.length > 1 ? ` · down when ${check.rule === 'all' ? 'all fail' : 'any fails'}` : ''}`],
    ['Every', `${every(check.interval_seconds)} · ${check.retries} ${check.retries === 1 ? 'retry' : 'retries'}${check.retries ? `, ${every(check.retry_interval_seconds)} apart` : ''}`],
    ['Timeout', `${check.timeout_seconds} s`],
    ...(['http', 'keyword', 'json'].includes(check.type) ? [['Accepts', (options.accepted_codes || []).join(', ')], ['Redirects', options.follow_redirects ? 'followed, up to 10' : 'not followed'], ['TLS', options.ignore_tls ? 'errors ignored' : 'must be valid']] : []),
    ...(check.type === 'keyword' ? [['Looks for', `“${check.keyword}”${options.case_sensitive ? ' (case-sensitive)' : ''}`]] : []),
    ...(check.type === 'json' ? [['JSON path', options.json_path], ['Condition', options.json_op === 'exists' ? 'the value exists' : `${JSON_OP_LABEL[options.json_op] || options.json_op} ${options.json_expect}`]] : []),
    ...(check.type === 'tls' ? [['Warn before', `${options.warn_days ?? 14} days`]] : []),
    ...(check.type === 'dns' ? [['Record', options.record_type || 'A'], ['DNS server', options.resolver || 'system default'], ['Expected', options.expected || 'any answer']] : []),
    ...(options.upside_down ? [['Upside down', 'up means the service is unreachable']] : []),
    ['Severity', check.severity === 'CRITICAL' ? 'Critical' : 'Warning'],
    ['Tags', check.tags.join(', ') || '—'],
  ]
  const ms = (value) => (value != null ? <>{value}<span className="ml-1 text-xs font-normal text-muted">ms</span></> : '—')
  const stats = [
    { label: 'Response now', value: ms(check.state !== 'down' && !check.paused ? check.response_ms : null) },
    { label: 'Average · 24 h', value: ms(detail?.average_24h) },
    { label: 'Uptime 24 h · 30 d · 1 y', value: <>{percent(check.uptime_24h)}<span className="ml-1.5 text-xs font-normal text-muted">{percent(check.uptime_30d)} · {percent(detail?.uptime_1y)}</span></> },
    { label: 'Certificate', value: check.cert_days != null ? `${check.cert_days} days` : '—' },
    { label: 'Last down', value: detail?.last_down ? <>{detail.last_down.ongoing ? 'now' : ago(detail.last_down.at)}<span className="ml-1.5 text-xs font-normal text-muted">for {detail.last_down.for}</span></> : detail ? 'never' : '—' },
  ]
  return (
    <section className="card overflow-hidden" aria-labelledby="check-detail-title">
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-[18px] py-3.5">
        <span className={`dot ${check.paused ? '' : check.state === 'down' ? 'dot-signal' : check.state === 'up' ? 'dot-ok' : 'dot-warn'}`} />
        <h2 id="check-detail-title" className="m-0 text-[17px] font-semibold text-paper">{check.name}</h2>
        <span className="chip">{TYPE_LABEL[check.type]}</span>
        <span className="mono min-w-0 flex-1 truncate text-xs text-muted">{shortTarget(check)}</span>
        {canEdit && <span className="flex gap-2">
          <button type="button" className="btn btn-sm" onClick={() => onPause(check, !check.paused)}>{check.paused ? 'Resume' : 'Pause'}</button>
          <button type="button" className="btn btn-sm" onClick={() => onEdit(check)}>Edit</button>
          <button type="button" className="btn btn-sm" onClick={() => onDelete(check)}>Delete</button>
        </span>}
      </header>
      <div className="grid grid-cols-2 divide-line border-b border-line md:grid-cols-5 md:divide-x">
        {stats.map(({ label, value }, index) => (
          <div key={label} className={`min-w-0 px-[18px] py-3.5 max-md:border-b max-md:border-line ${index === 4 ? 'max-md:col-span-2' : ''}`}><div className="truncate text-xs text-muted">{label}</div><div className="mt-1.5 text-[19px] font-semibold text-paper md:truncate">{value}</div></div>
        ))}
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-6 px-[18px] py-4">
          <div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="m-0 text-[13px] font-semibold text-paper">Response time <span className="ml-1.5 text-xs font-normal text-muted">ms · per server</span></h3>
              <span className="seg" role="group" aria-label="Range">{RANGE_LABELS.map(([id, label]) => <button key={id} type="button" aria-pressed={range === id} onClick={() => setRange(id)}>{label}</button>)}</span>
            </div>
            <div className="mt-3">{detail?.series ? <ResponseChart series={detail.series} range={range} compact={phone} /> : <div className="h-[150px]" />}</div>
          </div>
          {detail?.days && <Days days={detail.days} />}
          <div>
            <h3 className="m-0 text-[13px] font-semibold text-paper">Last 30 checks</h3>
            <div className="mt-2.5"><Strip check={check} /></div>
            <p className="mb-0 mt-2.5 text-xs text-muted">
              {check.paused ? 'Paused: no checks run and no alerts are sent.' : check.state === 'pending' ? 'Waiting for the first results from its servers.' : check.state === 'stale' ? `No recent results since ${check.state_since ? new Date(check.state_since).toLocaleString() : '—'}: its servers aren't running it, or their results don't arrive.` : `${check.state === 'down' ? 'Down' : 'Up'} since ${check.state_since ? new Date(check.state_since).toLocaleString() : '—'}.`}
              {check.last_result_at && ` Last result ${ago(check.last_result_at)}${check.last_detail ? `: ${check.last_detail}` : ''}.`}
              {check.quiet_until && ` New check: it alerts from ${new Date(check.quiet_until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}, so a wrong address doesn't page anyone.`}
            </p>
          </div>
        </div>
        <div className="space-y-6 border-line px-[18px] py-4 lg:border-l max-lg:border-t">
          <div>
            <h3 className="m-0 text-[13px] font-semibold text-paper">Events</h3>
            {detail?.events?.length ? <ul className="m-0 mt-3 list-none divide-y divide-line p-0">
              {detail.events.map((event) => <li key={`${event.kind}-${event.time}`} className="grid grid-cols-[64px_10px_minmax(0,1fr)] items-start gap-2.5 py-2 text-xs">
                <span className="text-muted">{ago(event.time)}</span><span className={`mt-1 h-2 w-2 rounded-[1px] ${event.kind === 'down' ? 'bg-signal' : 'bg-ok'}`} /><span className="text-paper">{event.text}</span>
              </li>)}
            </ul> : <p className="mb-0 mt-2 text-xs text-muted">{detail ? 'It hasn’t gone down yet.' : ''}</p>}
          </div>
          <div>
            <h3 className="m-0 text-[13px] font-semibold text-paper">Settings</h3>
            <dl className="mb-0 mt-3 grid grid-cols-[100px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
              {settings.map(([label, value]) => <Fragment key={label}><dt className="text-muted">{label}</dt><dd className="m-0 text-paper">{value}</dd></Fragment>)}
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}

function EmptyChecks({ canEdit, onNew }) {
  return (
    <div className="card px-[18px] py-10 text-center">
      <p className="m-0 text-sm font-medium text-paper">No checks yet.</p>
      <p className="mx-auto mb-0 mt-2 max-w-[520px] text-[13px] text-muted">A check tries a website or a port every minute or so from the servers you pick, and opens an incident when it stops answering.</p>
      {canEdit && <button type="button" className="btn btn-primary mt-4" onClick={onNew}><Icon name="plus" size={14} />New check</button>}
    </div>
  )
}

export default function AvailabilityPage({ user, onLogout }) {
  const phone = usePhone()
  const { data, error, reload, loadedAt } = useAvailability()
  const [params, setParams] = useSearchParams()
  const [editing, setEditing] = useState(null)   // null, 'new', or a check
  const [action, run] = useAction()
  const checks = data?.checks || []
  const selected = checks.find((check) => String(check.id) === params.get('check')) || null
  const canEdit = Boolean(data?.can_edit)

  const select = (check) => {
    const next = new URLSearchParams(params)
    if (selected?.id === check.id) next.delete('check'); else next.set('check', String(check.id))
    setParams(next, { replace: true })
    if (selected?.id !== check.id) requestAnimationFrame(() => document.getElementById('check-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }
  const pause = (check, paused) => run(async () => { await apiRequest(`/availability/checks/${check.id}/pause`, { method: 'POST', body: JSON.stringify({ paused }) }); reload() })
  const remove = (check) => {
    if (!window.confirm(`Delete the check “${check.name}” and its history?`)) return
    run(async () => { await apiRequest(`/availability/checks/${check.id}`, { method: 'DELETE' }); const next = new URLSearchParams(params); next.delete('check'); setParams(next, { replace: true }); reload() })
  }
  const saved = (check) => { setEditing(null); reload(); const next = new URLSearchParams(params); next.set('check', String(check.id)); setParams(next, { replace: true }) }
  const down = checks.filter((check) => check.state === 'down' && !check.paused)
  const unable = (data?.servers || []).filter((server) => !server.can_run_checks)

  const body = (
    <div className="space-y-6">
      {!phone && <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="m-0 text-[21px] font-semibold leading-7 text-paper">Availability</h1>
          <p className="mb-0 mt-1.5 max-w-[680px] text-[13px] leading-5 text-soft">Checks that your services answer, run from your own servers. Like Uptime Kuma, but every check knows which machine it ran from.</p>
        </div>
        <span className="flex flex-wrap gap-2">
          <button type="button" className="btn" disabled title="Coming in a later step">Status page<Soon /></button>
          <button type="button" className="btn" disabled title="Coming in a later step">Maintenance<Soon /></button>
          {canEdit && <button type="button" className="btn btn-primary" onClick={() => setEditing('new')}><Icon name="plus" size={14} />New check</button>}
        </span>
      </header>}
      {error && !data && <Notice>{error}</Notice>}
      <Notice>{action.error}</Notice>
      {data && <>
        {checks.length > 0 && <Summary data={data} />}
        <DownNow checks={down} canEdit={canEdit} onOpen={select} onPause={pause} />
        {checks.length ? <ChecksTable checks={checks} selectedId={selected?.id} onSelect={select} phone={phone} /> : <EmptyChecks canEdit={canEdit} onNew={() => setEditing('new')} />}
        {selected && <div id="check-detail" className="scroll-mt-20"><Detail key={selected.id} check={selected} refreshedAt={loadedAt} canEdit={canEdit} onEdit={setEditing} onPause={pause} onDelete={remove} /></div>}
        {unable.length > 0 && checks.length > 0 && <p className="m-0 text-xs text-muted">{unable.map((server) => server.hostname).join(', ')} {unable.length === 1 ? 'has' : 'have'} a collector older than 0.10.0 or {unable.length === 1 ? 'hasn’t' : 'haven’t'} reported yet, so {unable.length === 1 ? 'it' : 'they'} can’t run checks. <Link to="/admin#servers" className="link">Update the collector</Link>.</p>}
      </>}
      <Drawer open={editing !== null} title={editing && editing !== 'new' ? `Edit ${editing.name}` : 'New check'} description="Track a service you run. The servers you pick try it on every interval and report each result." onClose={() => setEditing(null)} width={680}>
        {editing !== null && <CheckForm check={editing === 'new' ? null : editing} servers={data?.servers || []} onSaved={saved} onCancel={() => setEditing(null)} />}
      </Drawer>
    </div>
  )

  if (phone) {
    return (
      <AppShell user={user} onLogout={onLogout} crumbs={[{ label: 'Availability' }]} bare phoneHeader>
        <PhoneHeader title="Availability" back={{ label: 'More', to: '/more' }} action={canEdit ? <button type="button" aria-label="New check" onClick={() => setEditing('new')} className="flex h-11 w-11 items-center justify-center text-paper"><Icon name="plus" size={18} /></button> : <span />} />
        <div className="px-4 pt-3">{body}</div>
      </AppShell>
    )
  }
  return <AppShell user={user} onLogout={onLogout} bare><div className="px-3 pb-8 pt-5 sm:px-4 lg:px-7 lg:pt-7">{body}</div></AppShell>
}

