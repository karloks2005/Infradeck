// Signing in, the two-factor step, registering and the first account on a new install. One frame for all of
// them: the brand panel on the left (a band on top on a phone), the form on the right.
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { Icon } from '../icons.jsx'
import { CODE_LENGTH, CodeBoxes } from '../ui.jsx'
import { landingPath } from '../prefs.js'

export const MIN_PASSWORD_LENGTH = 16
const MAX_PASSWORD_BYTES = 72
const DOCS_URL = 'https://github.com/karloks2005/Infradeck#readme'
const REPOSITORY_URL = 'https://github.com/karloks2005/Infradeck'
const SETUP_TOKEN_COMMAND = 'docker compose logs backend | grep "setup token"'

// What the backend lets a visitor know before signing in. ``enabled`` is null while loading; the backend decides
// (ALLOW_REGISTRATION, or no accounts exist yet).
function useInstance() {
  const [instance, setInstance] = useState({ enabled: null, bootstrapRequired: false, name: null, servers: null, version: null })
  useEffect(() => {
    let active = true
    apiRequest('/auth/config', { method: 'GET' })
      .then((data) => { if (active) setInstance({ enabled: Boolean(data.registration_enabled), bootstrapRequired: Boolean(data.bootstrap_required), name: data.instance_name || null, servers: data.server_count ?? null, version: data.version || null }) })
      .catch(() => { if (active) setInstance((current) => ({ ...current, enabled: true })) })
    return () => { active = false }
  }, [])
  return instance
}

// The README banner's rack: four units in line and the orange one pulled out.
function Rack({ className = '' }) {
  const unit = (y) => (
    <g key={y}>
      <rect x="27.5" y={y + 1.5} width="357" height="59" rx="3" fill="none" stroke="var(--color-paper)" strokeWidth="3" />
      <circle cx="51" cy={y + 31} r="5.5" fill="var(--color-paper)" /><circle cx="359" cy={y + 31} r="5.5" fill="var(--color-paper)" />
      {[276, 290, 304, 318].map((x) => <rect key={x} x={x} y={y + 22} width="3.5" height="18" fill="var(--color-paper)" />)}
    </g>
  )
  return (
    <svg viewBox="0 0 494 432" className={className} role="img" aria-label="A server rack with one unit pulled out">
      <rect x="2" y="0" width="3.5" height="432" fill="var(--color-paper)" /><rect x="404" y="0" width="3.5" height="432" fill="var(--color-paper)" />
      {[19, 100, 261, 341].map(unit)}
      <rect x="88" y="181" width="403" height="61" fill="var(--color-signal)" />
      <circle cx="112.5" cy="211.5" r="5.5" fill="var(--color-ink)" /><circle cx="465.5" cy="211.5" r="5.5" fill="var(--color-ink)" />
      <rect x="374" y="208" width="53" height="7" fill="var(--color-ink)" />
    </svg>
  )
}

function instanceLine(instance) {
  if (instance.bootstrapRequired) return 'new install'
  if (instance.servers == null) return ''
  return `${instance.servers} ${instance.servers === 1 ? 'server' : 'servers'}`
}

