// The first run: an empty rack and the shortest way to fill it. Also the Connect server panel a running fleet opens.
import { useEffect, useState } from 'react'
import { API_BASE, apiRequest } from '../api.js'
import { Icon } from '../icons.jsx'
import { FLEET_COLUMNS, FleetHeader } from './FleetRack.jsx'
import { SectionHeading } from './NeedsAttention.jsx'
import { installCommands } from './logic.js'

const DOCS_URL = 'https://github.com/karloks2005/Infradeck#readme'

function useCopy() {
  const [copied, setCopied] = useState(false)
  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { setCopied(false) }
  }
  return [copied, copy]
}

function Notice({ children, tone = 'warn' }) {
  return <div role="alert" className={`rounded-md border px-3.5 py-2.5 text-xs ${tone === 'warn' ? 'border-warn/40 text-warn' : 'border-line text-soft'}`}>{children}</div>
}

function Found({ ok, label, value, note }) {
  return <li className="flex items-center gap-2.5 text-xs"><span className={`dot ${ok ? 'dot-ok' : ''}`} style={{ width: 6, height: 6 }} /><span className="text-muted">{label}</span><span className={`font-medium ${ok ? 'text-paper' : 'text-muted'}`}>{value}</span>{note && <span className="text-muted">{note}</span>}</li>
}

