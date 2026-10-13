// Your account: who you are, how you sign in, where alerts reach you and how Infradeck looks for you.
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { MIN_PASSWORD_LENGTH } from '../auth/AuthPages.jsx'
import { Icon } from '../icons.jsx'
import { applyDisplay } from '../prefs.js'
import { AppShell } from '../shell.jsx'
import { Check, Field, Notice, Outcome, Row, Section, SettingsLayout, ShownOnce, Switch, ago, describeBrowser, formatDate, useAction, useCopy, useLoaded } from '../ui.jsx'

const COLORS = ['', '#FF5A1F', '#E2B04A', '#6DBE8F', '#7FA7D9', '#B58BD6', '#D98BA4']
const post = (path, body) => apiRequest(path, { method: 'POST', body: JSON.stringify(body || {}) })
const initials = (name) => { const parts = (name || '?').trim().split(/\s+/); return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase() }

function Avatar({ name, color, size = 44 }) {
  return <span className="flex shrink-0 items-center justify-center rounded-md border border-line bg-raised font-semibold" style={{ width: size, height: size, fontSize: size * 0.34, color: color || 'var(--color-paper)', borderColor: color || undefined }}>{initials(name)}</span>
}

function Profile({ account, onSaved }) {
  const [form, setForm] = useState({ name: account.name, username: account.username || '', avatar_color: account.avatar_color || '' })
  const [state, run] = useAction()
  const dirty = form.name !== account.name || form.username !== (account.username || '') || form.avatar_color !== (account.avatar_color || '')
  const nextColor = () => setForm((current) => ({ ...current, avatar_color: COLORS[(COLORS.indexOf(current.avatar_color) + 1) % COLORS.length] }))
  const save = (event) => { event.preventDefault(); run(async () => onSaved(await apiRequest('/account/profile', { method: 'PATCH', body: JSON.stringify(form) })), 'Saved.') }
  return (
    <form onSubmit={save} className="card px-[18px] py-[18px]">
      <div className="flex flex-wrap items-center gap-3.5">
        <Avatar name={form.name} color={form.avatar_color} />
        <span className="min-w-0 flex-1"><span className="block truncate text-[15px] font-semibold text-paper">{form.name || account.name}</span><span className="block text-xs text-muted">{account.role_name} · member since {formatDate(account.created_at)}</span></span>
        <button type="button" className="btn btn-sm" onClick={nextColor}>Change initials color</button>
      </div>
      <div className="mt-5 grid gap-x-5 gap-y-4 sm:grid-cols-2">
        <Field label="Display name"><input className="field h-9" value={form.name} maxLength={255} required onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
        <Field label="Username" hint="Optional. Shown next to your name; you still sign in with your email."><input className="field mono h-9" value={form.username} maxLength={32} autoCapitalize="none" spellCheck={false} onChange={(event) => setForm({ ...form, username: event.target.value })} /></Field>
        <Field label="Email" hint="Used for sign-in and, if email is set up on this server, alert emails."><input className="field h-9 text-soft" value={account.email} readOnly /></Field>
        <Field label="Role" hint="Roles are changed in Admin › Users"><input className="field h-9 text-soft" value={account.role_name} readOnly /></Field>
      </div>
      <div className="mt-5 flex items-center justify-end gap-3">
        <Outcome state={state} />
        <button type="button" className="btn btn-ghost" disabled={!dirty} onClick={() => setForm({ name: account.name, username: account.username || '', avatar_color: account.avatar_color || '' })}>Discard</button>
        <button type="submit" className="btn btn-primary" disabled={!dirty || state.busy}>{state.busy ? 'Saving…' : 'Save profile'}</button>
      </div>
    </form>
  )
}