// ``banner``: on a phone, show the tall band with the tagline and rack (sign-in); otherwise one compact row.
function AuthFrame({ instance, banner = false, children }) {
  const where = instanceLine(instance)
  return (
    <div className="flex min-h-screen flex-col bg-bg text-paper lg:flex-row">
      <aside className="hidden w-[41.6%] min-w-[420px] max-w-[640px] flex-col border-r border-line bg-ink px-[52px] pb-6 pt-[49px] lg:flex">
        <img src="/logo.svg" alt="Infradeck" className="h-6 w-auto self-start" />
        <div className="flex flex-1 items-center justify-center py-10"><Rack className="w-[356px] max-w-[72%]" /></div>
        <p className="m-0 text-[21px] font-medium leading-7 text-paper">Every server. Every container.<br /><span className="text-signal">One dashboard.</span></p>
        <div className="mt-7 flex items-center justify-between gap-4 border-t border-line pt-[18px] text-xs text-muted">
          <span className="min-w-0 truncate">{instance.name && <span className="font-medium text-paper">{instance.name}</span>}{instance.name && where ? ' · ' : ''}{where}</span>
          <span className="shrink-0">Infradeck{instance.version ? ` v${instance.version}` : ''}</span>
        </div>
      </aside>

      {banner ? (
        <header className="border-b border-line bg-ink px-5 pb-5 pt-[calc(1.25rem+env(safe-area-inset-top))] lg:hidden">
          <img src="/logo.svg" alt="Infradeck" className="h-[19px] w-auto" />
          <div className="mt-5 flex items-end justify-between gap-4">
            <p className="m-0 text-sm font-medium leading-[19px] text-paper">Every server.<br />Every container.<br /><span className="text-signal">One dashboard.</span></p>
            <Rack className="w-[92px] shrink-0" />
          </div>
        </header>
      ) : (
        <header className="flex items-center justify-between gap-4 border-b border-line bg-ink px-5 pb-4 pt-[calc(1rem+env(safe-area-inset-top))] lg:hidden">
          <img src="/logo.svg" alt="Infradeck" className="h-[17px] w-auto" />
          <span className="truncate text-[11px] text-muted">{instance.bootstrapRequired ? 'new install' : instance.name}</span>
        </header>
      )}

      <main className="flex min-w-0 flex-1 flex-col px-5 lg:px-10">
        <div className="flex flex-1 flex-col pb-8 pt-8 lg:items-center lg:justify-center lg:py-12">
          <div className="w-full lg:w-[380px]">{children}</div>
        </div>
        <footer className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 pb-[calc(1.25rem+env(safe-area-inset-bottom))] text-xs text-dim lg:pb-6">
          <span>Self-hosted · your data stays on this machine</span>
          <a href={DOCS_URL} target="_blank" rel="noreferrer" className="hidden hover:text-soft lg:inline">Docs</a>
          <a href={REPOSITORY_URL} target="_blank" rel="noreferrer" className="hidden hover:text-soft lg:inline">GitHub</a>
        </footer>
      </main>
    </div>
  )
}

function Heading({ title, children }) {
  return <>
    <h1 className="m-0 text-2xl font-semibold leading-8 text-paper">{title}</h1>
    {children && <p className="mb-0 mt-2 text-sm leading-[21px] text-muted">{children}</p>}
  </>
}

const strong = 'font-medium text-paper'
const inputClass = 'auth-input'

function Field({ label, aside, children }) {
  return (
    <label className="mt-[22px] block first:mt-0">
      <span className="mb-2 flex items-baseline justify-between gap-3 text-[13px] text-soft"><span>{label}</span>{aside}</span>
      {children}
    </label>
  )
}

function PasswordInput({ value, onChange, ...props }) {
  const [shown, setShown] = useState(false)
  return (
    <span className="relative block">
      <input {...props} type={shown ? 'text' : 'password'} value={value} onChange={onChange} className={`${inputClass} pr-16`} />
      <button type="button" onClick={() => setShown((visible) => !visible)} aria-pressed={shown} aria-label={shown ? 'Hide password' : 'Show password'} className="absolute right-1 top-1 flex h-[34px] items-center rounded px-3 text-xs text-muted hover:text-paper">{shown ? 'Hide' : 'Show'}</button>
    </span>
  )
}

function Submit({ children, busy, disabled }) {
  return <button type="submit" disabled={busy || disabled} className="auth-submit">{children}<Icon name="arrow" size={15} /></button>
}

function Problem({ children }) {
  if (!children) return null
  return <div role="alert" className="mt-4 rounded-md border border-warn/40 px-3.5 py-2.5 text-[13px] leading-5 text-warn">{children}</div>
}

function Quiet({ children }) {
  return <p className="mb-0 mt-5 text-center text-[13px] leading-5 text-muted">{children}</p>
}

const quietLink = 'font-medium text-paper underline decoration-muted underline-offset-4 hover:decoration-paper'