// The machine Infradeck itself runs on: its collector ships with the stack and is already asking to join.
export function ThisMachineCard({ pending, onChanged, recommended = true }) {
  const local = pending.find((item) => item.status === 'pending' && item.details?.dashboard_host) || null
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const details = local?.details || {}
  const chosen = name.trim() || local?.hostname || ''

  const start = async () => {
    if (local.replaces && !window.confirm(`This machine matches the existing server "${local.replaces.hostname}". Adding it replaces that server's credentials and signs its current collector out. Only continue if you are re-installing that machine.`)) return
    setBusy(true); setError('')
    try {
      await apiRequest(`/monitoring/pending-collectors/${local.id}/approve`, { method: 'POST', body: JSON.stringify({ replace: Boolean(local.replaces), hostname: chosen }) })
      await onChanged()
    } catch (requestError) { setError(requestError.message || 'Could not start monitoring this machine.') } finally { setBusy(false) }
  }

  return (
    <div className={`card flex flex-col px-[18px] pb-[18px] pt-4 ${recommended ? 'border-edge' : ''}`}>
      <div className="flex items-center gap-2.5 text-[11px] text-muted">{recommended && <span className="chip text-paper">Recommended</span>}1 click</div>
      <h3 className="m-0 mt-2.5 text-[15px] font-semibold text-paper">Monitor this machine</h3>
      <p className="mb-0 mt-1.5 text-[13px] text-soft">The collector already ships with Infradeck's compose file. Turning it on starts reporting within 15 seconds.</p>
      {local ? <>
        <ul className="m-0 mt-3.5 grid list-none gap-x-6 gap-y-2 rounded-md border border-line bg-bg px-3.5 py-3 sm:grid-cols-2">
          <Found ok label="Host" value={local.hostname} />
          <Found ok={Boolean(local.os_info)} label="" value={local.os_info || 'Unknown system'} note={details.cpu_cores ? `· ${details.cpu_cores} cores` : ''} />
          <Found ok={details.containers != null} label="Docker socket" value={details.containers != null ? 'readable' : 'not reachable'} />
          <Found ok={details.containers > 0} label="" value={details.containers != null ? `${details.containers} containers` : 'No containers'} note={details.containers != null ? 'found' : ''} />
          <Found ok={Boolean(details.helper_installed)} label="System helper" value={details.helper_installed ? 'installed' : 'not installed'} />
          <Found ok={details.mounts > 0} label="Disk" value={details.mounts != null ? `${details.mounts} ${details.mounts === 1 ? 'mount' : 'mounts'}` : 'unknown'} />
        </ul>
        <label className="mt-3.5 block text-xs text-soft">Name
          <input className="field mt-1.5 h-[34px]" value={name} placeholder={local.hostname} maxLength={253} onChange={(event) => setName(event.target.value)} />
        </label>
        {error && <div className="mt-3"><Notice>{error}</Notice></div>}
        <div className="mt-3.5 flex flex-wrap items-center gap-3">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={start}><Icon name="check" size={14} />{busy ? 'Starting…' : `Start monitoring ${chosen}`}</button>
          <span className="text-xs text-muted">Checks every minute at first · change it once it reports</span>
        </div>
      </> : <div className="mt-3.5 flex items-center gap-3 rounded-md border border-line bg-bg px-3.5 py-3 text-xs text-muted"><span className="spinner shrink-0" />Waiting for this machine's collector. It starts with Infradeck's own compose stack and shows up here within a few seconds; if it doesn't, check that the <span className="mono text-soft">collector</span> service is running.</div>}
    </div>
  )
}

function Waiting({ pending, onChanged }) {
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState('')
  const waiting = pending.filter((item) => item.status === 'pending' && !item.details?.dashboard_host)
  const dismissed = pending.filter((item) => item.status === 'dismissed')

  const act = async (item, action) => {
    // Approving a machine that matches an existing server replaces that server's credentials and signs out its collector.
    if (action === 'approve' && item.replaces && !window.confirm(`This machine matches the existing server "${item.replaces.hostname}". Adding it replaces that server's credentials and signs its current collector out. Only continue if you are re-installing that machine.`)) return
    setBusyId(item.id); setError('')
    try {
      await apiRequest(`/monitoring/pending-collectors/${item.id}/${action}`, { method: 'POST', body: JSON.stringify({ replace: Boolean(item.replaces) }) })
      await onChanged()
    } catch (requestError) { setError(requestError.message || 'Could not update the server.') } finally { setBusyId(null) }
  }

  const row = (item) => (
    <li key={item.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-line bg-bg px-3.5 py-2.5">
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2"><span className="text-[13px] font-medium text-paper">{item.hostname}</span><span className={`chip ${item.verified ? 'chip-ok' : 'chip-warn'}`}>{item.verified ? 'join token' : 'no token'}</span></span>
        <span className="mt-0.5 block text-[11px] text-muted">{[item.ip_address, item.os_info, item.details?.containers != null ? `${item.details.containers} containers` : null].filter(Boolean).join(' · ')} · code <span className="mono text-soft">{item.pairing_code}</span></span>
        {item.replaces && <span className="mt-0.5 block text-[11px] text-warn">Replaces the existing server “{item.replaces.hostname}”: its current collector will be signed out.</span>}
      </span>
      <button type="button" className="btn btn-sm btn-primary" disabled={busyId === item.id} onClick={() => act(item, 'approve')}>{busyId === item.id ? 'Adding…' : 'Add'}</button>
      {item.status === 'pending' && <button type="button" className="btn btn-sm" disabled={busyId === item.id} onClick={() => act(item, 'dismiss')}>Dismiss</button>}
    </li>
  )

  if (!waiting.length && !dismissed.length) return null
  return (
    <div className="mt-3.5">
      {waiting.length > 0 && <>
        <div className="mb-2 text-xs text-soft">Asking to join. The pairing code is also printed in that collector's log.</div>
        <ul className="m-0 list-none space-y-2 p-0">{waiting.map(row)}</ul>
      </>}
      {dismissed.length > 0 && <details className="mt-2.5"><summary className="cursor-pointer text-[11px] text-muted">Dismissed ({dismissed.length})</summary><ul className="m-0 mt-2 list-none space-y-2 p-0">{dismissed.map(row)}</ul></details>}
      {error && <div className="mt-2.5"><Notice>{error}</Notice></div>}
    </div>
  )
}

function EnrollmentKey() {
  const [keyInfo, setKeyInfo] = useState(null)
  const [enrollKey, setEnrollKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmRevoke, setConfirmRevoke] = useState(false)
  const [error, setError] = useState('')
  const [copied, copy] = useCopy()

  useEffect(() => {
    apiRequest('/monitoring/enrollment-key', { method: 'GET' }).then(setKeyInfo).catch((requestError) => setError(requestError.message || 'Could not load the enrollment key.'))
  }, [])

  const generate = async () => {
    setBusy(true); setError('')
    try {
      const data = await apiRequest('/monitoring/enrollment-key', { method: 'POST' })
      setEnrollKey(data.enroll_key); setKeyInfo({ exists: true, created_at: data.created_at })
    } catch (requestError) { setError(requestError.message || 'Could not create an enrollment key.') } finally { setBusy(false) }
  }
  const revoke = async () => {
    setBusy(true); setError('')
    try {
      await apiRequest('/monitoring/enrollment-key', { method: 'DELETE' })
      setEnrollKey(''); setKeyInfo({ exists: false, created_at: null }); setConfirmRevoke(false)
    } catch (requestError) { setError(requestError.message || 'Could not revoke the enrollment key.') } finally { setBusy(false) }
  }

  return (
    <div className="mt-2.5 text-xs">
      <p className="m-0 text-muted">For scripted installs where nobody clicks Add: collectors started with <span className="mono text-soft">INFRADECK_ENROLL_KEY</span> join immediately. Servers already connected are unaffected by rotating or revoking it.</p>
      {error && <div className="mt-2.5"><Notice>{error}</Notice></div>}
      {!keyInfo ? <p className="mb-0 mt-2.5 text-muted">Loading…</p> : enrollKey ? <>
        <p className="mb-0 mt-2.5 text-warn">Copy this key now. It is shown only once.</p>
        <div className="mt-1.5 flex items-center gap-3 rounded-md border border-line bg-bg py-1.5 pl-3.5 pr-1.5"><code className="mono min-w-0 flex-1 truncate text-paper">{enrollKey}</code><button type="button" className="btn btn-sm" onClick={() => copy(enrollKey)}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}</button></div>
      </> : keyInfo.exists && <p className="mb-0 mt-2.5 text-soft">A key was created {new Date(keyInfo.created_at).toLocaleString()}. Rotate it to see a new one.</p>}
      {keyInfo && <div className="mt-2.5 flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={generate}>{keyInfo.exists ? 'Rotate key' : 'Generate key'}</button>
        {keyInfo.exists && (confirmRevoke ? <>
          <button type="button" className="btn btn-sm border-signal/50 text-signal" disabled={busy} onClick={revoke}>Yes, revoke</button>
          <button type="button" className="btn btn-sm" onClick={() => setConfirmRevoke(false)}>Cancel</button>
        </> : <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setConfirmRevoke(true)}>Revoke key</button>)}
      </div>}
    </div>
  )
}

