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
const READY = new Set(['http', 'keyword', 'json', 'tcp', 'tls', 'dns', 'ping'])
const URL_TYPES = new Set(['http', 'keyword', 'json'])       // check an http(s) URL
const HOSTPORT_TYPES = new Set(['tcp', 'tls'])               // connect to host:port
const HOST_TYPES = new Set(['ping', 'dns'])                  // a bare host or name, no port
const DNS_RECORDS = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS']
const JSON_OPS = [['equals', 'equals'], ['contains', 'contains'], ['gt', 'is greater than'], ['lt', 'is less than'], ['exists', 'exists']]
const IP = /^(\d{1,3}\.){3}\d{1,3}$|^\[?[0-9A-Fa-f:]+\]?$/
const HOSTPORT = /^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?$/
const BARE_HOST = /^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)$/

const initial = (check, servers) => {
  const options = check?.options || {}
  const first = servers.find((server) => server.can_run_checks) || servers[0]
  return {
    type: check?.type || 'http', name: check?.name || '', tags: (check?.tags || []).join(', '), target: check?.target || '',
    keyword: options.keyword || '', case_sensitive: Boolean(options.case_sensitive),
    json_path: options.json_path || '', json_op: options.json_op || 'equals', json_expect: options.json_expect || '',
    warn_days: String(options.warn_days ?? 14),
    record_type: options.record_type || 'A', resolver: options.resolver || '', expected: options.expected || '',
    server_ids: check ? check.servers.map((server) => server.id) : first ? [first.id] : [], rule: check?.rule || 'all',
    interval_seconds: String(check?.interval_seconds ?? 60), retries: String(check?.retries ?? 2), retry_interval_seconds: String(check?.retry_interval_seconds ?? 20), timeout_seconds: String(check?.timeout_seconds ?? 48),
    accepted_codes: (options.accepted_codes || ['200-299']).join(', '), follow_redirects: options.follow_redirects ?? true, ignore_tls: Boolean(options.ignore_tls), upside_down: Boolean(options.upside_down),
    severity: check?.severity || 'CRITICAL',
  }
}

