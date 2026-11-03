// A server's week on one strip and in readable lines: alerts, containers, network and system events together.
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Icon } from '../icons.jsx'
import { keptFor } from '../ui.jsx'
import { usePolled } from './data.js'
import { EVENT_SOURCES, dayHeading, describeEvent, describeGroup, eventCategory, eventStrip, eventsToCsv, groupRepeats } from './logic.js'

const DAYS = 7
const PAGE = 40
const MARK = { signal: 'bg-signal', warn: 'bg-warn', ok: 'bg-ok', paper: 'bg-paper', plain: 'bg-dim' }
const BAR = { signal: 'var(--color-signal)', warn: 'var(--color-warn)', plain: 'var(--color-rail)' }
const TAG = { signal: 'chip-signal', warn: 'chip-warn', ok: 'chip-ok' }
const clock = (time) => new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function Strip({ events }) {
  const strip = useMemo(() => eventStrip(events, DAYS), [events])
  return (
    <div className="grid gap-x-2.5 border-b border-line px-[18px] pb-4 pt-4" style={{ gridTemplateColumns: `repeat(${strip.length}, minmax(0, 1fr))` }} role="img" aria-label={`Events per day over the last ${DAYS} days: ${strip.map((day) => `${day.label} ${day.count}`).join(', ')}`}>
      {strip.map((day) => (
        <div key={day.key} className="min-w-0">
          <div className={`flex h-10 items-end gap-0.5 border-b ${day.today ? 'border-paper' : 'border-transparent'}`}>
            {day.bars.map((bar, index) => <span key={index} className="flex-1 rounded-t-[1px]" style={{ height: bar.count ? `${Math.round(bar.height * 100)}%` : 3, background: bar.count ? BAR[bar.tone] : 'var(--color-edge)' }} />)}
          </div>
          <div className={`mt-2.5 flex justify-between text-[11px] ${day.today ? 'font-medium text-paper' : 'text-muted'}`}><span>{day.label}</span><span>{day.count}</span></div>
        </div>
      ))}
    </div>
  )
}

function Row({ group, serverId, grouped }) {
  const [open, setOpen] = useState(false)
  const line = grouped ? describeGroup(group) : { ...describeEvent(group.events[0]), count: 1 }
  const first = group.events[0]
  const many = line.count > 1
  let action = null
  if (many) action = <button type="button" className="link" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? 'Hide' : `Show ${line.count}`}</button>
  else if (first.kind === 'alert_resolved') action = <Link to={`/statistics?server=${serverId}`} className="link">See chart</Link>
  else if (line.tag) action = <span className={`chip ${TAG[line.tag.tone] || ''}`}>{line.tag.text}</span>
  return <>
    <li className="grid min-h-[44px] grid-cols-[52px_14px_minmax(0,1fr)_auto] items-center gap-x-3 border-t border-line px-[18px] py-2.5 text-xs md:grid-cols-[58px_14px_150px_minmax(0,1fr)_auto]">
      <time dateTime={first.time} className="mono text-muted" title={new Date(first.time).toLocaleString()}>{clock(first.time)}</time>
      <span aria-hidden="true" className={`h-2 w-2 rounded-[2px] ${MARK[line.tone] || MARK.plain}`} />
      <span className="hidden truncate text-soft md:block">{line.source}</span>
      <span className="min-w-0 text-soft"><span className="mr-1.5 text-soft md:hidden">{line.source}:</span>{line.lead && <span className="font-medium text-paper">{line.lead} </span>}{line.text}</span>
      <span className="justify-self-end whitespace-nowrap">{action}</span>
    </li>
    {open && group.events.map((event, index) => {
      const child = describeEvent(event)
      return <li key={`${event.time}-${index}`} className="grid grid-cols-[52px_14px_minmax(0,1fr)] gap-x-3 bg-sunken px-[18px] py-1.5 text-[11px] text-muted md:grid-cols-[58px_14px_150px_minmax(0,1fr)]"><time dateTime={event.time} className="mono">{clock(event.time)}</time><span /><span className="hidden md:block" /><span className="min-w-0">{child.lead && <span className="text-soft">{child.lead} </span>}{child.text}</span></li>
    })}
  </>
}