// Any other machine: one command, three ways to run it, and a live wait for its first check-in.
export function AnotherMachineCard({ pending, onChanged, focusRef }) {
  const [info, setInfo] = useState(null)
  const [apiUrl, setApiUrl] = useState('')
  const [name, setName] = useState('')
  const [token, setToken] = useState(null)
  const [expiresAt, setExpiresAt] = useState(null)
  const [method, setMethod] = useState('docker run')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, copy] = useCopy()

  useEffect(() => {
    apiRequest('/monitoring/install-info', { method: 'GET' })
      .then((data) => {
        setInfo(data)
        // API_BASE is usually relative ('/api'); a collector needs an absolute address. The VPN address wins when one is configured.
        setApiUrl(data.collector_url || new URL(API_BASE, window.location.origin).href.replace(/\/$/, ''))
      })
      .catch((requestError) => setError(requestError.message || 'Could not load the install settings.'))
  }, [])

  const generate = async () => {
    setBusy(true); setError('')
    try {
      const data = await apiRequest('/monitoring/join-tokens', { method: 'POST' })
      setToken(data.join_token); setExpiresAt(Date.now() + data.expires_in_seconds * 1000)
    } catch (requestError) { setError(requestError.message || 'Could not create a join token.') } finally { setBusy(false) }
  }

  const url = apiUrl.trim().replace(/\/$/, '')
  const isLocalhost = /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(url)
  const ready = Boolean(info?.collector_image && info?.proxy_image)
  const cleanName = name.trim().replace(/[^A-Za-z0-9.-]/g, '')
  const commands = installCommands({ url: url || '<address>', token: token || '<generated when you click the button>', name: cleanName, collectorImage: info?.collector_image || '<collector image>', proxyImage: info?.proxy_image || '<docker proxy image>' })
  const minutes = expiresAt ? Math.max(1, Math.round((expiresAt - Date.now()) / 60000)) : null
  const command = commands[method]

  return (
    <div className="card flex flex-col px-[18px] pb-[18px] pt-4">
      <div className="text-[11px] text-muted">About 2 minutes</div>
      <h3 className="m-0 mt-2.5 text-[15px] font-semibold text-paper">Another machine</h3>
      <p className="mb-0 mt-1.5 text-[13px] text-soft">Run one command on any Linux machine with Docker. It connects back to this Infradeck and shows up here to be added.</p>

      <div className="mt-3.5 grid gap-3 @container sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <label className="block text-xs text-soft">Name
          <input ref={focusRef} className="field mt-1.5 h-[34px]" value={name} placeholder="e.g. homelab-01" maxLength={253} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="block text-xs text-soft">Address it will use to reach Infradeck
          <input className="field mono mt-1.5 h-[34px]" value={apiUrl} onChange={(event) => { setApiUrl(event.target.value); setToken(null) }} aria-describedby="api-url-hint" />
        </label>
      </div>
      <p id="api-url-hint" className={`mb-0 mt-1.5 text-[11px] ${info?.strict && !info?.collector_url ? 'text-warn' : isLocalhost ? 'text-warn' : 'text-muted'}`}>
        {info?.collector_url ? 'Your configured collector (VPN) address.'
          : info?.strict ? <>Collectors can't use the public address. Set <span className="mono">COLLECTOR_PUBLIC_URL</span> to the collector listener on your VPN (for example <span className="mono">http://10.8.0.1:8081/api</span>).</>
            : isLocalhost ? <>Other machines can't reach <span className="mono">localhost</span>. Use this machine's LAN IP, VPN address or domain.</>
              : 'Prefilled with the address you opened this page at. Over plain HTTP, use it only on a trusted LAN or VPN.'}
      </p>

      {info && !ready && <div className="mt-3"><Notice>No collector images are configured, so there is nothing safe to install from. Set <span className="mono">COLLECTOR_IMAGE</span> and <span className="mono">DOCKER_PROXY_IMAGE</span> in <span className="mono">.env</span> to your published, digest-pinned images (see SECURITY.md).</Notice></div>}
      {error && <div className="mt-3"><Notice>{error}</Notice></div>}

      <div className="relative mt-3.5 rounded-md border border-line bg-bg">
        <pre className="custom-scroll mono m-0 max-h-[168px] overflow-auto px-3.5 py-3 pr-24 leading-[19px] text-soft">{token ? command.split(token).flatMap((part, index) => (index ? [<span key={index} className="text-signal">{token}</span>, part] : [part])) : command}</pre>
        {token ? <button type="button" className="btn btn-sm absolute right-2.5 top-2.5" onClick={() => copy(command)}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}</button>
          : <div className="absolute inset-0 flex items-center justify-center rounded-md bg-bg/85"><button type="button" className="btn btn-primary" disabled={busy || !ready || !url} onClick={generate}>{busy ? 'Generating…' : 'Generate command'}</button></div>}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2.5">
        <div className="seg" role="group" aria-label="How to run it">{Object.keys(commands).map((key) => <button key={key} type="button" aria-pressed={method === key} onClick={() => setMethod(key)}>{key}</button>)}</div>
        {token && <button type="button" className="link text-xs" disabled={busy} onClick={generate}>New token</button>}
        <span className="ml-auto flex items-center gap-2.5 text-xs text-muted">{token ? <><span className="spinner" />Waiting for a check-in…</> : 'The token is single use and expires.'}</span>
      </div>
      {token && <p className="mb-0 mt-2 text-[11px] text-muted">Single use · expires in about {minutes} min. Both containers run with no capabilities; only the read-only proxy touches the Docker socket.</p>}

      <Waiting pending={pending} onChanged={onChanged} />

      <details className="mt-3.5 border-t border-line pt-3"><summary className="cursor-pointer text-xs text-muted">Advanced: skip approval with an enrollment key</summary><EnrollmentKey /></details>
    </div>
  )
}