function RecoveryCodes({ codes, onDone, doneLabel = 'Done' }) {
  const [saved, setSaved] = useState(false)
  const [copied, copy] = useCopy()
  return (
    <div className="border-t border-line px-[18px] py-4">
      <p className="m-0 text-[13px] font-medium text-paper">Save your recovery codes</p>
      <p className="mb-0 mt-1 text-xs text-muted">Each works once if you lose your authenticator. They are shown only now; keep them somewhere safe, such as a password manager.</p>
      <ul className="m-0 mt-3 grid list-none grid-cols-2 gap-1.5 p-0 sm:grid-cols-5">{codes.map((code) => <li key={code} className="mono rounded border border-line bg-bg px-2 py-1.5 text-center text-paper">{code}</li>)}</ul>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-sm" onClick={() => copy(codes.join('\n'))}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy codes'}</button>
        <label className="flex items-center gap-2 text-xs text-soft"><Check checked={saved} onChange={setSaved} />I have saved these codes</label>
        <button type="button" className="btn btn-sm btn-primary" disabled={!saved} onClick={onDone}>{doneLabel}</button>
      </div>
    </div>
  )
}

// Confirm a sensitive change with the password and a current code.
function Reauth({ label, path, onDone, onCancel }) {
  const [form, setForm] = useState({ password: '', code: '' })
  const [state, run] = useAction()
  return (
    <form className="border-t border-line px-[18px] py-4" onSubmit={(event) => { event.preventDefault(); run(async () => onDone(await post(path, { password: form.password, code: form.code.trim() }))) }}>
      <p className="m-0 text-xs text-muted">Confirm with your password and a current code from your authenticator (or a recovery code).</p>
      <div className="mt-3 flex flex-wrap items-center gap-2.5">
        <input type="password" className="field h-9 w-56" autoComplete="current-password" autoFocus placeholder="Password" aria-label="Password" required value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
        <input className="field mono h-9 w-40" autoComplete="one-time-code" placeholder="Code" aria-label="Code" maxLength={32} required value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} />
        <button type="submit" className="btn btn-primary" disabled={state.busy}>{label}</button>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
      <div className="mt-2"><Outcome state={state} /></div>
    </form>
  )
}

function ChangePassword({ onDone }) {
  const [form, setForm] = useState({ current: '', next: '', repeat: '' })
  const [state, run] = useAction()
  const [problem, setProblem] = useState('')
  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }))
  const submit = (event) => {
    event.preventDefault()
    if (form.next.length < MIN_PASSWORD_LENGTH) return setProblem(`The new password must be at least ${MIN_PASSWORD_LENGTH} characters.`)
    if (form.next !== form.repeat) return setProblem('The two new passwords do not match.')
    setProblem('')
    return run(async () => { const data = await post('/auth/password', { current_password: form.current, new_password: form.next }); onDone(`Password changed. ${data.other_sessions_signed_out} other ${data.other_sessions_signed_out === 1 ? 'session was' : 'sessions were'} signed out.`) })
  }
  return (
    <form onSubmit={submit} className="border-t border-line px-[18px] py-4">
      <div className="grid max-w-[720px] gap-2.5 sm:grid-cols-3">
        <input type="password" className="field h-9" autoComplete="current-password" autoFocus placeholder="Current password" aria-label="Current password" required value={form.current} onChange={set('current')} />
        <input type="password" className="field h-9" autoComplete="new-password" placeholder={`New password (${MIN_PASSWORD_LENGTH}+ characters)`} aria-label="New password" required value={form.next} onChange={set('next')} />
        <input type="password" className="field h-9" autoComplete="new-password" placeholder="Repeat the new password" aria-label="Repeat the new password" required value={form.repeat} onChange={set('repeat')} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3"><button type="submit" className="btn btn-primary" disabled={state.busy}>{state.busy ? 'Changing…' : 'Change password'}</button><span className="text-xs text-muted">A few random words make a good passphrase. Changing it signs you out everywhere else.</span></div>
      <div className="mt-2">{problem ? <p role="alert" className="m-0 text-xs text-warn">{problem}</p> : <Outcome state={state} />}</div>
    </form>
  )
}

