// The Agent page: an inbox of incidents, one incident with its diagnosis and the proof behind it, and a chat
// about that incident. The diagnosis is built by the backend from stored data; the chat is the language model.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { usePolled } from '../dashboard/data.js'
import { formatAge, niceCeiling } from '../dashboard/logic.js'
import { Icon } from '../icons.jsx'
import { refreshOverview } from '../overviewStore.js'
import { serverLink, AppShell } from '../shell.jsx'
import { Notice, useAction, useCopy } from '../ui.jsx'

const SEVERITY = { CRITICAL: ['Critical', 'chip-signal', 'var(--color-signal)'], WARNING: ['Warning', 'chip-warn', 'var(--color-warn)'], INFO: ['Info', '', 'var(--color-edge)'] }
const TONE = { signal: 'bg-signal', warn: 'bg-warn', ok: 'bg-ok', plain: 'bg-dim' }
const clock = (time) => new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
const day = (time) => new Date(time).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
const post = (path, body) => apiRequest(path, { method: 'POST', body: JSON.stringify(body || {}) })

function stateLine(incident) {
  if (incident.status === 'resolved') return incident.resolution || 'Cleared on its own'
  if (incident.muted_until) return `Muted until ${clock(incident.muted_until)}`
  return `${incident.state === 'watching' ? 'Watching' : 'Diagnosed'}${incident.acknowledged_at ? ` · acknowledged${incident.acknowledged_by ? ` by ${incident.acknowledged_by}` : ''}` : ''}`
}

function InboxRow({ incident, selected, onSelect }) {
  const [label, chip, color] = SEVERITY[incident.severity] || SEVERITY.INFO
  const resolved = incident.status === 'resolved'
  return (
    <li>
      <button type="button" aria-current={selected ? 'true' : undefined} onClick={() => onSelect(incident.id)} className={`block w-full border-0 border-b border-solid border-line px-[18px] py-3.5 text-left transition-colors hover:bg-ink ${selected ? 'bg-ink' : 'bg-transparent'}`} style={{ boxShadow: resolved ? undefined : `inset 2px 0 0 ${color}` }}>
        <span className="flex items-center gap-2 text-xs text-muted">{!resolved && <span className={`chip ${chip}`}>{label}</span>}<span className="truncate">{incident.hostname}</span><span className="ml-auto shrink-0">{resolved ? day(incident.resolved_at) : formatAge(incident.created_at)}</span></span>
        <span className={`mt-1.5 block text-[13px] font-semibold leading-[18px] ${resolved ? 'text-soft' : 'text-paper'}`}>{incident.title}</span>
        <span className="mt-1.5 flex items-center gap-2 text-xs text-muted">{!resolved && <span className={`dot ${incident.state === 'watching' || incident.muted_until ? 'dot-warn' : 'dot-ok'}`} style={{ width: 6, height: 6 }} />}{stateLine(incident)}</span>
      </button>
    </li>
  )
}