function TwoFactorStep({ instance, email, pendingToken, onDone, onBack }) {
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const verify = async (value) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      onDone(await apiRequest('/auth/login/2fa', { method: 'POST', body: JSON.stringify({ pending_token: pendingToken, code: value.trim() }) }))
    } catch (requestError) {
      // An expired or used-up sign-in can't be retried; start again from the password.
      if (requestError.status === 401 && /sign in again|expired/i.test(requestError.message || '')) { onBack(requestError.message); return }
      setError(requestError.message || 'That code is not valid.')
      setCode('')
      setBusy(false)
    }
  }

  return (
    <AuthFrame instance={instance}>
      <form onSubmit={(event) => { event.preventDefault(); if (!recovery && code.length < CODE_LENGTH) setError('Type all six digits of the code.'); else if (code.trim()) verify(code) }}>
        <Heading title="Enter your code">Signed in as <span className={strong}>{email}</span>. {recovery ? 'Type one of the recovery codes you saved when you turned two-factor on.' : 'Open your authenticator app and type the 6-digit code.'}</Heading>
        <div className="mt-6">
          {recovery
            ? <input key="recovery" autoFocus value={code} onChange={(event) => setCode(event.target.value)} maxLength={32} autoComplete="off" autoCapitalize="none" spellCheck={false} aria-label="Recovery code" placeholder="xxxx-xxxx-xxxx" className={`${inputClass} mono tracking-wider`} />
            : <CodeBoxes key="digits" value={code} onChange={setCode} onComplete={verify} disabled={busy} />}
        </div>
        <Problem>{error}</Problem>
        <div className="mt-[22px]"><Submit busy={busy}>{busy ? 'Checking…' : 'Verify'}</Submit></div>
        <Quiet>{recovery ? 'Have your phone after all? ' : 'Lost your phone? '}<button type="button" className={quietLink} onClick={() => { setRecovery((mode) => !mode); setCode(''); setError('') }}>{recovery ? 'Use the 6-digit code' : 'Use a recovery code'}</button></Quiet>
        <p className="mb-0 mt-3 text-center text-[13px]"><button type="button" className="text-muted underline decoration-edge underline-offset-4 hover:text-paper" onClick={() => onBack('')}>Back to sign in</button></p>
      </form>
    </AuthFrame>
  )
}

export function LoginPage({ onLogin }) {
  const navigate = useNavigate()
  const instance = useInstance()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [pendingToken, setPendingToken] = useState(null)
  const [forgot, setForgot] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const finish = (data) => {
    const setupRequired = Boolean(data.setup_required)
    onLogin({ id: data.user.id, name: data.user.name, email: data.user.email, role: data.user.role, setup_required: setupRequired })
    navigate(setupRequired ? '/account' : landingPath())
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const data = await apiRequest('/auth/login', { method: 'POST', body: JSON.stringify({ email, password, remember }) })
      if (data.status === 'two_factor_required') {
        setPassword('')
        setPendingToken(data.pending_token)
        return
      }
      finish(data)
    } catch (requestError) {
      setError(requestError.message || 'Invalid email or password.')
    } finally { setBusy(false) }
  }

  if (pendingToken) return <TwoFactorStep instance={instance} email={email} pendingToken={pendingToken} onDone={finish} onBack={(message) => { setPendingToken(null); setError(message || '') }} />

  const host = typeof window !== 'undefined' ? window.location.host : ''
  return (
    <AuthFrame instance={instance} banner>
      <form onSubmit={handleSubmit}>
        <Heading title="Sign in">to <span className={strong}>{instance.name || 'Infradeck'}</span>{host ? ` on ${host}` : ''}</Heading>
        <div className="mt-6">
          <Field label="Email"><input type="email" autoComplete="username" autoFocus required value={email} onChange={(event) => setEmail(event.target.value)} className={inputClass} /></Field>
          <Field label="Password" aside={<button type="button" aria-expanded={forgot} onClick={(event) => { event.preventDefault(); setForgot((open) => !open) }} className="text-[13px] text-soft underline decoration-edge underline-offset-4 hover:text-paper">Forgot it?</button>}>
            <PasswordInput autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </Field>
          {forgot && <p className="mb-0 mt-3 rounded-md border border-line bg-ink px-3.5 py-3 text-[13px] leading-5 text-muted">Infradeck doesn't send email, so it can't mail you a reset link. Ask an admin of this install to help you back in. If you are the only admin, you can create a new admin account from the server: <span className="mono text-soft">docker compose exec -it backend python src/backend/seed_user.py</span></p>}
          <label className="mt-[22px] flex cursor-pointer items-center gap-2.5 text-[13px] text-soft">
            <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} className="auth-check" />
            Keep me signed in for 30 days
          </label>
        </div>
        <Problem>{error}</Problem>
        <div className="mt-[22px]"><Submit busy={busy}>{busy ? 'Signing in…' : 'Sign in'}</Submit></div>
        {instance.enabled === true && <Quiet>New to this Infradeck? <Link to="/register" className={quietLink}>{instance.bootstrapRequired ? 'Set it up' : 'Register'}</Link></Quiet>}
        {instance.enabled === false && <Quiet>Accounts are created by an admin.<span className="lg:hidden"><br /></span> Ask the owner for an invite.</Quiet>}
      </form>
    </AuthFrame>
  )
}