// Turning two-factor on: ask for the password (unless this is the forced first set-up), show the key, confirm a code.
function TwoFactorSetup({ forced, onEnabled, onCancel }) {
  const [setup, setSetup] = useState(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [state, run] = useAction()
  const begin = () => run(async () => setSetup(await post('/auth/2fa/setup', forced ? {} : { password })))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (forced) begin() }, [])
  return (
    <div className="border-t border-line px-[18px] py-4">
      {!setup && !forced && (
        <form className="flex flex-wrap items-center gap-2.5" onSubmit={(event) => { event.preventDefault(); begin() }}>
          <input type="password" className="field h-9 w-64" autoComplete="current-password" autoFocus placeholder="Your current password" aria-label="Your current password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          <button type="submit" className="btn btn-primary" disabled={state.busy}>Continue</button>
          {onCancel && <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>}
        </form>
      )}
      {setup && (
        <ol className="m-0 max-w-[640px] space-y-4 pl-5 text-[13px] text-soft">
          <li>In your authenticator app (Aegis, Authy, Google Authenticator), add an account and enter this key (time-based, 6 digits):
            <div className="mono mt-2 select-all break-all rounded-md border border-line bg-bg px-3 py-2 text-sm tracking-wider text-paper">{setup.secret.match(/.{1,4}/g).join(' ')}</div>
            <a href={setup.otpauth_uri} className="link mt-1.5 inline-block text-xs">or open it in an authenticator app on this device</a>
          </li>
          <li>Enter the code the app shows now:
            <form className="mt-2 flex flex-wrap items-center gap-2.5" onSubmit={(event) => { event.preventDefault(); run(async () => onEnabled((await post('/auth/2fa/enable', { code: code.trim() })).recovery_codes)) }}>
              <input className="field mono h-9 w-36 tracking-widest" inputMode="numeric" autoComplete="one-time-code" maxLength={8} placeholder="123456" aria-label="Code from the authenticator app" required value={code} onChange={(event) => setCode(event.target.value)} />
              <button type="submit" className="btn btn-primary" disabled={state.busy}>Turn on</button>
            </form>
          </li>
        </ol>
      )}
      <div className="mt-2"><Outcome state={state} /></div>
    </div>
  )
}

function Security({ account, reload, forced = false, onEnrolled }) {
  const [open, setOpen] = useState(forced ? 'setup' : null)
  const [codes, setCodes] = useState(null)
  const [message, setMessage] = useState('')
  const two = account.two_factor
  const days = account.password_changed_at ? Math.floor((Date.now() - new Date(account.password_changed_at).getTime()) / 86400000) : null
  const toggle = (name) => { setMessage(''); setOpen((current) => (current === name ? null : name)) }
  const done = (text) => { setOpen(null); setMessage(text || ''); reload() }
  return (
    <div className="card divide-y divide-line overflow-hidden">
      {!forced && <div>
        <Row icon="***" title="Password" detail={days == null ? 'Never changed since the account was created' : days === 0 ? 'Changed today' : `Last changed ${days} ${days === 1 ? 'day' : 'days'} ago`}><button type="button" className="btn btn-sm" aria-expanded={open === 'password'} onClick={() => toggle('password')}>Change password</button></Row>
        {open === 'password' && <ChangePassword onDone={done} />}
      </div>}
      <div>
        <Row icon="2FA" title="Two-factor authentication" detail={!two.available ? 'Not possible on this server yet: it has no SECRETS_KEY (see docs/accounts.md)' : two.enabled ? 'Authenticator app · asked for at every new sign-in' : two.required ? 'Required for your account. Set it up to continue.' : 'A 6-digit code from an authenticator app, asked for after your password'} tone={!two.enabled && two.required ? 'warn' : undefined}>
          <span className={`chip ${two.enabled ? 'chip-ok' : ''}`}>{two.enabled ? 'On' : 'Off'}</span>
          {two.available && !forced && <button type="button" className="btn btn-sm" aria-expanded={open === 'setup' || open === 'off'} onClick={() => toggle(two.enabled ? 'off' : 'setup')}>{two.enabled ? 'Manage' : 'Set up'}</button>}
        </Row>
        {open === 'setup' && !two.enabled && !codes && <TwoFactorSetup forced={forced} onCancel={forced ? null : () => setOpen(null)} onEnabled={(list) => { setCodes(list); reload() }} />}
        {open === 'off' && two.enabled && (two.required
          ? <p className="m-0 border-t border-line px-[18px] py-4 text-xs text-muted">Two-factor is required for your account, so it can't be turned off. An admin sets that in Admin › Users.</p>
          : <Reauth label="Turn off two-factor" path="/auth/2fa/disable" onCancel={() => setOpen(null)} onDone={() => done('Two-factor is off. Your other sessions were signed out.')} />)}
        {codes && <RecoveryCodes codes={codes} doneLabel={onEnrolled ? 'Continue' : 'Done'} onDone={() => { setCodes(null); setOpen(null); if (onEnrolled) onEnrolled() }} />}
      </div>
      {two.enabled && !forced && <div>
        <Row icon="#" title="Recovery codes" detail={`${two.recovery_codes_left} left. Stored only as hashes, so they can't be shown again, only replaced.`} tone={two.recovery_codes_left <= 2 ? 'warn' : undefined}><button type="button" className="btn btn-sm" aria-expanded={open === 'codes'} onClick={() => toggle('codes')}>Regenerate</button></Row>
        {open === 'codes' && !codes && <Reauth label="Generate new codes" path="/auth/2fa/recovery-codes" onCancel={() => setOpen(null)} onDone={(data) => { setCodes(data.recovery_codes); reload() }} />}
      </div>}
      {!forced && <Row icon="key" title="Passkeys" detail="Sign in with a fingerprint or security key instead of a password. Not available yet."><button type="button" className="btn btn-sm" disabled title="Passkeys are not built yet"><Icon name="plus" size={13} />Add passkey</button></Row>}
      {message && <p role="status" className="m-0 px-[18px] py-3 text-xs text-ok">{message}</p>}
    </div>
  )
}