// The metric the rule looks at, with its limit as a dashed line and a mark wherever something happened.
function Evidence({ evidence }) {
  const points = evidence.series.filter((point) => point.value != null)
  if (points.length < 2) return <p className="m-0 rounded-md border border-line px-[18px] py-8 text-center text-xs text-muted">Not enough samples in this window to draw.</p>
  const start = new Date(evidence.series[0].time).getTime()
  const span = Math.max(1, new Date(evidence.series[evidence.series.length - 1].time).getTime() - start)
  const percent = evidence.unit === '%'
  const inGb = evidence.unit === 'MB' && Math.max(evidence.limit || 0, ...points.map((point) => point.value)) >= 1024
  const scale = inGb ? 1024 : 1
  const top = percent ? 100 : niceCeiling(Math.max(evidence.limit || 0, ...points.map((point) => point.value)) / scale * 1.15)
  const x = (time) => ((new Date(time).getTime() - start) / span) * 100
  const y = (value) => (1 - Math.min(Math.max(value / scale, 0), top) / top) * 100
  const unit = percent ? '%' : inGb ? ' GB' : evidence.unit ? ` ${evidence.unit}` : ''
  let drawing = false
  const line = evidence.series.map((point) => { if (point.value == null) { drawing = false; return '' } const command = `${drawing ? 'L' : 'M'}${x(point.time).toFixed(2)},${y(point.value).toFixed(2)}`; drawing = true; return command }).join(' ')
  return (
    <figure className="m-0 rounded-md border border-line bg-ink px-[18px] pb-3 pt-5" role="img" aria-label={`${evidence.title} around the incident`}>
      <div className="relative ml-12 h-[150px]">
        {[top, top / 2, 0].map((tick) => <span key={tick} aria-hidden="true" className="absolute right-full -translate-y-1/2 whitespace-nowrap pr-2.5 text-[11px] text-muted" style={{ top: `${(1 - tick / top) * 100}%` }}>{Number(tick.toFixed(1))}{unit}</span>)}
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden="true">
          {[top, top / 2, 0].map((tick) => <line key={tick} x1="0" x2="100" y1={(1 - tick / top) * 100} y2={(1 - tick / top) * 100} stroke="var(--color-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
          {(evidence.marks || []).map((mark) => <line key={mark.time} x1={x(mark.time)} x2={x(mark.time)} y1="0" y2="100" stroke="var(--color-signal)" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
          {evidence.limit != null && evidence.limit / scale <= top && <line x1="0" x2="100" y1={y(evidence.limit)} y2={y(evidence.limit)} stroke="var(--color-signal)" strokeWidth="1" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />}
          <path d={line} fill="none" stroke="var(--color-paper)" strokeWidth="1.4" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
        {(evidence.marks || []).map((mark) => <span key={mark.time} aria-hidden="true" className="absolute -top-[3px] h-[6px] w-[6px] -translate-x-1/2 bg-signal" style={{ left: `${x(mark.time)}%` }} />)}
      </div>
      <div aria-hidden="true" className="ml-12 mt-2 flex justify-between text-[11px] text-muted"><span>{clock(evidence.series[0].time)}</span><span>{clock(evidence.series[evidence.series.length - 1].time)}</span></div>
      <figcaption className="ml-12 mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-xs text-soft">
        {(evidence.marks || []).length > 0 && <span><span className="mr-2 inline-block h-[6px] w-[6px] bg-signal align-middle" />{evidence.mark_label || 'Event'}</span>}
        {evidence.limit != null && <span><span className="mr-2 inline-block w-4 border-t border-dashed border-signal align-middle" />{evidence.limit_label || 'Limit'}</span>}
      </figcaption>
    </figure>
  )
}

function Command({ step }) {
  const [copied, copy] = useCopy()
  return (
    <div className="mt-2.5 flex items-center gap-3 rounded-md border border-line bg-bg py-1.5 pl-3 pr-1.5">
      {step.label && <span className="mono shrink-0 text-dim">{step.label}</span>}
      <code className="mono custom-scroll min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-paper">{step.command}</code>
      <button type="button" className="btn btn-sm btn-ghost shrink-0" onClick={() => copy(step.command)}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}</button>
    </div>
  )
}

function Incident({ incident, canAct, onChanged }) {
  const [state, run] = useAction()
  const [muteFor, setMuteFor] = useState(60)
  const found = incident.diagnosis
  const [label, chip] = SEVERITY[incident.severity] || SEVERITY.INFO
  const resolved = incident.status === 'resolved'
  const act = (action, body) => run(async () => { await post(`/incidents/${incident.id}/${action}`, body); refreshOverview(); onChanged() })
  const denied = canAct ? undefined : 'Only owners and admins can change an incident.'
  const section = 'mt-7'
  return (
    <article className="px-5 pb-10 pt-6 lg:px-7" aria-label={`${incident.reference}: ${incident.title}`}>
      <div className="flex flex-wrap items-center gap-2"><span className={`chip ${chip}`}>{label}</span><span className={`chip ${resolved || found.state === 'diagnosed' ? 'chip-ok' : 'chip-warn'}`}>{resolved ? 'Resolved' : incident.muted_until ? 'Muted' : found.state === 'watching' ? 'Watching' : 'Diagnosed'}</span><span className="mono text-xs text-muted">{incident.reference}</span></div>
      <h1 className="m-0 mt-3 text-[21px] font-semibold leading-7 text-paper">{incident.title}</h1>
      <dl className="m-0 mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted">
        <div>Server <span className="font-medium text-paper">{incident.hostname}</span></div>
        {found.container && <div>Container <span className="mono text-paper">{found.container}</span></div>}
        <div>Opened <span className="font-medium text-paper">{clock(incident.created_at)}</span> · {formatAge(incident.created_at)} ago</div>
        {resolved && <div>Resolved <span className="font-medium text-paper">{day(incident.resolved_at)} {clock(incident.resolved_at)}</span></div>}
        <div>Notified <span className="font-medium text-paper">{incident.notified ? 'sent to the channels people chose' : 'nobody yet'}</span></div>
        {incident.acknowledged_at && <div>Acknowledged <span className="font-medium text-paper">{incident.acknowledged_by ? `by ${incident.acknowledged_by}` : formatAge(incident.acknowledged_at)}</span></div>}
      </dl>
      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        {!resolved && <>
          <button type="button" className={`btn ${incident.acknowledged_at ? '' : 'btn-primary'}`} disabled={!canAct || state.busy || Boolean(incident.acknowledged_at)} title={denied} onClick={() => act('acknowledge')}><Icon name="check" size={14} />{incident.acknowledged_at ? 'Acknowledged' : 'Acknowledge'}</button>
          <button type="button" className="btn" disabled={!canAct || state.busy} title={denied || 'If the cause is still there, a new incident opens at the next check-in.'} onClick={() => act('resolve')}>Mark resolved</button>
          {incident.muted_until
            ? <button type="button" className="btn" disabled={!canAct || state.busy} title={denied} onClick={() => act('unmute')}>Unmute</button>
            : <span className="inline-flex"><button type="button" className="btn rounded-r-none" disabled={!canAct || state.busy} title={denied} onClick={() => act('mute', { minutes: muteFor })}>Mute</button><select className="select -ml-px rounded-l-none" aria-label="Mute for how long" value={muteFor} disabled={!canAct} onChange={(event) => setMuteFor(Number(event.target.value))}><option value={60}>1 h</option><option value={240}>4 h</option><option value={1440}>1 day</option><option value={10080}>1 week</option></select></span>}
        </>}
        <Link to={serverLink(incident.server_id, found.container ? 'containers' : found.kind?.startsWith('network') ? 'network' : 'overview')} className="btn btn-ghost">{found.container ? 'Open container' : 'Open server'}<Icon name="arrow" size={13} /></Link>
      </div>
      {state.error && <p role="alert" className="mb-0 mt-2 text-xs text-warn">{state.error}</p>}

      <p className="mb-0 mt-6 max-w-[760px] text-[15px] leading-[25px] text-paper">{found.summary}</p>

      {found.evidence && <section className={section} aria-label="Evidence">
        <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2"><h2 className="m-0 text-sm font-semibold text-paper">Evidence</h2><span className="text-xs text-muted">{found.evidence.title} · {clock(found.window.start)} to {resolved ? clock(found.window.end) : 'now'}{found.evidence.limit != null ? ` · ${found.evidence.limit_label?.toLowerCase() || 'limit'} drawn dashed` : ''}</span></div>
        <Evidence evidence={found.evidence} />
      </section>}

      {found.lines.length > 0 && <section className="mt-3" aria-label="What was recorded">
        <ul className="mono custom-scroll m-0 max-h-[240px] list-none space-y-1 overflow-auto rounded-md border border-line bg-ink px-[18px] py-3.5 leading-5">
          {found.lines.map((line, index) => <li key={`${line.time}-${index}`} className={line.strong ? 'text-signal' : 'text-soft'}><span className="text-dim">{clock(line.time)}</span> <span className="text-muted">{line.source}</span> {line.text}</li>)}
        </ul>
        <p className="mb-0 mt-2 text-xs text-muted">These are the events Infradeck recorded on {incident.hostname}, not the container's own log, which it can't read.</p>
      </section>}

      {found.signals.length > 0 && <section className={section} aria-label="Why the agent thinks so">
        <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2"><h2 className="m-0 text-sm font-semibold text-paper">Why the agent thinks so</h2><span className="text-xs text-soft">{found.agreeing} of {found.signals.length} checks against the data hold</span></div>
        <ul className="card m-0 list-none divide-y divide-line p-0">
          {found.signals.map((signal) => <li key={signal.text} className="flex items-center gap-3.5 px-[18px] py-3 text-[13px]"><Icon name={signal.ok ? 'check' : 'close'} size={14} className={signal.ok ? 'shrink-0 text-ok' : 'shrink-0 text-muted'} /><span className="min-w-0 flex-1 text-paper">{signal.text}</span><span className="shrink-0 text-xs text-muted">{signal.source}</span></li>)}
        </ul>
      </section>}

      {found.steps.length > 0 && <section className={section} aria-label="How to fix it">
        <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2"><h2 className="m-0 text-sm font-semibold text-paper">How to fix it</h2><span className="text-xs text-muted">You run these yourself. Infradeck can't change your servers.</span></div>
        <ol className="card m-0 list-none divide-y divide-line p-0">
          {found.steps.map((step, index) => (
            <li key={step.title} className="grid grid-cols-[34px_minmax(0,1fr)] px-[18px] py-4">
              <span className="mono pt-0.5 text-signal">{String(index + 1).padStart(2, '0')}</span>
              <div className="min-w-0"><p className="m-0 text-sm font-semibold text-paper">{step.title}{step.recommended && <span className="chip ml-2.5 align-middle font-normal">recommended</span>}</p><p className="mb-0 mt-1 text-[13px] leading-5 text-muted">{step.text}</p>{step.command && <Command step={step} />}</div>
            </li>
          ))}
        </ol>
      </section>}
      {!found.steps.length && !found.signals.length && <Notice tone="plain">There is no playbook for this kind of incident yet. The description above is everything the rule knows.</Notice>}

      <section className={section} aria-label="Timeline">
        <h2 className="m-0 mb-2.5 text-sm font-semibold text-paper">Timeline</h2>
        <ol className="m-0 list-none space-y-2.5 p-0">{found.timeline.map((item, index) => <li key={`${item.time}-${index}`} className="grid grid-cols-[96px_14px_minmax(0,1fr)] items-baseline text-[13px]"><time dateTime={item.time} className="mono text-muted" title={new Date(item.time).toLocaleString()}>{day(item.time).split(',')[0]} {clock(item.time)}</time><span aria-hidden="true" className={`h-[7px] w-[7px] self-center rounded-[1px] ${TONE[item.tone] || TONE.plain}`} /><span className="text-soft">{item.text}</span></li>)}</ol>
      </section>
    </article>
  )
}

const SUGGESTIONS = { default: ['Is anything else close to its limit?', 'What changed on this server today?', 'Explain this incident in one paragraph'], none: ['Is anything close to its limit?', 'Which server needs attention first?', 'What happened overnight?'] }

function Chat({ user, incident, enabled }) {
  const conversationId = `${incident ? `incident-${incident.id}` : 'general'}-u${user.id}`
  const [messages, setMessages] = useState(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const exists = useRef(false)
  const end = useRef(null)

  useEffect(() => {
    let active = true
    setMessages(null); setError(''); exists.current = false
    apiRequest('/conversations', { method: 'GET' }).then((data) => {
      if (!active) return
      const found = data.conversations.find((item) => item.id === conversationId)
      exists.current = Boolean(found)
      setMessages(found ? found.messages : [])
    }).catch((requestError) => { if (active) { setMessages([]); setError(requestError.message || 'Could not load the chat.') } })
    return () => { active = false }
  }, [conversationId])
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [messages, busy])

  const send = async (text) => {
    const message = text.trim()
    if (!message || busy) return
    setBusy(true); setError(''); setDraft('')
    setMessages((current) => [...(current || []), { id: `local-${Date.now()}`, role: 'user', text: message }])
    try {
      if (!exists.current) { await post('/conversations', { conversation_id: conversationId, title: incident ? `${incident.reference} · ${incident.title}`.slice(0, 250) : 'New chat' }); exists.current = true }
      const reply = await post('/agent/chat', { conversation_id: conversationId, message, incident_id: incident?.id })
      setMessages((current) => [...current, { id: `reply-${Date.now()}`, role: 'agent', text: reply.message }])
    } catch (requestError) { setError(requestError.message || 'The agent could not answer.') } finally { setBusy(false) }
  }
  const restart = async () => {
    if (exists.current) { try { await apiRequest(`/conversations/${conversationId}`, { method: 'DELETE' }) } catch { /* it is gone either way for this page */ } }
    exists.current = false; setMessages([]); setError('')
  }

  return (
    <aside className="flex min-h-[420px] flex-col xl:h-full xl:min-h-0" aria-label="Ask the agent">
      <div className="border-b border-line px-5 py-4">
        <div className="flex items-baseline justify-between"><h2 className="m-0 text-[15px] font-semibold text-paper">Ask the agent</h2><button type="button" className="border-0 bg-transparent p-0 text-xs text-muted hover:text-paper" onClick={restart}>New chat</button></div>
        <div className="mt-2 flex items-center gap-2 text-xs text-muted">About <span className="chip max-w-full truncate text-paper">{incident ? `${incident.reference} · ${incident.hostname}` : 'any server'}</span></div>
      </div>
      <div className="custom-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {!enabled && <Notice tone="plain">Chat with the agent has been turned off by an admin. The diagnosis on the left doesn't need it.</Notice>}
        {enabled && messages === null && <p className="m-0 text-xs text-muted">Loading…</p>}
        {enabled && messages?.length === 0 && <div className="space-y-2">
          <p className="m-0 text-xs text-muted">{incident ? 'The agent is given this incident\'s facts with your question.' : 'Ask about any server.'} Suggestions:</p>
          {(incident ? SUGGESTIONS.default : SUGGESTIONS.none).map((text) => <button key={text} type="button" className="block w-full rounded-md border border-line bg-transparent px-3 py-2 text-left text-[13px] text-soft hover:border-edge hover:text-paper" onClick={() => send(text)}>{text}</button>)}
        </div>}
        {enabled && (messages || []).map((message) => (message.role === 'user'
          ? <div key={message.id} className="ml-8 rounded-md border border-line bg-ink px-3.5 py-2.5 text-[13px] leading-5 text-paper">{message.text}</div>
          : <div key={message.id}><div className="mb-1.5 flex items-center gap-2 text-xs text-muted"><span aria-hidden="true" className="inline-flex flex-col gap-[2px]"><i className="block h-[3px] w-[5px] bg-paper" /><i className="ml-[2px] block h-[3px] w-[5px] bg-signal" /><i className="block h-[3px] w-[5px] bg-paper" /></span>Agent</div><p className="m-0 whitespace-pre-wrap text-[13px] leading-[21px] text-paper">{message.text}</p></div>))}
        {busy && <p className="m-0 flex items-center gap-2.5 text-xs text-muted"><span className="spinner" />Thinking…</p>}
        {error && <Notice>{error}</Notice>}
        <div ref={end} />
      </div>
      <form className="border-t border-line p-4" onSubmit={(event) => { event.preventDefault(); send(draft) }}>
        <div className="flex items-end gap-2 rounded-md border border-edge bg-ink p-1.5 focus-within:border-muted">
          <textarea rows={2} className="min-h-[40px] flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-[13px] text-paper outline-none placeholder:text-dim" placeholder={incident ? 'Ask about this incident or any server…' : 'Ask about any server…'} aria-label="Your question" maxLength={8000} disabled={!enabled} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(draft) } }} />
          <button type="submit" className="btn btn-primary h-8 w-8 shrink-0 p-0" aria-label="Send" disabled={!enabled || busy || !draft.trim()}><Icon name="arrow" size={14} /></button>
        </div>
        <p className="mb-0 mt-2 text-[11px] leading-4 text-muted">Reads metrics and events. Can't run commands on your servers. Your question and the incident's facts are sent to the model provider.</p>
      </form>
    </aside>
  )
}