// What the backend would refuse, said next to the field before saving.
function problems(form) {
  const found = {}
  const target = form.target.trim()
  const url = URL_TYPES.has(form.type), hostPort = HOSTPORT_TYPES.has(form.type), hostOnly = HOST_TYPES.has(form.type)
  if (url && target && !/^https?:\/\/[^\s/:?#]+/i.test(target)) found.target = /^[\w.-]+(:\d+)?(\/|$)/.test(target) ? `Add http:// or https:// in front, like http://${target}` : 'The URL must start with http:// or https://.'
  if (hostPort && target) {
    const hasPort = /:\d{1,5}$/.test(target.replace(/^\[[0-9A-Fa-f:.]+\]/, ''))
    if (!HOSTPORT.test(target)) found.target = 'Enter host:port, like 10.0.0.12:3306.'
    else if (form.type === 'tcp' && !hasPort) found.target = /^[A-Za-z0-9.-]+$/.test(target) ? `Add the port, like ${target}:22` : 'Enter host:port, like 10.0.0.12:3306.'
  }
  if (hostOnly && target && !BARE_HOST.test(target)) found.target = form.type === 'ping' ? 'Enter a host name or IP, like 10.0.0.1.' : 'Enter the name to look up, like deck.home.lan.'
  if (form.type === 'keyword' && !form.keyword) found.keyword = 'Enter the text to look for.'
  if (form.type === 'json') {
    if (!form.json_path.trim()) found.json_path = 'Enter the path, like data.status.'
    if (form.json_op !== 'exists' && !form.json_expect) found.json_expect = 'Enter the value to compare.'
  }
  if (form.type === 'dns' && form.resolver.trim() && !IP.test(form.resolver.trim())) found.resolver = 'The DNS server must be an IP address.'
  if (form.type === 'tls' && !/^\d+$/.test(form.warn_days.trim())) found.warn_days = 'A whole number of days.'
  const whole = (value, low, high) => /^\d+$/.test(String(value).trim()) && Number(value) >= low && Number(value) <= high
  if (!whole(form.interval_seconds, 20, 86400)) found.interval_seconds = 'From 20 s to 24 h.'
  if (!whole(form.retries, 0, 10)) found.retries = 'From 0 to 10.'
  if (!whole(form.retry_interval_seconds, 20, 86400)) found.retry_interval_seconds = 'At least 20 s.'
  if (!whole(form.timeout_seconds, 1, 48)) found.timeout_seconds = 'From 1 to 48 s.'
  else if (whole(form.interval_seconds, 20, 86400) && Number(form.timeout_seconds) >= Number(form.interval_seconds)) found.timeout_seconds = 'Shorter than the interval.'
  if (url && !form.accepted_codes.split(',').map((code) => code.trim()).filter(Boolean).every((code) => /^[1-5]\d\d(-[1-5]\d\d)?$/.test(code))) found.accepted_codes = 'Codes or ranges like 200-299, 301.'
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
  const url = URL_TYPES.has(form.type), hostPort = HOSTPORT_TYPES.has(form.type)
  const issues = problems(form)
  // An empty required field only warns once you've tried to save/test; a filled-but-wrong one warns live.
  const shown = (key) => (tried || String(form[key] ?? '').trim()) && issues[key]
  const hint = (key) => shown(key) && <span className="mt-1.5 block text-[11px] leading-4 text-warn">{issues[key]}</span>
  const toggleServer = (id, on) => set({ server_ids: on ? [...form.server_ids, id] : form.server_ids.filter((item) => item !== id) })

  const bodyOf = () => {
    const whole = (value) => (/^\d+$/.test(String(value).trim()) ? Number(value) : value)
    return {
      type: form.type, name: form.name.trim(), target: form.target.trim(), tags: form.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
      server_ids: form.server_ids, rule: form.rule, severity: form.severity, upside_down: form.upside_down,
      interval_seconds: whole(form.interval_seconds), retries: whole(form.retries), retry_interval_seconds: whole(form.retry_interval_seconds), timeout_seconds: whole(form.timeout_seconds),
      ...(url ? { accepted_codes: form.accepted_codes.split(',').map((code) => code.trim()).filter(Boolean), follow_redirects: form.follow_redirects, ignore_tls: form.ignore_tls } : {}),
      ...(form.type === 'keyword' ? { keyword: form.keyword, case_sensitive: form.case_sensitive } : {}),
      ...(form.type === 'json' ? { json_path: form.json_path.trim(), json_op: form.json_op, json_expect: form.json_expect } : {}),
      ...(form.type === 'tls' ? { warn_days: whole(form.warn_days) } : {}),
      ...(form.type === 'dns' ? { record_type: form.record_type, resolver: form.resolver.trim(), expected: form.expected } : {}),
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
  const textField = (key, label, placeholder, mono = false) => (
    <Field label={label}>
      <input value={form[key]} onChange={(event) => set({ [key]: event.target.value })} placeholder={placeholder} aria-invalid={Boolean(shown(key))} className={`field h-9 text-[13px] ${mono ? 'mono' : ''} ${shown(key) ? '!border-warn' : ''}`} />
      {hint(key)}
    </Field>
  )
  const targetField = () => {
    if (url) return textField('target', form.type === 'json' ? 'URL of the JSON' : 'URL', form.type === 'json' ? 'https://stock.home.lan/api/' : 'https://ha.home.lan/api/', true)
    if (hostPort) return (
      <Field label="Host and port" hint={shown('target') ? null : (form.type === 'tls' ? 'The port defaults to 443. Checked from the servers that run it.' : "Seen from the servers that run the check: localhost is the collector's own container.")}>
        <input value={form.target} onChange={(event) => set({ target: event.target.value })} placeholder={form.type === 'tls' ? 'mail.example.com:993' : '10.0.0.12:3306'} aria-invalid={Boolean(shown('target'))} className={`field mono h-9 text-[13px] ${shown('target') ? '!border-warn' : ''}`} required />
        {hint('target')}
      </Field>
    )
    return (
      <Field label={form.type === 'ping' ? 'Host' : 'Name to look up'} hint={shown('target') ? null : (form.type === 'ping' ? 'A host name or IP. Checked from the servers you pick.' : 'The name whose DNS record is checked.')}>
        <input value={form.target} onChange={(event) => set({ target: event.target.value })} placeholder={form.type === 'ping' ? '10.0.0.1' : 'deck.home.lan'} aria-invalid={Boolean(shown('target'))} className={`field mono h-9 text-[13px] ${shown('target') ? '!border-warn' : ''}`} required />
        {hint('target')}
      </Field>
    )
  }

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
      {targetField()}
      {form.type === 'keyword' && <div className="grid items-end gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
        <Field label="Text to look for" hint="In the first 1 MiB of the page. The page itself is never stored or sent anywhere."><input value={form.keyword} onChange={(event) => set({ keyword: event.target.value })} maxLength={200} className="field h-9 text-[13px]" required /></Field>
        <label className="flex items-center gap-2 pb-6 text-xs text-soft"><Check checked={form.case_sensitive} onChange={(value) => set({ case_sensitive: value })} label="Case sensitive" />Case sensitive</label>
      </div>}
      {form.type === 'json' && <div className="grid gap-4 sm:grid-cols-[minmax(0,1.3fr)_auto_minmax(0,1fr)]">
        {textField('json_path', 'Path to the value', 'data.status', true)}
        <Field label="Comparison">
          <select value={form.json_op} onChange={(event) => set({ json_op: event.target.value })} className="select h-9 w-full">{JSON_OPS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
        </Field>
        {form.type === 'json' && form.json_op !== 'exists' ? textField('json_expect', 'Expected value', 'ok') : <Field label="&nbsp;"><p className="m-0 flex h-9 items-center text-xs text-muted">just has to exist</p></Field>}
      </div>}
      {form.type === 'dns' && <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Record type"><select value={form.record_type} onChange={(event) => set({ record_type: event.target.value })} className="select h-9 w-full">{DNS_RECORDS.map((record) => <option key={record} value={record}>{record}</option>)}</select></Field>
        {textField('resolver', 'DNS server', 'system default', true)}
        {textField('expected', 'Expected answer', 'optional')}
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
      {url && <Field label="Accepted status codes" hint="Comma-separated codes or ranges, like 200-299, 301."><input value={form.accepted_codes} onChange={(event) => set({ accepted_codes: event.target.value })} className={`field mono h-9 text-[13px] ${shown('accepted_codes') ? '!border-warn' : ''}`} />{hint('accepted_codes')}</Field>}
      {form.type === 'tls' && number('warn_days', 'Warn this many days before the certificate expires', 'd')}
      <div className="grid gap-4 sm:grid-cols-2">
        {url && <Toggle checked={form.follow_redirects} onChange={(value) => set({ follow_redirects: value })} title="Follow redirects" detail="up to 10, only to http and https" />}
        {url && <Toggle checked={form.ignore_tls} onChange={(value) => set({ ignore_tls: value })} title="Ignore TLS errors" detail="for self-signed certificates" />}
        <Toggle checked={form.upside_down} onChange={(value) => set({ upside_down: value })} title="Upside down" detail="up means the service is unreachable" />
      </div>
      {url && <p className="m-0 text-xs text-muted">Request: GET · no headers · no body · no auth</p>}
      {form.type === 'tls' && <p className="m-0 text-xs text-muted">The certificate chain must be valid. Self-signed certificates need a TCP or HTTP check with “Ignore TLS errors” instead.</p>}
      {form.type === 'ping' && <p className="m-0 text-xs text-muted">One ICMP echo per check, over an unprivileged socket. No shell, no raw sockets.</p>}

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