function Sessions({ days, onCount }) {
  const { data, error, reload } = useLoaded('/auth/sessions')
  const [state, run] = useAction()
  const sessions = data?.sessions
  useEffect(() => { if (sessions) onCount(sessions.length) }, [sessions, onCount])
  return (
    <div className="card overflow-hidden">
      <div className="custom-scroll overflow-x-auto"><table className="w-full min-w-[560px] border-collapse text-left text-[13px]">
        <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">Device</th><th className="font-medium">Address</th><th className="font-medium">Signed in</th><th className="pr-[18px]" /></tr></thead>
        <tbody>
          {(sessions || []).map((session) => (
            <tr key={session.id} className="h-[46px] border-t border-line">
              <td className="pl-[18px] font-medium text-paper">{describeBrowser(session.user_agent)}{session.current && <span className="chip chip-ok ml-2.5 font-normal">this session</span>}</td>
              <td className="mono text-soft">{session.ip || 'unknown'}</td>
              <td className="text-soft">{session.created_at ? ago(session.created_at) : 'earlier'}</td>
              <td className="pr-[18px] text-right">{!session.current && <button type="button" className="link" disabled={state.busy} onClick={() => run(async () => { await apiRequest(`/auth/sessions/${session.id}`, { method: 'DELETE' }); reload() })}>Sign out</button>}</td>
            </tr>
          ))}
          {!sessions && <tr className="border-t border-line"><td colSpan={4} className="px-[18px] py-5 text-center text-xs text-muted">{error || 'Loading…'}</td></tr>}
        </tbody>
      </table></div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-[18px] py-3 text-xs text-muted">
        <span>A session lasts a day, or {days} days if you ticked “Keep me signed in”. <Outcome state={state} /></span>
        {sessions?.length > 1 && <button type="button" className="btn btn-sm" disabled={state.busy} onClick={() => run(async () => { const result = await post('/auth/sessions/revoke-others'); reload(); return result }, 'Signed out everywhere else.')}>Sign out all other sessions</button>}
      </div>
    </div>
  )
}