const byteLength = (text) => new TextEncoder().encode(text).length

// Whether the password is long enough, checked as it is typed. On a phone a four-part bar fills up to the rule.
function PasswordRule({ password, extra }) {
  const met = password.length >= MIN_PASSWORD_LENGTH
  const tooLong = byteLength(password) > MAX_PASSWORD_BYTES
  const filled = Math.min(4, Math.floor((password.length / MIN_PASSWORD_LENGTH) * 4))
  return <>
    <span aria-hidden="true" className="mt-2 flex gap-1 lg:hidden">{[0, 1, 2, 3].map((part) => <span key={part} className={`h-[3px] flex-1 rounded-sm ${part < filled ? 'bg-paper' : 'bg-edge'}`} />)}</span>
    <span className={`mt-2.5 flex items-center gap-1.5 text-xs ${tooLong ? 'text-warn' : met ? 'text-ok' : 'text-muted'}`}>
      {met && !tooLong && <Icon name="check" size={12} />}
      {tooLong ? `Too long: at most ${MAX_PASSWORD_BYTES} bytes` : `At least ${MIN_PASSWORD_LENGTH} characters${met ? '' : ` · ${password.length} so far`}`}{extra && !tooLong ? ` · ${extra}` : ''}
    </span>
  </>
}

function SetupTokenHelp() {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(SETUP_TOKEN_COMMAND); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { setCopied(false) }
  }
  return (
    <div className="mt-2.5 rounded-md border border-line bg-ink px-3 py-3 text-xs text-muted">
      Find it in the backend log:
      <div className="mt-2.5 flex items-center gap-1.5 rounded border border-line bg-bg py-1.5 pl-2 pr-1.5">
        <code className="no-scrollbar min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-[10.5px] text-paper">{SETUP_TOKEN_COMMAND}</code>
        <button type="button" onClick={copy} aria-label={copied ? 'Copied' : 'Copy the command'} title={copied ? 'Copied' : 'Copy'} className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-line text-soft hover:text-paper"><Icon name={copied ? 'check' : 'copy'} size={12} /></button>
      </div>
      <p className="mb-0 mt-2.5">Or use <span className="mono text-soft">ADMIN_BOOTSTRAP_TOKEN</span> from your .env if you set one.</p>
    </div>
  )
}

