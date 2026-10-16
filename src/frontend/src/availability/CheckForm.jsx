// The New check / Edit form in the drawer: the type, what to check, from which servers, the timing in numbers
// with one sentence saying what they mean together, what counts as up, and how bad it is when it's down.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { Check, Field, Switch, useAction } from '../ui.jsx'

// Every type of the design; the ones not built yet are shown, but can't be picked.
const TYPES = [
  ['http', 'HTTP(s)', 'Status code of a URL'], ['keyword', 'Keyword', 'Text on the page'], ['json', 'JSON query', 'A value in a JSON reply'],
  ['tcp', 'TCP port', 'Port accepts connections'], ['ping', 'Ping', 'Host answers ICMP'], ['dns', 'DNS', 'Record resolves as expected'],
  ['docker', 'Docker container', 'Running and healthy'], ['tls', 'TLS certificate', 'Valid and not expiring'], ['push', 'Push', 'A job pings Infradeck'],
]
const READY = new Set(['http', 'keyword', 'tcp'])

const initial = (check, servers) => {
  const options = check?.options || {}
  const first = servers.find((server) => server.can_run_checks) || servers[0]
  return {
    type: check?.type || 'http', name: check?.name || '', tags: (check?.tags || []).join(', '), target: check?.target || '',
    keyword: options.keyword || '', case_sensitive: Boolean(options.case_sensitive),
    server_ids: check ? check.servers.map((server) => server.id) : first ? [first.id] : [], rule: check?.rule || 'all',
    interval_seconds: String(check?.interval_seconds ?? 60), retries: String(check?.retries ?? 2), retry_interval_seconds: String(check?.retry_interval_seconds ?? 20), timeout_seconds: String(check?.timeout_seconds ?? 48),
    accepted_codes: (options.accepted_codes || ['200-299']).join(', '), follow_redirects: options.follow_redirects ?? true, ignore_tls: Boolean(options.ignore_tls), upside_down: Boolean(options.upside_down),
    severity: check?.severity || 'CRITICAL',
  }
}

// What the backend would refuse, said next to the field before saving.
function problems(form, web) {
  const found = {}
  const target = form.target.trim()
  if (web && target && !/^https?:\/\/[^\s/:?#]+/i.test(target)) found.target = /^[\w.-]+(:\d+)?(\/|$)/.test(target) ? `Add http:// or https:// in front, like http://${target}` : 'The URL must start with http:// or https://.'
  if (!web && target && !/^([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]):\d{1,5}$/.test(target)) found.target = /^[A-Za-z0-9.-]+$/.test(target) ? `Add the port, like ${target}:22` : 'Enter host:port, like 10.0.0.12:3306.'
  if (form.type === 'keyword' && !form.keyword) found.keyword = 'Enter the text to look for.'
  const whole = (value, low, high) => /^\d+$/.test(String(value).trim()) && Number(value) >= low && Number(value) <= high
  if (!whole(form.interval_seconds, 20, 86400)) found.interval_seconds = 'From 20 s to 24 h.'
  if (!whole(form.retries, 0, 10)) found.retries = 'From 0 to 10.'
  if (!whole(form.retry_interval_seconds, 20, 86400)) found.retry_interval_seconds = 'At least 20 s.'
  if (!whole(form.timeout_seconds, 1, 48)) found.timeout_seconds = 'From 1 to 48 s.'
  else if (whole(form.interval_seconds, 20, 86400) && Number(form.timeout_seconds) >= Number(form.interval_seconds)) found.timeout_seconds = 'Shorter than the interval.'
  if (web && !form.accepted_codes.split(',').map((code) => code.trim()).filter(Boolean).every((code) => /^[1-5]\d\d(-[1-5]\d\d)?$/.test(code))) found.accepted_codes = 'Codes or ranges like 200-299, 301.'
  if (!form.server_ids.length) found.server_ids = 'Pick at least one server.'
  return found
}

const duration = (seconds) => (seconds < 90 ? `${Math.round(seconds)} seconds` : seconds < 5400 ? `${Math.round(seconds / 60)} ${Math.round(seconds / 60) === 1 ? 'minute' : 'minutes'}` : `${Math.round(seconds / 3600)} hours`)

// How long the form waits for a test: the collectors ask every 10 s when idle, then the check may take its timeout.
const TEST_WAIT_MS = 75_000

// "Test passed · 200 OK · 142 ms from both servers", or what went wrong and where.
function describeTest(test) {
  const done = test.servers.filter((server) => server.state === 'done')
  const waiting = test.servers.filter((server) => server.state === 'waiting')
  const names = (servers) => (servers.length === test.servers.length && servers.length > 1 ? (servers.length === 2 ? 'both servers' : 'all servers') : servers.map((server) => server.hostname).join(', '))
  const failed = done.filter((server) => !server.ok)
  if (failed.length) return { tone: 'warn', text: `Test failed · ${failed.map((server) => `${server.detail || (server.code ? `HTTP ${server.code}` : 'no answer')} from ${server.hostname}`).join(', ')}` }
  if (waiting.length) return { tone: 'muted', text: test.expired ? `No answer from ${names(waiting)}: is its collector running?` : `Testing from ${names(test.servers)}…` }
  const times = done.map((server) => server.response_ms).filter((value) => value != null)
  const code = done.find((server) => server.code)?.code
  return { tone: 'ok', text: `Test passed${code ? ` · ${code} OK` : ''}${times.length ? ` · ${Math.round(times.reduce((a, b) => a + b, 0) / times.length)} ms` : ''} from ${names(done)}` }
}

// "Marked down after 3 failed checks in a row, so about 1 minute after it breaks."
function TimingSentence({ form }) {
  const interval = Number(form.interval_seconds), retries = Number(form.retries), retry = Number(form.retry_interval_seconds)
  if (![interval, retries, retry].every(Number.isFinite) || interval <= 0) return null
  const failures = retries + 1
  // The first failure comes on average half an interval after it breaks; the retries follow at the retry interval.
  const after = interval / 2 + retries * retry
  return (
    <p className="m-0 rounded-md border border-line bg-ink px-3.5 py-2.5 text-xs leading-5 text-soft">
      Marked <b className="font-semibold text-paper">down</b> after <b className="font-semibold text-paper">{failures} failed {failures === 1 ? 'check' : 'checks in a row'}</b>, so about <b className="font-semibold text-paper">{duration(after)}</b> after it breaks. Up again on the first good check.
    </p>
  )
}

function Heading({ children, detail }) {
  return <h3 className="m-0 border-t border-line pt-5 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted first:border-t-0 first:pt-0">{children}{detail && <span className="ml-2 font-normal normal-case tracking-normal text-dim">{detail}</span>}</h3>
}

function Toggle({ checked, onChange, title, detail }) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <span className="pt-0.5"><Switch checked={checked} onChange={onChange} label={title} /></span>
      <span><span className="block text-[13px] text-paper">{title}</span><span className="mt-0.5 block text-xs text-muted">{detail}</span></span>
    </label>
  )
}