function Tokens({ account, reload }) {
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: '', scope: 'read', expires_in_days: '' })
  const [created, setCreated] = useState(null)
  const [state, run] = useAction()
  const canWrite = account.role === 'admin'
  const create = (event) => { event.preventDefault(); run(async () => { setCreated(await post('/account/tokens', { name: form.name, scope: form.scope, expires_in_days: form.expires_in_days ? Number(form.expires_in_days) : null })); setAdding(false); setForm({ name: '', scope: 'read', expires_in_days: '' }); reload() }) }
  const expires = (token) => { if (!token.expires_at) return 'never'; const days = Math.ceil((new Date(token.expires_at).getTime() - Date.now()) / 86400000); return days <= 0 ? 'expired' : `in ${days} ${days === 1 ? 'day' : 'days'}` }
  return (
    <div className="card overflow-hidden">
      {account.tokens.length > 0 && <div className="custom-scroll overflow-x-auto"><table className="w-full min-w-[620px] border-collapse text-left text-[13px]">
        <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">Name</th><th className="font-medium">Scope</th><th className="font-medium">Last used</th><th className="font-medium">Expires</th><th className="pr-[18px]" /></tr></thead>
        <tbody>{account.tokens.map((token) => (
          <tr key={token.id} className="h-[54px] border-t border-line">
            <td className="pl-[18px]"><span className="block font-medium text-paper">{token.name}</span><span className="mono block text-[11px] text-dim">idk_{token.scope === 'write' ? 'w' : 'r'}_••••••••{token.hint}</span></td>
            <td><span className={`chip ${token.scope === 'write' ? 'chip-warn' : ''}`}>{token.scope === 'write' ? 'read + acknowledge' : 'read · metrics'}</span></td>
            <td className="text-soft">{ago(token.last_used_at)}</td>
            <td className={expires(token) === 'expired' ? 'text-warn' : 'text-soft'}>{expires(token)}</td>
            <td className="pr-[18px] text-right"><button type="button" className="link" onClick={() => { if (window.confirm(`Revoke the token “${token.name}”? Whatever uses it stops working at once.`)) run(async () => { await apiRequest(`/account/tokens/${token.id}`, { method: 'DELETE' }); reload() }) }}>Revoke</button></td>
          </tr>
        ))}</tbody>
      </table></div>}
      {created && <div className="border-t border-line px-[18px] py-3.5 first:border-t-0"><ShownOnce value={created.token}>Copy “{created.name}” now. It is shown only once. Send it as <span className="mono">Authorization: Bearer …</span></ShownOnce></div>}
      {adding && (
        <form onSubmit={create} className="flex flex-wrap items-end gap-3 border-t border-line px-[18px] py-3.5 first:border-t-0">
          <Field label="Name"><input className="field h-9 w-52" autoFocus required maxLength={64} placeholder="home-assistant" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
          <Field label="Scope"><select className="select h-9" value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value })}><option value="read">read · metrics</option><option value="write" disabled={!canWrite}>read + acknowledge{canWrite ? '' : ' (admins only)'}</option></select></Field>
          <Field label="Expires"><select className="select h-9" value={form.expires_in_days} onChange={(event) => setForm({ ...form, expires_in_days: event.target.value })}><option value="">never</option><option value="30">in 30 days</option><option value="90">in 90 days</option><option value="365">in a year</option></select></Field>
          <button type="submit" className="btn btn-primary h-9" disabled={state.busy}>Create token</button>
          <button type="button" className="btn btn-ghost h-9" onClick={() => setAdding(false)}>Cancel</button>
        </form>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-[18px] py-3 text-xs text-muted first:border-t-0">
        <span>{account.tokens.length ? 'A token is shown once, when it\'s created.' : 'No tokens yet. A token is shown once, when it\'s created.'} A read token can only read; no token can change your account or the servers. <Outcome state={state} /></span>
        {!adding && <button type="button" className="btn btn-sm" onClick={() => { setCreated(null); setAdding(true) }}><Icon name="plus" size={13} />New token</button>}
      </div>
    </div>
  )
}

const CHANNELS = { email: ['@', 'Email'], ntfy: ['ntfy', 'ntfy'], discord: ['#', 'Discord webhook'], webhook: ['{ }', 'Webhook'] }
const EVENTS = [['critical', 'Critical incidents', 'dot-signal'], ['warning', 'Warnings', 'dot-warn'], ['resolved', 'Resolved', 'dot-ok']]
const DEFAULT_ROUTING = { critical: true, warning: true, resolved: false }