// An empty rack with dashed slots and one orange drawer sliding in.
function EmptyRack({ name }) {
  return (
    <svg viewBox="0 0 360 252" className="h-[252px] w-[360px] max-w-full" role="img" aria-label="An empty rack with one server sliding in">
      <path d="M1 0v252M359 0v252" stroke="var(--color-rail)" strokeWidth="2" />
      {[24, 124, 167, 210].map((y) => <rect key={y} x="24.5" y={y + 0.5} width="311" height="33" rx="2" fill="none" stroke="var(--color-edge)" strokeDasharray="3 3" />)}
      <rect x="58" y="66" width="278" height="34" rx="2" fill="var(--color-signal)" />
      <path d="M76 83h10M81 78v10" stroke="var(--color-bg)" strokeWidth="1.8" />
      <text x="96" y="88" fill="var(--color-bg)" fontSize="13.5" fontWeight="600" fontFamily="var(--font-sans)">{name}</text>
      <circle cx="320" cy="83" r="2.4" fill="var(--color-bg)" />
    </svg>
  )
}

const PROMISES = [
  ['Needs attention', 'Problems float to the top as pulled-out drawers, so the page opens on what needs you.'],
  ['Live health', 'CPU, memory, load and disks at every check-in, plus checks for firewall, updates, clock and OOM kills.'],
  ['Containers and ports', 'Every container grouped by compose project, and which ports are reachable from other machines.'],
  ['History', 'Kept as rolled-up points for as long as you set in Admin, with forecasts like “disk full around April”.'],
]