export function RegisterPage({ onRegister }) {
  const navigate = useNavigate()
  const instance = useInstance()
  const [form, setForm] = useState({ name: '', email: '', password: '', bootstrapToken: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // An invitation link from an admin: it names the email it is for and works while registration is closed.
  const [params] = useSearchParams()
  const inviteToken = params.get('invite') || ''
  const [invite, setInvite] = useState(inviteToken ? undefined : null)
  useEffect(() => {
    if (!inviteToken) return undefined
    let active = true
    apiRequest(`/auth/invite?token=${encodeURIComponent(inviteToken)}`, { method: 'GET' })
      .then((data) => { if (active) { setInvite(data); setForm((current) => ({ ...current, email: data.email })) } })
      .catch(() => { if (active) setInvite(null) })
    return () => { active = false }
  }, [inviteToken])
  const setup = instance.bootstrapRequired
  const change = (event) => setForm((current) => ({ ...current, [event.target.name]: event.target.value }))

  const handleSubmit = async (event) => {
    event.preventDefault()
    const name = form.name.trim()
    const email = form.email.trim()
    if (!name || !email || form.password.length < MIN_PASSWORD_LENGTH) {
      setError(`Please provide a name, an email, and a password of at least ${MIN_PASSWORD_LENGTH} characters.`)
      return
    }
    setBusy(true); setError('')
    try {
      const data = await apiRequest('/auth/register', { method: 'POST', body: JSON.stringify({ name, email, password: form.password, bootstrap_token: setup ? form.bootstrapToken.trim() : undefined, invite_token: invite ? inviteToken : undefined }) })
      onRegister({ id: data.user.id, name: data.user.name, email: data.user.email, role: data.user.role, setup_required: Boolean(data.setup_required) })
      navigate(data.setup_required ? '/account' : '/')
    } catch (requestError) {
      setError(requestError.message || 'Unable to create the account right now.')
    } finally { setBusy(false) }
  }

  if (inviteToken && invite === undefined) return <AuthFrame instance={instance}><Heading title="Checking your invitation…" /></AuthFrame>
  if (instance.enabled === false && !invite) {
    return (
      <AuthFrame instance={instance}>
        <Heading title={inviteToken ? 'This invitation is no longer valid' : 'Registration is closed'}>{inviteToken ? 'It may have been used or have expired. ' : ''}Accounts on <span className={strong}>{instance.name || 'this Infradeck'}</span> are created by an admin. Ask the owner for an invite.</Heading>
        <Quiet>Already have an account? <Link to="/login" className={quietLink}>Sign in</Link></Quiet>
      </AuthFrame>
    )
  }

  return (
    <AuthFrame instance={instance}>
      <form onSubmit={handleSubmit}>
        {setup
          ? <Heading title="Set up Infradeck"><span className="lg:hidden">No accounts yet. This one becomes the <span className={strong}>admin</span>.</span><span className="hidden lg:inline">This install has no accounts yet. The account you create now becomes the <span className={strong}>admin</span>.</span></Heading>
          : <Heading title="Create your account">on <span className={strong}>{instance.name || 'this Infradeck'}</span></Heading>}
        <div className="mt-6">
          <Field label="Full name"><input type="text" name="name" autoComplete="name" autoFocus required value={form.name} onChange={change} className={inputClass} /></Field>
          <Field label="Email" aside={invite ? <span className="text-xs text-muted">from your invitation</span> : null}><input type="email" name="email" autoComplete="username" required readOnly={Boolean(invite)} value={form.email} onChange={change} className={inputClass} /></Field>
          <Field label="Password">
            <PasswordInput name="password" autoComplete="new-password" required minLength={MIN_PASSWORD_LENGTH} value={form.password} onChange={change} />
            <PasswordRule password={form.password} extra={setup ? `up to ${MAX_PASSWORD_BYTES} bytes` : ''} />
            {!setup && <span className="mt-2 block text-xs leading-[18px] text-muted">A few random words make a good passphrase, like “correct horse battery staple”. Up to {MAX_PASSWORD_BYTES} bytes.</span>}
          </Field>
          {setup && <>
            <Field label={<span className="font-medium text-paper">Setup token</span>} aside={<span className="hidden text-[13px] text-muted lg:inline">proves you run this server</span>}>
              <input type="text" name="bootstrapToken" autoComplete="off" autoCapitalize="none" spellCheck={false} required value={form.bootstrapToken} onChange={change} placeholder="hlb_…" className={`${inputClass} mono`} />
            </Field>
            <div className="hidden lg:block"><SetupTokenHelp /></div>
            <p className="mb-0 mt-2.5 text-xs leading-[19px] text-muted lg:hidden">From the backend log: <span className="mono rounded border border-line bg-ink px-1 py-px text-soft">{SETUP_TOKEN_COMMAND}</span>, or <span className="mono rounded border border-line bg-ink px-1 py-px text-soft">ADMIN_BOOTSTRAP_TOKEN</span> in .env.</p>
          </>}
        </div>
        <Problem>{error}</Problem>
        <div className="mt-[22px]"><Submit busy={busy}>{busy ? 'Creating…' : setup ? 'Create admin account' : 'Create account'}</Submit></div>
        {!setup && <Quiet>Already have an account? <Link to="/login" className={quietLink}>Sign in</Link></Quiet>}
      </form>
    </AuthFrame>
  )
}