function Notifications({ account, reload, onPreferences }) {
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ kind: 'ntfy', target: '' })
  const [state, run] = useAction()
  const prefs = account.preferences
  const routed = (event, channel) => prefs.routing?.[event]?.[String(channel.id)] ?? DEFAULT_ROUTING[event]
  const save = (changes, done) => run(async () => onPreferences(await apiRequest('/account/preferences', { method: 'PUT', body: JSON.stringify(changes) })), done)
  const route = (event, channel, on) => {
    const routing = Object.fromEntries(EVENTS.map(([key]) => [key, Object.fromEntries(account.channels.map((item) => [String(item.id), routed(key, item)]))]))
    routing[event][String(channel.id)] = on
    save({ routing })
  }
  const add = (event) => { event.preventDefault(); run(async () => { await post('/account/channels', form.kind === 'email' ? { kind: 'email' } : form); setAdding(false); setForm({ kind: 'ntfy', target: '' }); reload() }) }
  const status = (channel) => (channel.last_ok == null ? ['Not tested', ''] : channel.last_ok ? ['Working', 'chip-ok'] : [`Failed ${ago(channel.last_checked_at)} · ${channel.last_status}`, 'chip-warn'])
  const zone = prefs.display.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone
  return <>
    {!account.secrets_configured && <Notice>Channels can't be saved on this server yet: it has no <span className="mono">SECRETS_KEY</span> to encrypt their addresses with (see docs/accounts.md).</Notice>}
    <div className="card divide-y divide-line overflow-hidden">
      {account.channels.map((channel) => { const [text, tone] = status(channel); return (
        <Row key={channel.id} icon={CHANNELS[channel.kind][0]} title={CHANNELS[channel.kind][1]} detail={<span className={channel.kind === 'email' ? '' : 'mono'}>{channel.display}</span>}>
          <span className={`chip ${tone}`}>{text}</span>
          <button type="button" className="btn btn-sm btn-ghost" disabled={state.busy} onClick={() => run(async () => { await post(`/account/channels/${channel.id}/test`); reload() })}>Send test</button>
          <button type="button" className="link text-xs" onClick={() => { if (window.confirm(`Remove this ${CHANNELS[channel.kind][1]} channel?`)) run(async () => { await apiRequest(`/account/channels/${channel.id}`, { method: 'DELETE' }); reload() }) }}>Remove</button>
        </Row>
      ) })}
      {adding && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-3 px-[18px] py-3.5">
          <Field label="Kind"><select className="select h-9" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}><option value="ntfy">ntfy</option><option value="discord">Discord webhook</option><option value="webhook">Any webhook (Slack, Gotify, Telegram bridge…)</option><option value="email" disabled={!account.smtp_configured}>Email{account.smtp_configured ? '' : ' (not set up on this server)'}</option></select></Field>
          {form.kind === 'email' ? <span className="pb-2 text-xs text-muted">Alerts go to {account.email}.</span>
            : <Field label="Address" className="min-w-0 flex-1 basis-72"><input className="field mono h-9" autoFocus required maxLength={500} placeholder={form.kind === 'ntfy' ? 'https://ntfy.sh/your-topic' : 'https://…'} value={form.target} onChange={(event) => setForm({ ...form, target: event.target.value })} /></Field>}
          <button type="submit" className="btn btn-primary h-9" disabled={state.busy || !account.secrets_configured}>Add</button>
          <button type="button" className="btn btn-ghost h-9" onClick={() => setAdding(false)}>Cancel</button>
        </form>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 px-[18px] py-3 text-xs text-muted">
        <span>{account.channels.length ? 'ntfy, Discord, or any webhook (Slack, Gotify, a Telegram bridge).' : 'No channels yet, so alerts only show up here. Add ntfy, Discord or any webhook.'} The address is stored encrypted and never shown in full again. <Outcome state={state} /></span>
        {!adding && <button type="button" className="btn btn-sm" onClick={() => setAdding(true)}><Icon name="plus" size={13} />Add channel</button>}
      </div>
    </div>
    {account.channels.length > 0 && (
      <div className="card overflow-hidden"><div className="custom-scroll overflow-x-auto"><table className="w-full border-collapse text-left text-[13px]">
        <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">Send</th>{account.channels.map((channel) => <th key={channel.id} className="w-[110px] px-2 text-center font-medium">{CHANNELS[channel.kind][1].replace(' webhook', '')}</th>)}</tr></thead>
        <tbody>
          {EVENTS.map(([key, label, dot]) => (
            <tr key={key} className="h-11 border-t border-line">
              <td className="pl-[18px] text-paper"><span className={`dot ${dot} mr-2.5`} style={{ width: 6, height: 6 }} />{label}</td>
              {account.channels.map((channel) => <td key={channel.id} className="px-2 text-center"><Check checked={routed(key, channel)} label={`${label} to ${CHANNELS[channel.kind][1]}`} onChange={(on) => route(key, channel, on)} /></td>)}
            </tr>
          ))}
          {['Agent diagnosis ready', 'Daily digest', 'Weekly report (Monday)'].map((label) => (
            <tr key={label} className="h-11 border-t border-line text-dim"><td className="pl-[18px]"><span className="dot mr-2.5" style={{ width: 6, height: 6 }} />{label} <span className="ml-1.5 text-[11px]">not available yet</span></td>{account.channels.map((channel) => <td key={channel.id} className="px-2 text-center"><Check checked={false} disabled onChange={() => {}} label={`${label} (not available yet)`} /></td>)}</tr>
          ))}
        </tbody>
      </table></div></div>
    )}
    <div className="card">
      <Row title="Quiet hours" detail={`Warnings wait until the end of quiet hours. Critical still comes through. Times are in ${zone}.`}>
        <input type="time" className="field h-8 w-[104px]" aria-label="Quiet hours start" value={prefs.quiet.start} onChange={(event) => save({ quiet: { ...prefs.quiet, start: event.target.value } })} />
        <span className="text-xs text-muted">to</span>
        <input type="time" className="field h-8 w-[104px]" aria-label="Quiet hours end" value={prefs.quiet.end} onChange={(event) => save({ quiet: { ...prefs.quiet, end: event.target.value } })} />
        <Switch checked={prefs.quiet.enabled} label="Quiet hours" onChange={(enabled) => save({ quiet: { ...prefs.quiet, enabled } })} />
      </Row>
    </div>
  </>
}