export function EmptyFleet({ overview, isAdmin, onChanged }) {
  const pending = overview.pending_collectors || []
  const local = pending.find((item) => item.status === 'pending' && item.details?.dashboard_host)
  const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return (
    <div className="space-y-9">
      <section className="flex flex-wrap items-center justify-between gap-x-10 gap-y-8 pt-6">
        <div className="max-w-[560px]">
          <h1 className="m-0 text-[28px] font-semibold leading-[1.12] tracking-[-0.01em] text-paper">Your deck is empty.<br />Slide in your first server.</h1>
          <p className="mb-0 mt-5 text-sm leading-[23px] text-soft">Infradeck watches servers through a small collector container. Start with the machine Infradeck runs on; it takes one click. Other machines need one command each.</p>
          {isAdmin ? <div className="mt-6 flex flex-wrap items-center gap-2.5">
            <button type="button" className="btn btn-primary" onClick={() => scrollTo('add-this-machine')}><Icon name="plus" size={14} />Monitor this machine</button>
            <button type="button" className="btn" onClick={() => scrollTo('add-another-machine')}>Add another machine</button>
            <a className="btn btn-ghost" href={DOCS_URL} target="_blank" rel="noreferrer">Read the docs</a>
          </div> : <p className="mb-0 mt-5 text-[13px] text-muted">An admin needs to connect a server before anything shows up here.</p>}
        </div>
        <EmptyRack name={`${local?.hostname || 'server'} · this machine`} />
      </section>

      {isAdmin && <section aria-label="Add a server">
        <SectionHeading title="Add a server" detail="pick one to start, you can add the rest later" />
        <div className="grid items-start gap-4 xl:grid-cols-2">
          <div id="add-this-machine"><ThisMachineCard pending={pending} onChanged={onChanged} /></div>
          <div id="add-another-machine"><AnotherMachineCard pending={pending} onChanged={onChanged} /></div>
        </div>
      </section>}

      <section aria-label="Fleet">
        <SectionHeading title="Fleet" detail="0 servers · this is where they'll line up" />
        <div className="custom-scroll overflow-x-auto"><div className="min-w-[1080px]">
          <FleetHeader minutes={30} />
          <div className="rack" aria-hidden="true">
            {[[150, 205], [124, 178]].map(([first, second]) => (
              <div key={first} className={`drawer drawer-ghost ${FLEET_COLUMNS}`} style={{ minHeight: 58 }}>
                <span className="flex items-center gap-2.5"><span className="dot" style={{ background: 'var(--color-edge)' }} /><span className="ghost-bar" style={{ width: first }} /></span>
                <span className="ghost-bar" style={{ width: second }} /><span className="ghost-bar w-[120px]" /><span className="ghost-bar w-[120px]" /><span className="ghost-bar w-[140px]" /><span className="ghost-bar w-9" /><span className="ghost-bar w-20" />
              </div>
            ))}
          </div>
        </div></div>
      </section>

      <section aria-label="What you'll get once a server reports">
        <SectionHeading title="What you'll get once a server reports" />
        <div className="card grid divide-y divide-line sm:grid-cols-2 sm:divide-x xl:grid-cols-4 xl:divide-y-0">
          {PROMISES.map(([title, text]) => <div key={title} className="px-[18px] py-4"><h3 className="m-0 text-[13px] font-semibold text-paper">{title}</h3><p className="mb-0 mt-2 text-xs leading-[19px] text-muted">{text}</p></div>)}
        </div>
        <p className="mb-0 mt-3.5 text-xs text-muted">Everything stays on this machine. The agent only sends data to your model provider when you ask it something, and you can turn that off in Admin.</p>
      </section>
    </div>
  )
}