export default function AgentPage({ user, onLogout }) {
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState('open')
  const { data: inbox, error, reload } = usePolled('/incidents', 10)
  const { data: config } = usePolled('/agent/config', null)
  const all = useMemo(() => (inbox ? [...inbox.open, ...inbox.muted, ...inbox.resolved] : []), [inbox])
  const requested = Number(params.get('incident'))
  const selectedId = all.some((item) => item.id === requested) ? requested : inbox?.open[0]?.id ?? null
  const selected = all.find((item) => item.id === selectedId) || null
  const { data: detail, error: detailError, reload: reloadDetail } = usePolled(selectedId ? `/incidents/${selectedId}` : null, 20)
  const select = (id) => setParams({ incident: String(id) }, { replace: true })
  // Following a link to a resolved or muted incident opens the tab it lives in.
  useEffect(() => { if (selected) setTab(selected.status === 'resolved' ? 'resolved' : selected.muted_until ? 'muted' : 'open') }, [selected?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const list = inbox ? inbox[tab] : []
  const crumbs = selected ? [{ label: 'Agent', to: '/agent' }, { label: selected.reference }] : [{ label: 'Agent' }]

  return (
    <AppShell user={user} onLogout={onLogout} crumbs={crumbs} bare fitViewport>
      <div className="grid xl:h-full xl:grid-cols-[minmax(300px,380px)_minmax(0,1fr)_minmax(320px,440px)] xl:divide-x xl:divide-line">
        <section className="flex min-h-0 flex-col border-b border-line xl:border-b-0" aria-label="Incidents">
          <div className="px-[18px] pb-3 pt-5">
            <div className="flex items-baseline gap-2.5"><h2 className="m-0 text-[15px] font-semibold text-paper">Incidents</h2><span className="text-xs text-muted">{inbox ? `${inbox.open.length} open` : ''}</span></div>
            <div className="seg mt-3" role="tablist" aria-label="Which incidents">
              {[['open', 'Open', inbox?.open.length], ['resolved', 'Resolved', inbox?.resolved_count], ['muted', 'Muted', inbox?.muted.length]].map(([key, label, count]) => <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label} {count ?? ''}</button>)}
            </div>
          </div>
          <ul className="custom-scroll m-0 max-h-[320px] min-h-0 flex-1 list-none overflow-y-auto border-t border-line p-0 xl:max-h-none">
            {list.map((incident) => <InboxRow key={incident.id} incident={incident} selected={incident.id === selectedId} onSelect={select} />)}
            {inbox && !list.length && <li className="px-[18px] py-8 text-center text-xs text-muted">{tab === 'open' ? 'Nothing is open. Every drawer is in.' : tab === 'muted' ? 'Nothing is muted.' : 'Nothing has been resolved yet.'}</li>}
            {!inbox && <li className="px-[18px] py-8 text-center text-xs text-muted">{error || 'Loading…'}</li>}
          </ul>
        </section>
        <div className="custom-scroll min-h-0 xl:overflow-y-auto">
          {detail && detail.id === selectedId ? <Incident incident={detail} canAct={Boolean(inbox?.can_act)} onChanged={() => { reload(); reloadDetail() }} />
            : selectedId ? <p className="m-0 px-7 py-16 text-center text-[13px] text-muted">{detailError || 'Loading the incident…'}</p>
              : <div className="px-7 py-16 text-center"><p className="m-0 text-sm font-medium text-paper">{inbox && !all.length ? 'No incidents yet.' : 'Nothing needs you right now.'}</p><p className="mb-0 mt-2 text-[13px] text-muted">{inbox && !all.length ? 'When a rule fires on one of your servers, the incident shows up here with what was found and how to fix it.' : 'Pick a resolved incident on the left to see how it went.'}</p></div>}
        </div>
        <div className="min-h-0 border-t border-line xl:border-t-0"><Chat key={selectedId ?? 'none'} user={user} incident={detail && detail.id === selectedId ? detail : null} enabled={config ? config.chat_enabled : true} /></div>
      </div>
    </AppShell>
  )
}