function Choice({ value, options, onChange, label }) {
  return <div className="seg" role="group" aria-label={label}>{options.map(([key, text]) => <button key={key} type="button" aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>)}</div>
}

function Display({ account, onPreferences }) {
  const [state, run] = useAction()
  const display = account.preferences.display
  const set = (key, value) => run(async () => { const saved = await apiRequest('/account/preferences', { method: 'PUT', body: JSON.stringify({ display: { [key]: value } }) }); applyDisplay(saved.display); onPreferences(saved) })
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone
  return (
    <div className="card divide-y divide-line">
      <Row title="Theme" detail="Dark is what Infradeck was designed in."><Choice label="Theme" value={display.theme} options={[['dark', 'Dark'], ['light', 'Light'], ['system', 'System']]} onChange={(value) => set('theme', value)} /></Row>
      <Row title="Density" detail="Compact fits more rows on a screen."><Choice label="Density" value={display.density} options={[['comfortable', 'Comfortable'], ['compact', 'Compact']]} onChange={(value) => set('density', value)} /></Row>
      <Row title="Open on" detail="The page you land on after signing in."><select className="select" aria-label="Open on" value={display.open_on} onChange={(event) => set('open_on', event.target.value)}><option value="overview">Overview</option><option value="statistics">Statistics</option><option value="agent">Agent</option></select></Row>
      <Row title="Time zone" detail={`Used for your quiet hours. Charts and incident times follow this browser (${browserZone}).`}><select className="select max-w-[240px]" aria-label="Time zone" value={display.timezone} onChange={(event) => set('timezone', event.target.value)}><option value="">This browser ({browserZone})</option>{zones.map((zone) => <option key={zone} value={zone}>{zone}</option>)}</select></Row>
      <Row title="Time format" detail="Saved with your account. Times are shown in 24 h everywhere for now."><Choice label="Time format" value={display.time_format} options={[['24h', '24 h'], ['12h', '12 h']]} onChange={(value) => set('time_format', value)} /></Row>
      <Row title="Sizes" detail="Saved with your account. Sizes are shown as 1 GB = 1024 MB everywhere for now."><Choice label="Sizes" value={display.sizes} options={[['GB', 'GB'], ['GiB', 'GiB']]} onChange={(value) => set('sizes', value)} /></Row>
      {(state.error || state.ok) && <div className="px-[18px] py-2.5"><Outcome state={state} /></div>}
    </div>
  )
}