export function EventsTab({ server, pending, isAdmin, onReview }) {
  const { data, error } = usePolled(`/monitoring/servers/${server.id}/events?days=${DAYS}`, 30)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [grouped, setGrouped] = useState(true)
  const [limit, setLimit] = useState(PAGE)

  // Machines asking to join are the fleet's news, told on the machine that runs the dashboard.
  const events = useMemo(() => {
    const joined = server.is_dashboard_host && isAdmin ? (pending || []).filter((item) => item.status === 'pending').map((item) => ({ time: item.created_at, source: 'infradeck', kind: 'pending_collector', title: item.hostname, detail: `asked to join the fleet${item.ip_address ? ` from ${item.ip_address}` : ''}` })) : []
    return [...joined, ...(data?.events || [])].sort((a, b) => (a.time < b.time ? 1 : -1))
  }, [data, pending, server.is_dashboard_host, isAdmin])

  if (!data) return <p className="m-0 px-[18px] py-10 text-center text-[13px] text-muted">{error || 'Loading events…'}</p>

  const counts = { all: events.length }
  for (const key of Object.keys(EVENT_SOURCES)) counts[key] = events.filter((event) => eventCategory(event) === key).length
  const needle = query.trim().toLowerCase()
  const matching = events.filter((event) => (category === 'all' || eventCategory(event) === category) && (!needle || `${event.title || ''} ${event.detail || ''} ${event.kind} ${event.source}`.toLowerCase().includes(needle)))
  const groups = grouped ? groupRepeats(matching) : matching.map((event) => ({ source: event.source, kind: event.kind, events: [event] }))
  const visible = groups.slice(0, limit)
  const days = []
  for (const group of visible) {
    const heading = dayHeading(group.events[0].time)
    if (!days.length || days[days.length - 1].heading !== heading) days.push({ heading, groups: [] })
    days[days.length - 1].groups.push(group)
  }

  return <>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 border-b border-line px-[18px] py-3">
      <label className="relative block w-full max-w-[262px]">
        <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-[9px] text-muted" />
        <input type="search" className="field pl-9" value={query} onChange={(event) => { setQuery(event.target.value); setLimit(PAGE) }} placeholder="Search events" aria-label={`Search events on ${server.hostname}`} />
      </label>
      <div className="seg" role="group" aria-label="Show">
        <button type="button" aria-pressed={category === 'all'} onClick={() => { setCategory('all'); setLimit(PAGE) }}>All {counts.all}</button>
        {Object.entries(EVENT_SOURCES).map(([key, label]) => <button key={key} type="button" aria-pressed={category === key} onClick={() => { setCategory(key); setLimit(PAGE) }}>{label} {counts[key]}</button>)}
      </div>
      <span className="ml-auto flex items-center gap-3.5">
        <label className="flex cursor-pointer items-center gap-2.5 text-xs text-soft">
          <input type="checkbox" role="switch" className="peer sr-only" checked={grouped} onChange={(event) => setGrouped(event.target.checked)} />
          <span aria-hidden="true" className="relative h-[18px] w-8 rounded-full border border-edge bg-raised transition-colors after:absolute after:left-[2px] after:top-[2px] after:h-3 after:w-3 after:rounded-full after:bg-muted after:transition-all peer-checked:border-paper peer-checked:bg-paper peer-checked:after:left-[16px] peer-checked:after:bg-bg peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-paper" />
          Group repeats
        </label>
        <button type="button" className="btn" disabled={!matching.length} onClick={() => download(`${server.hostname}-events.csv`, eventsToCsv(matching))}><Icon name="download" size={14} />Export</button>
      </span>
    </div>
    <Strip events={matching} />
    {days.length ? days.map((day) => (
      <div key={day.heading}>
        <div className="bg-sunken px-[18px] py-2.5 text-[11px] font-semibold tracking-[0.1em] text-muted">{day.heading}</div>
        <ul className="m-0 list-none p-0">
          {day.groups.map((group, index) => group.kind === 'pending_collector'
            ? <li key={`${group.events[0].time}-${index}`} className="grid min-h-[44px] grid-cols-[52px_14px_minmax(0,1fr)_auto] items-center gap-x-3 border-t border-line px-[18px] py-2 text-xs md:grid-cols-[58px_14px_150px_minmax(0,1fr)_auto]">
              <time dateTime={group.events[0].time} className="mono text-muted">{clock(group.events[0].time)}</time><span aria-hidden="true" className="h-2 w-2 rounded-[2px] bg-paper" /><span className="hidden text-soft md:block">Infradeck</span>
              <span className="min-w-0 text-soft"><span className="font-medium text-paper">{group.events[0].title} </span>{group.events[0].detail}</span>
              <button type="button" className="btn btn-sm" onClick={onReview}>Review</button>
            </li>
            : <Row key={`${group.events[0].time}-${group.kind}-${index}`} group={group} serverId={server.id} grouped={grouped} />)}
        </ul>
      </div>
    )) : <p className="m-0 px-[18px] py-10 text-center text-[13px] text-muted">{events.length ? 'No events match this search.' : `Nothing happened on ${server.hostname} in the last ${DAYS} days.`}</p>}
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-[18px] py-3 text-xs text-muted">
      <span>Showing {visible.length} of {groups.length} {grouped ? 'grouped ' : ''}events from the last {DAYS} days. Events are kept {data.retention_days === 0 ? 'forever' : `for ${keptFor(data.retention_days)}`}.</span>
      {groups.length > visible.length && <button type="button" className="link" onClick={() => setLimit((value) => value + PAGE)}>Show {Math.min(PAGE, groups.length - visible.length)} more</button>}
    </div>
  </>
}