export function CheckForm({ check, servers, onSaved, onCancel }) {
  const [form, setForm] = useState(() => initial(check, servers))
  const [state, run] = useAction()
  const [tried, setTried] = useState(false)
  const [test, setTest] = useState(null)   // null, { running: true }, a test from the backend, or { error }
  // A finished test says nothing about a changed form, so it goes once anything changes.
  const set = (changes) => { setTest((current) => (current && (current.done || current.expired || current.error) ? null : current)); setFormFields(changes) }
  const setFormFields = (changes) => setForm((current) => {
    const next = { ...current, ...changes }
    // A shorter interval takes the timeout down with it, so the default 48 s doesn't block a 30 s interval.
    if ('interval_seconds' in changes && /^\d+$/.test(changes.interval_seconds) && Number(next.timeout_seconds) >= Number(changes.interval_seconds)) {
      next.timeout_seconds = String(Math.max(1, Math.min(48, Math.floor(Number(changes.interval_seconds) * 0.8))))
    }
    return next
  })
  const web = form.type !== 'tcp'
  const issues = problems(form, web)
  const shown = (key) => (tried || (key === 'target' ? form.target.trim() : true)) && issues[key]
  const hint = (key) => shown(key) && <span className="mt-1.5 block text-[11px] leading-4 text-warn">{issues[key]}</span>
  const toggleServer = (id, on) => set({ server_ids: on ? [...form.server_ids, id] : form.server_ids.filter((item) => item !== id) })

  const bodyOf = () => {
    const whole = (value) => (/^\d+$/.test(String(value).trim()) ? Number(value) : value)
    return {
      type: form.type, name: form.name.trim(), target: form.target.trim(), tags: form.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
      server_ids: form.server_ids, rule: form.rule, severity: form.severity, upside_down: form.upside_down,
      interval_seconds: whole(form.interval_seconds), retries: whole(form.retries), retry_interval_seconds: whole(form.retry_interval_seconds), timeout_seconds: whole(form.timeout_seconds),
      ...(web ? { accepted_codes: form.accepted_codes.split(',').map((code) => code.trim()).filter(Boolean), follow_redirects: form.follow_redirects, ignore_tls: form.ignore_tls } : {}),
      ...(form.type === 'keyword' ? { keyword: form.keyword, case_sensitive: form.case_sensitive } : {}),
    }
  }

  // Test: the check runs once from its servers; the answer is asked for every 1.5 s until all servers replied.
  const polling = useRef(null)
  useEffect(() => () => clearTimeout(polling.current), [])
  const runTest = async () => {
    setTried(true)
    if (Object.keys(issues).length) return
    clearTimeout(polling.current)
    setTest({ running: true })
    try {
      const started = Date.now()
      let current = await apiRequest('/availability/test', { method: 'POST', body: JSON.stringify(bodyOf()) })
      setTest(current)
      const poll = async () => {
        if (current.done) return
        if (Date.now() - started > TEST_WAIT_MS) { setTest({ ...current, expired: true }); return }
        try { current = await apiRequest(`/availability/test/${current.id}`, { method: 'GET' }); setTest(current) } catch (error) { setTest({ error: error.message }); return }
        polling.current = setTimeout(poll, 1500)
      }
      polling.current = setTimeout(poll, 1500)
    } catch (error) {
      setTest({ error: error.message || 'The test could not start.' })
    }
  }
  const testLine = test && (test.running ? { tone: 'muted', text: 'Starting the test…' } : test.error ? { tone: 'warn', text: test.error } : describeTest(test))
  const testing = Boolean(test && (test.running || (test.servers && !test.done && !test.expired)))

  const save = (event) => {
    event.preventDefault()
    setTried(true)
    if (Object.keys(issues).length) return
    const body = bodyOf()
    run(async () => {
      const result = await apiRequest(check ? `/availability/checks/${check.id}` : '/availability/checks', { method: check ? 'PATCH' : 'POST', body: JSON.stringify(body) })
      onSaved(result)
    })
  }

  const number = (key, label, unit) => (
    <Field label={label}>
      <span className="relative block"><input inputMode="numeric" value={form[key]} onChange={(event) => set({ [key]: event.target.value })} aria-invalid={Boolean(shown(key))} className={`field h-9 pr-7 text-[13px] tabular-nums ${shown(key) ? '!border-warn' : ''}`} />
        {unit && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted">{unit}</span>}</span>
      {hint(key)}
    </Field>
  )

  return (
    <form onSubmit={save} className="space-y-5">
      <Heading>Type</Heading>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Check type">
        {TYPES.map(([id, label, detail]) => {
          const ready = READY.has(id)
          const chosen = form.type === id
          return (
            <button key={id} type="button" role="radio" aria-checked={chosen} disabled={!ready || Boolean(check)} title={ready ? undefined : 'Coming in a later step'} onClick={() => set({ type: id })}
              className={`rounded-md border px-3 py-2.5 text-left transition-colors ${chosen ? 'border-paper bg-raised' : 'border-edge bg-ink hover:bg-raised'} disabled:cursor-not-allowed ${ready ? '' : 'opacity-40'}`}>
              <span className="block text-[13px] font-medium text-paper">{label}</span><span className="mt-0.5 block text-[11px] text-muted">{ready ? detail : 'later'}</span>
            </button>
          )
        })}
      </div>

      <Heading>Target</Heading>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name"><input value={form.name} onChange={(event) => set({ name: event.target.value })} placeholder="Home Assistant" maxLength={120} className="field h-9 text-[13px]" required /></Field>
        <Field label="Tags" hint="Optional, comma-separated. The list is grouped by the first one."><input value={form.tags} onChange={(event) => set({ tags: event.target.value })} placeholder="Home services" className="field h-9 text-[13px]" /></Field>
      </div>
      {web ? <Field label="URL"><input value={form.target} onChange={(event) => set({ target: event.target.value })} placeholder="https://ha.home.lan/api/" aria-invalid={Boolean(shown('target'))} className={`field mono h-9 text-[13px] ${shown('target') ? '!border-warn' : ''}`} required />{hint('target')}</Field>
        : <Field label="Host and port" hint={shown('target') ? null : "Seen from the servers that run the check: localhost is the collector's own container."}><input value={form.target} onChange={(event) => set({ target: event.target.value })} placeholder="10.0.0.12:3306" aria-invalid={Boolean(shown('target'))} className={`field mono h-9 text-[13px] ${shown('target') ? '!border-warn' : ''}`} required />{hint('target')}</Field>}
      {form.type === 'keyword' && <div className="grid items-end gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
        <Field label="Text to look for" hint="In the first 1 MiB of the page. The page itself is never stored or sent anywhere."><input value={form.keyword} onChange={(event) => set({ keyword: event.target.value })} maxLength={200} className="field h-9 text-[13px]" required /></Field>
        <label className="flex items-center gap-2 pb-6 text-xs text-soft"><Check checked={form.case_sensitive} onChange={(value) => set({ case_sensitive: value })} label="Case sensitive" />Case sensitive</label>
      </div>}

      <Heading detail="any server with a collector">Check from</Heading>
      <div className="flex flex-wrap gap-2">
        {servers.map((server) => {
          const on = form.server_ids.includes(server.id)
          return (
            <label key={server.id} className={`flex h-9 cursor-pointer items-center gap-2.5 rounded-md border px-3 text-[13px] ${on ? 'border-paper bg-raised text-paper' : 'border-edge text-soft'}`} title={server.can_run_checks ? undefined : 'Its collector is older than 0.10.0 or hasn’t reported yet, so it can’t run checks until it is updated.'}>
              <Check checked={on} onChange={(value) => toggleServer(server.id, value)} label={server.hostname} />{server.hostname}{!server.can_run_checks && <span className="text-[11px] text-warn">update collector</span>}
            </label>
          )
        })}
      </div>
      {hint('server_ids')}
      {form.server_ids.length > 1 && <span className="seg" role="group" aria-label="When is it down">
        <button type="button" aria-pressed={form.rule === 'all'} onClick={() => set({ rule: 'all' })}>Down when all fail</button>
        <button type="button" aria-pressed={form.rule === 'any'} onClick={() => set({ rule: 'any' })}>Down when any fails</button>
      </span>}

      <Heading>Timing</Heading>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {number('interval_seconds', 'Every', 's')}{number('retries', 'Retries')}{number('retry_interval_seconds', 'Retry every', 's')}{number('timeout_seconds', 'Timeout', 's')}
      </div>
      <TimingSentence form={form} />

      <Heading>What counts as up</Heading>
      {web && <Field label="Accepted status codes" hint="Comma-separated codes or ranges, like 200-299, 301."><input value={form.accepted_codes} onChange={(event) => set({ accepted_codes: event.target.value })} className={`field mono h-9 text-[13px] ${shown('accepted_codes') ? '!border-warn' : ''}`} />{hint('accepted_codes')}</Field>}
      <div className="grid gap-4 sm:grid-cols-2">
        {web && <Toggle checked={form.follow_redirects} onChange={(value) => set({ follow_redirects: value })} title="Follow redirects" detail="up to 10, only to http and https" />}
        {web && <Toggle checked={form.ignore_tls} onChange={(value) => set({ ignore_tls: value })} title="Ignore TLS errors" detail="for self-signed certificates" />}
        <Toggle checked={form.upside_down} onChange={(value) => set({ upside_down: value })} title="Upside down" detail="up means the service is unreachable" />
      </div>
      {web && <p className="m-0 text-xs text-muted">Request: GET · no headers · no body · no auth</p>}

      <Heading detail="channels from Account › Notifications">Notify</Heading>
      <p className="m-0 text-xs leading-5 text-soft">A check that goes down opens an incident, which goes to the same channels as every other incident (<Link to="/account#notifications" className="link">your notification channels</Link>). New checks stay quiet for their first 10 minutes.</p>
      <Field label="Severity">
        <span className="seg" role="group" aria-label="Severity">
          <button type="button" aria-pressed={form.severity === 'CRITICAL'} onClick={() => set({ severity: 'CRITICAL' })}>Critical</button>
          <button type="button" aria-pressed={form.severity === 'WARNING'} onClick={() => set({ severity: 'WARNING' })}>Warning</button>
        </span>
      </Field>

      <div className="sticky -bottom-5 -mx-6 -mb-5 flex items-center justify-end gap-2 border-t border-line bg-bg px-6 py-3">
        {state.error || (tried && Object.keys(issues).length) || !testLine
          ? <p role="alert" className="m-0 mr-auto min-w-0 text-xs leading-4 text-warn">{state.error || (tried && Object.keys(issues).length ? `Fix ${Object.keys(issues).length === 1 ? 'the field' : `${Object.keys(issues).length} fields`} marked above.` : '')}</p>
          : <p role="status" className={`m-0 mr-auto flex min-w-0 items-center gap-2 text-xs leading-4 ${testLine.tone === 'ok' ? 'text-ok' : testLine.tone === 'warn' ? 'text-warn' : 'text-soft'}`}>
              <span className={`dot shrink-0 ${testLine.tone === 'ok' ? 'dot-ok' : testLine.tone === 'warn' ? 'dot-warn' : ''}`} /><span className="min-w-0">{testLine.text}</span></p>}
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="button" className="btn" onClick={runTest} disabled={testing}>{testing ? 'Testing…' : test ? 'Test again' : 'Test'}</button>
        <button type="submit" className="btn btn-primary" disabled={state.busy}>{state.busy ? 'Saving…' : 'Save check'}</button>
      </div>
    </form>
  )
}