export default function AccountPage({ user, onLogout, onUserChange }) {
  const navigate = useNavigate()
  const status = useLoaded('/auth/2fa/status')
  // Remember that this visit began as a required set-up, so Continue leads on to the dashboard once it is done.
  const wasRequired = useRef(false)
  const forced = Boolean(status.data?.setup_required)
  if (forced) wasRequired.current = true
  const { data: account, error, reload, setData } = useLoaded(status.data && !forced ? '/account' : null)
  const [sessionCount, setSessionCount] = useState(null)
  useEffect(() => { if (status.data) onUserChange({ setup_required: status.data.setup_required, two_factor_enabled: status.data.enabled }) }, [status.data]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (account) { applyDisplay(account.preferences.display); onUserChange({ name: account.name }) } }, [account]) // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => { status.reload(); reload() }
  const setPreferences = (preferences) => setData({ ...account, preferences })

  if (forced) {
    const two = { enabled: status.data.enabled, available: status.data.available, required: true, recovery_codes_left: status.data.recovery_codes_left }
    return (
      <AppShell user={user} onLogout={onLogout} bare>
        <div className="mx-auto max-w-[760px] space-y-4 px-3 pt-7 sm:px-4 lg:px-7">
          <h1 className="m-0 text-[21px] font-semibold text-paper">Set up two-factor to continue</h1>
          <p className="m-0 text-[13px] text-soft">Infradeck can see inside every server you connect, so your account needs a second step at sign-in before you can use it.</p>
          <Security account={{ two_factor: two }} reload={status.reload} forced onEnrolled={() => { status.reload(); navigate('/') }} />
        </div>
      </AppShell>
    )
  }

  const sections = [{ id: 'profile', label: 'Profile' }, { id: 'security', label: 'Security' }, { id: 'sessions', label: 'Sessions', count: sessionCount }, { id: 'tokens', label: 'API tokens', count: account?.tokens.length || null }, { id: 'notifications', label: 'Notifications' }, { id: 'display', label: 'Display' }]
  return (
    <AppShell user={user} onLogout={onLogout} bare>
      <SettingsLayout title="Account" description="Your sign-in, where alerts reach you, and how Infradeck looks for you. Server-wide settings live in Admin." sections={sections}
        footer={<button type="button" onClick={onLogout} className="flex h-[31px] w-full items-center rounded-md border-0 bg-transparent px-2.5 text-left text-[13px] text-muted hover:bg-ink hover:text-paper">Sign out</button>}>
        {(error || status.error) && !account && <Notice>{error || status.error}</Notice>}
        {!account && !error && <p className="m-0 py-16 text-center text-[13px] text-muted">Loading your account…</p>}
        {account && <>
          <Section id="profile" title="Profile" description="Shown in the sidebar and on incidents you acknowledge."><Profile key={`${account.name}-${account.username}-${account.avatar_color}`} account={account} onSaved={setData} /></Section>
          <Section id="security" title="Security" description="Infradeck can see inside every server you connect. Protect it like an SSH key."><Security account={account} reload={refresh} onEnrolled={wasRequired.current ? () => navigate('/') : null} /></Section>
          <Section id="sessions" title="Sessions" description="Every browser and device signed in as you. Sign out anything you don't recognize."><Sessions days={account.session_days} onCount={setSessionCount} /></Section>
          <Section id="tokens" title="API tokens" description="For scripts, Home Assistant or a status page. Tokens act as you, limited to the scope you pick."><Tokens account={account} reload={reload} /></Section>
          <Section id="notifications" title="Notifications" description="Where your alerts go and how loud they are. Critical alerts ignore quiet hours."><Notifications account={account} reload={reload} onPreferences={setPreferences} /></Section>
          <Section id="display" title="Display" description="Only affects how you see Infradeck."><Display account={account} onPreferences={setPreferences} /></Section>
          <button type="button" onClick={onLogout} className="btn lg:hidden">Sign out</button>
        </>}
      </SettingsLayout>
    </AppShell>
  )
}
