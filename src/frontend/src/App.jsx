import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import StatisticsContent from './StatisticsPage.jsx'

const AUTH_KEY = 'homie-auth-user'
const USERS_KEY = 'homie-auth-users'
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'
// Vite only exposes VITE_-prefixed env vars; clamp so a bad value can't flood the API.
const LIVE_GRAPH_REFRESH_INTERVAL_SECONDS = Math.max(Number(import.meta.env.VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS) || 3, 1)

function createChatThread() {
  return {
    id: typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `chat-${Date.now()}`,
    title: 'New chat',
    preview: 'Start a task with Homie',
    time: 'now',
    unread: 0,
    persisted: false,
    messages: [],
  }
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  })

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}))
    const detail = errorBody.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Request failed.')
    error.status = response.status
    throw error
  }

  return response.json()
}

function parseAgentMessage(text) {
  return { text, risk: 'neutral' }
}

const navigation = [
  { label: 'Dashboard', to: '/', icon: '▣' },
  { label: 'Statistics', to: '/statistics', icon: '▤' },
  { label: 'Agent', to: '/agent', icon: '◷' },
  { label: 'Account', to: '/account', icon: '◉' },
  { label: 'Admin', to: '/admin', icon: '⚙', adminOnly: true },
]

const conversationThreads = []

const stats = [
  { label: 'Health checks', value: '0', delta: 'Monitoring-only', accent: 'emerald' },
  { label: 'Avg. response', value: '3.8s', delta: '-0.6s', accent: 'silver' },
  { label: 'Uptime', value: '99.97%', delta: '+0.3%', accent: 'violet' },
  { label: 'Alerts', value: '0', delta: 'No critical findings', accent: 'amber' },
]

const logEntries = [
  { id: 'health-monitor', type: 'COMPLETED', taskTitle: 'Monitoring-only mode enabled', message: 'The agent can run only predefined health checks.', time: 'now', actor: 'homie', steps: ['Shell command execution is disabled.', 'Server, container, and daily health checks are available.'] },
]

const adminSettings = {
  systemPrompt: 'Homie is a monitoring-only health assistant. It can request only predefined read-only checks and cannot execute commands or change the system.',
  safetyRules: [
    'No shell execution',
    'Closed allow-list of health checks',
    'No configuration or service changes',
    'Alerts only for critical findings',
  ],
}

function getStoredUser() {
  if (typeof window === 'undefined') return null

  try {
    const raw = window.localStorage.getItem(AUTH_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function getStoredUsers() {
  if (typeof window === 'undefined') return []

  try {
    const raw = window.localStorage.getItem(USERS_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function getAccentClasses(accent) {
  const map = {
    emerald: 'from-emerald-500/25 to-emerald-400/10 text-emerald-300 ring-emerald-400/20',
    silver: 'from-[#e6e7ea]/25 to-[#f5f5f6]/10 text-[#cfcfcf] ring-[#cfcfcf]/20',
    violet: 'from-violet-500/25 to-violet-400/10 text-violet-300 ring-violet-400/20',
    amber: 'from-amber-500/25 to-amber-400/10 text-amber-300 ring-amber-400/20',
  }
  return map[accent] || map.emerald
}

function getRiskStyles(risk, role) {
  if (role === 'user') {
    return {
      card: 'border-white/20 bg-white/5',
      badge: 'bg-white/10 text-slate-200 border-white/20',
      dot: 'bg-white',
    }
  }

  const map = {
    low: {
      card: 'border-emerald-400/40 bg-emerald-500/5',
      badge: 'bg-emerald-500/10 text-emerald-300 border-emerald-400/30',
      dot: 'bg-emerald-400',
    },
    medium: {
      card: 'border-amber-400/45 bg-amber-500/5',
      badge: 'bg-amber-500/10 text-amber-300 border-amber-400/30',
      dot: 'bg-amber-400',
    },
    high: {
      card: 'border-red-400/45 bg-red-500/5',
      badge: 'bg-red-500/10 text-red-300 border-red-400/30',
      dot: 'bg-red-400',
    },
    neutral: {
      card: 'border-[#1f2326]/80 bg-[#0d1013]/60',
      badge: 'bg-[#111417]/70 text-[#bcbcbc] border-[#1f2326]',
      dot: 'bg-[#cfcfcf]',
    },
  }

  return map[risk] || map.neutral
}

function MobileTopBar({ user, onLogout }) {
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    if (!menuOpen) return
    const close = (event) => { if (event.key === 'Escape') setMenuOpen(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [menuOpen])

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-[#0d1013]/85 pt-[env(safe-area-inset-top)] backdrop-blur-xl lg:hidden">
      <div className="flex h-14 items-center justify-between px-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-linear-to-br from-white to-[#cfcfcf] text-xs font-bold text-slate-950">H</div>
          <span className="text-sm font-medium text-[#cfcfcf]">Homie</span>
        </div>
        {user && (
          <div className="relative">
            <button type="button" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-haspopup="menu" aria-label="Account menu" className="flex h-10 w-10 items-center justify-center rounded-full border border-white/10 bg-white/5 text-sm font-medium text-white transition active:bg-white/10">
              {(user.name || user.email || '?').trim().charAt(0).toUpperCase()}
            </button>
            {menuOpen && <>
              <button type="button" aria-label="Close account menu" onClick={() => setMenuOpen(false)} className="fixed inset-0 z-10 cursor-default" />
              <div role="menu" className="glass-panel absolute right-0 z-20 mt-2 w-64 rounded-2xl p-3">
                <div className="px-1 text-sm font-medium text-white">{user.name}</div>
                <div className="truncate px-1 text-xs text-[#9aa0a6]">{user.email}</div>
                <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onLogout() }} className="mt-3 flex h-11 w-full items-center justify-center rounded-xl border border-red-400/30 bg-red-500/10 text-sm text-red-300 transition active:bg-red-500/20">Logout</button>
              </div>
            </>}
          </div>
        )}
      </div>
    </header>
  )
}

function MobileTabBar({ user }) {
  const items = navigation.filter((item) => !item.adminOnly || user?.role === 'admin')
  return (
    <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 border-t border-white/5 bg-[#0d1013]/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden">
      <div className="mx-auto grid max-w-md" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) => `flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium transition ${isActive ? 'text-white' : 'text-[#7f8589] active:text-[#cfcfcf]'}`}
          >
            {({ isActive }) => <>
              <span className={`flex h-7 w-12 items-center justify-center rounded-full text-base transition ${isActive ? 'bg-white/10' : ''}`}>{item.icon}</span>
              {item.label}
            </>}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

function AppShell({ children, user, onLogout, fitViewport = false }) {
  return (
    <div className={`${fitViewport ? 'min-h-screen xl:h-screen xl:overflow-hidden' : 'min-h-screen'} bg-[#0d1013] text-[#e6e7ea]`}>
      <MobileTopBar user={user} onLogout={onLogout} />
      <div className={`${fitViewport ? 'xl:h-full xl:min-h-0' : ''} mx-auto flex max-w-[1700px] gap-6 px-3 pt-4 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:px-4 lg:px-6 lg:py-5`}>
        <aside className="glass-panel hidden w-72 shrink-0 rounded-3xl p-4 lg:block">
          <div className="mb-6 flex items-center gap-3 px-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-linear-to-br from-white to-[#cfcfcf] text-sm font-bold text-slate-950">H</div>
            <div>
              <div className="text-sm font-medium text-[#cfcfcf]">Homie</div>
              <div className="text-xs text-[#9aa0a6]">Sysadmin agent</div>
            </div>
          </div>

          <nav className="space-y-2">
            {navigation.filter((item) => !item.adminOnly || user?.role === 'admin').map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm transition ${
                    isActive
                      ? 'bg-[#171b1f] text-white ring-1 ring-white/10'
                      : 'text-[#bcbcbc] hover:bg-[#111417] hover:text-white'
                  }`
                }
              >
                <span className="text-base">{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>

          {user && (
            <div className="mt-8 rounded-2xl border border-neutral-800 bg-[#111417] p-3">
              <div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Signed in</div>
              <div className="mt-2 text-sm font-medium text-white">{user.name}</div>
              <div className="text-xs text-[#9aa0a6]">{user.email}</div>
              <button
                onClick={onLogout}
                className="mt-4 w-full rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-300 transition hover:bg-red-500/15"
              >
                Logout
              </button>
            </div>
          )}

          <div className="mt-8 rounded-2xl border border-emerald-400/20 bg-emerald-500/5 p-3">
            <div className="flex items-center justify-between text-xs text-[#bcbcbc]">
              <span>Agent status</span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
                Online
              </span>
            </div>
            <div className="mt-3 text-2xl font-semibold text-white">99.97%</div>
            <div className="mt-1 text-xs text-[#9aa0a6]">uptime this week</div>
          </div>
        </aside>

        <main className={`${fitViewport ? 'xl:min-h-0' : ''} min-w-0 flex-1`}>{children}</main>
      </div>
      <MobileTabBar user={user} />
    </div>
  )
}

function AuthCard({ title, subtitle, children, footerText, footerLink, footerLinkText }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.08),transparent_22%),radial-gradient(circle_at_bottom_right,rgba(255,255,255,0.04),transparent_28%),linear-gradient(180deg,#12171b_0%,#090b0d_100%)] px-4">
      <div className="glass-panel w-full max-w-md rounded-[28px] p-7">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-linear-to-br from-white to-[#cfcfcf] text-lg font-bold text-slate-950">H</div>
          <div>
            <div className="text-lg font-semibold text-white">Homie</div>
            <div className="text-sm text-[#9aa0a6]">{subtitle}</div>
          </div>
        </div>

        <div className="mb-6">
          <div className="text-sm uppercase tracking-[0.2em] text-emerald-300">{title}</div>
          <h1 className="mt-2 text-3xl font-semibold text-white">{title === 'Login' ? 'Welcome back' : 'Create account'}</h1>
        </div>

        {children}

        {footerText && (
          <div className="mt-5 text-center text-sm text-[#9aa0a6]">
            {footerText}{' '}
            <Link to={footerLink} className="font-medium text-emerald-300">{footerLinkText}</Link>
          </div>
        )}
      </div>
    </div>
  )
}

// null while loading; the backend decides (ALLOW_REGISTRATION, or no accounts exist yet).
const MIN_PASSWORD_LENGTH = 16

function useRegistrationEnabled() {
  const [config, setConfig] = useState({ enabled: null, bootstrapRequired: false })
  useEffect(() => {
    let active = true
    apiRequest('/auth/config', { method: 'GET' })
      .then((data) => { if (active) setConfig({ enabled: Boolean(data.registration_enabled), bootstrapRequired: Boolean(data.bootstrap_required) }) })
      .catch(() => { if (active) setConfig({ enabled: true, bootstrapRequired: false }) })
    return () => { active = false }
  }, [])
  return config
}

function LoginPage({ onLogin }) {
  const navigate = useNavigate()
  const { enabled: registrationEnabled } = useRegistrationEnabled()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [pendingToken, setPendingToken] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')

  const finish = (data) => {
    const setupRequired = Boolean(data.setup_required)
    onLogin({ id: data.user.id, name: data.user.name, email: data.user.email, role: data.user.role, setup_required: setupRequired })
    navigate(setupRequired ? '/account' : '/')
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')

    try {
      const data = await apiRequest('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })
      if (data.status === 'two_factor_required') {
        setPassword('')
        setPendingToken(data.pending_token)
        return
      }
      finish(data)
    } catch (requestError) {
      setError(requestError.message || 'Invalid email or password.')
    }
  }

  const handleCode = async (event) => {
    event.preventDefault()
    setError('')
    try {
      finish(await apiRequest('/auth/login/2fa', { method: 'POST', body: JSON.stringify({ pending_token: pendingToken, code: code.trim() }) }))
    } catch (requestError) {
      setError(requestError.message || 'Invalid code.')
      // An expired or used-up sign-in can't be retried; start again from the password.
      if (requestError.status === 401 && /sign in again|expired/i.test(requestError.message || '')) { setPendingToken(null); setCode('') }
    }
  }

  if (pendingToken) {
    return (
      <AuthCard title="Two-factor" subtitle="Second step">
        <form onSubmit={handleCode} className="space-y-4">
          <p className="text-sm text-[#bcbcbc]">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
          <label className="block">
            <span className="mb-2 block text-sm text-[#bcbcbc]">Code</span>
            <input
              type="text"
              inputMode="text"
              autoComplete="one-time-code"
              autoFocus
              maxLength={32}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 font-mono text-lg tracking-widest text-white outline-none transition focus:border-emerald-400"
              placeholder="123456"
              required
            />
          </label>
          {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
          <button type="submit" className="flex w-full items-center justify-center rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-3 font-medium text-slate-950 transition hover:brightness-110">Verify</button>
          <button type="button" onClick={() => { setPendingToken(null); setCode(''); setError('') }} className="w-full py-2 text-sm text-[#9aa0a6] hover:text-white">Back to sign in</button>
        </form>
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Login" subtitle="Secure access" footerText={registrationEnabled ? 'Need an account?' : null} footerLink="/register" footerLinkText="Register here">
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-2 block text-sm text-[#bcbcbc]">Email</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 text-white outline-none ring-0 transition focus:border-emerald-400"
            required
          />
        </label>

        <label className="block">
          <span className="mb-2 block text-sm text-[#bcbcbc]">Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 text-white outline-none transition focus:border-emerald-400"
            required
          />
        </label>

        {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}

        <div className="flex items-center justify-between text-sm text-[#9aa0a6]">
          <label className="inline-flex items-center gap-2 py-2">
            <input type="checkbox" defaultChecked className="h-4 w-4 rounded border-neutral-600 bg-neutral-800" />
            Remember me
          </label>
          <a href="#" className="py-2 text-emerald-300">Forgot password?</a>
        </div>

        <button
          type="submit"
          className="mt-2 flex w-full items-center justify-center rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-3 font-medium text-slate-950 transition hover:brightness-110"
        >
          Sign in
        </button>
      </form>
    </AuthCard>
  )
}

function RegisterPage({ onRegister }) {
  const navigate = useNavigate()
  const { enabled: registrationEnabled, bootstrapRequired } = useRegistrationEnabled()
  const [form, setForm] = useState({ name: '', email: '', password: '', bootstrapToken: '' })
  const [error, setError] = useState('')

  const handleChange = (event) => {
    setForm((current) => ({
      ...current,
      [event.target.name]: event.target.value,
    }))
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    const trimmedName = form.name.trim()
    const trimmedEmail = form.email.trim()

    if (!trimmedName || !trimmedEmail || form.password.length < MIN_PASSWORD_LENGTH) {
      setError(`Please provide a name, an email, and a password of at least ${MIN_PASSWORD_LENGTH} characters.`)
      return
    }

    try {
      const data = await apiRequest('/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          name: trimmedName,
          email: trimmedEmail,
          password: form.password,
          bootstrap_token: bootstrapRequired ? form.bootstrapToken.trim() : undefined,
        }),
      })

      onRegister({
        id: data.user.id,
        name: data.user.name,
        email: data.user.email,
        role: data.user.role,
        setup_required: Boolean(data.setup_required),
      })
      navigate(data.setup_required ? '/account' : '/')
    } catch (requestError) {
      setError(requestError.message || 'Unable to create account right now.')
    }
  }

  return (
    <AuthCard title="Register" subtitle="Create account" footerText="Already have an account?" footerLink="/login" footerLinkText="Log in">
      {registrationEnabled === false ? (
        <p className="rounded-2xl border border-neutral-700 bg-[#111417] p-4 text-sm text-[#bcbcbc]">Registration is disabled on this server. Ask the administrator for an account.</p>
      ) : (
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-2 block text-sm text-[#bcbcbc]">Full name</span>
          <input
            type="text"
            name="name"
            value={form.name}
            onChange={handleChange}
            className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 text-white outline-none transition focus:border-emerald-400"
            placeholder="Jane Doe"
            required
          />
        </label>

        <label className="block">
          <span className="mb-2 block text-sm text-[#bcbcbc]">Email</span>
          <input
            type="email"
            name="email"
            value={form.email}
            onChange={handleChange}
            className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 text-white outline-none transition focus:border-emerald-400"
            placeholder="jane@example.com"
            required
          />
        </label>

        <label className="block">
          <span className="mb-2 block text-sm text-[#bcbcbc]">Password</span>
          <input
            type="password"
            name="password"
            value={form.password}
            onChange={handleChange}
            className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 text-white outline-none transition focus:border-emerald-400"
            placeholder={`At least ${MIN_PASSWORD_LENGTH} characters`}
            minLength={MIN_PASSWORD_LENGTH}
            autoComplete="new-password"
            required
          />
          <span className="mt-2 block text-xs text-[#7f8589]">A few random words make a good passphrase, for example “correct horse battery staple”. Up to 72 bytes.</span>
        </label>

        {bootstrapRequired && (
          <label className="block">
            <span className="mb-2 block text-sm text-[#bcbcbc]">Setup token</span>
            <input
              type="password"
              name="bootstrapToken"
              value={form.bootstrapToken}
              onChange={handleChange}
              autoComplete="off"
              className="w-full rounded-2xl border border-neutral-700 bg-neutral-900/70 px-4 py-3 font-mono text-sm text-white outline-none transition focus:border-emerald-400"
              placeholder="hlb_…"
              required
            />
            <span className="mt-2 block text-xs text-[#7f8589]">This install has no accounts yet, so the first one needs its setup token. Find it in the backend log: <code>docker compose logs backend | grep "setup token"</code>, or use the value of <code>ADMIN_BOOTSTRAP_TOKEN</code> if you set one. The first account becomes the admin.</span>
          </label>
        )}

        {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}

        <button
          type="submit"
          className="mt-2 flex w-full items-center justify-center rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-3 font-medium text-slate-950 transition hover:brightness-110"
        >
          Create account
        </button>
      </form>
      )}
    </AuthCard>
  )
}

// Izvan ili na vrhu komponente postavi guard za izbjegavanje paralelnih poziva
let isFetchingOverview = false

// Collectors from before per-mount reporting only send the root filesystem's usage.
function serverMounts(server) {
  if (server.mounts?.length) return server.mounts
  const host = server.host
  return host?.disk_usage_pct != null ? [{ mount: '/', used_pct: host.disk_usage_pct, inodes_pct: host.disk_inodes_pct }] : []
}

function fullestMount(mounts) {
  return mounts.reduce((fullest, mount) => (mount.used_pct != null && (!fullest || mount.used_pct > fullest.used_pct) ? mount : fullest), null)
}

const HOST_HISTORY_MINUTES = 30

// Adds the latest check-in unless it's already there (the dashboard polls faster than collectors report).
function appendHostSample(samples, sample) {
  const last = samples.at(-1)
  if (!sample.time || (last && new Date(sample.time) <= new Date(last.time))) return samples
  const cutoff = new Date(sample.time).getTime() - HOST_HISTORY_MINUTES * 60 * 1000
  return [...samples, sample].filter((item) => new Date(item.time).getTime() >= cutoff)
}

function formatGigabytes(value) {
  if (value == null) return '?'
  return value >= 1024 ? `${(value / 1024).toFixed(1)} TB` : `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} GB`
}

const DISK_LEVEL_STYLES = [
  { bar: 'bg-emerald-400/70', text: 'text-[#cfcfcf]' },
  { bar: 'bg-amber-400/80', text: 'text-amber-200' },
  { bar: 'bg-red-400/80', text: 'text-red-300' },
]

function thresholdLevel(value, warning, critical) {
  if (value == null) return 0
  return value >= critical ? 2 : value >= warning ? 1 : 0
}

function StorageBars({ mounts, thresholds, mountThresholds }) {
  if (!mounts.length) return null
  const t = { disk_warning_pct: 85, disk_critical_pct: 95, inodes_warning_pct: 85, inodes_critical_pct: 95, ...thresholds }
  return (
    <div className="mt-4">
      <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#7f8589]">Storage</div>
      <div className="space-y-2">
        {mounts.map((mount) => {
          const own = mountThresholds?.[mount.mount]
          const style = DISK_LEVEL_STYLES[thresholdLevel(mount.used_pct, own?.disk_warning_pct ?? t.disk_warning_pct, own?.disk_critical_pct ?? t.disk_critical_pct)]
          const inodeLevel = thresholdLevel(mount.inodes_pct, t.inodes_warning_pct, t.inodes_critical_pct)
          const used = Math.min(100, Math.max(0, mount.used_pct ?? 0))
          return (
            <div key={mount.mount} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-sm sm:grid-cols-[minmax(0,12rem)_1fr_auto]">
              <span className="truncate font-mono text-xs text-[#bcbcbc]" title={mount.fs_type ? `${mount.mount} (${mount.fs_type})` : mount.mount}>{mount.mount}</span>
              <div className="h-2 overflow-hidden rounded-full bg-white/5" role="meter" aria-label={`${mount.mount} usage`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={used}>
                <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${used}%` }} />
              </div>
              <span className="whitespace-nowrap text-right text-xs text-[#9aa0a6]">
                {mount.total_gb != null && <span className="hidden sm:inline">{formatGigabytes(mount.used_gb)} / {formatGigabytes(mount.total_gb)} · </span>}
                <span className={`font-medium ${style.text}`}>{mount.used_pct == null ? '—' : `${Math.round(mount.used_pct)}%`}</span>
                {inodeLevel > 0 && <span className={`ml-2 ${DISK_LEVEL_STYLES[inodeLevel].text}`}>inodes {Math.round(mount.inodes_pct)}%</span>}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function readStoredIds(storageKey) {
  try {
    const stored = JSON.parse(window.localStorage.getItem(storageKey))
    return new Set(Array.isArray(stored) ? stored : [])
  } catch {
    return new Set()
  }
}

// A set of ids kept in localStorage; falls back to in-memory state when storage is unavailable.
function useStoredIdSet(storageKey) {
  const [ids, setIds] = useState(() => readStoredIds(storageKey))
  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify([...ids]))
    } catch {
      // Private windows and blocked storage: keep the state for this visit only.
    }
  }, [storageKey, ids])
  const toggle = (id) => setIds((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  return [ids, toggle, (list) => setIds(new Set(list))]
}

// Like useStoredIdSet but forgotten on reload, so these sections always start collapsed.
function useIdSet() {
  const [ids, setIds] = useState(() => new Set())
  const toggle = (id) => setIds((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  return [ids, toggle]
}

function Chevron({ open, className = '' }) {
  return <span aria-hidden="true" className={`inline-block text-[10px] text-[#7f8589] transition-transform ${open ? 'rotate-90' : ''} ${className}`}>▶</span>
}

const CONTAINER_SEARCH_MIN = 10
const byName = (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true })

const CONTAINER_EVENT_LABELS = { container_added: 'Added', container_removed: 'Removed', image_changed: 'Image change', container_notice: 'Notice' }
const CONTAINER_EVENT_CLASSES = { container_added: 'text-emerald-300', container_removed: 'text-amber-300', image_changed: 'text-sky-300', container_notice: 'text-[#9aa0a6]' }

function DockerNotice({ host }) {
  const reasons = { timeout: 'it did not answer in time', refused: 'the connection was refused', missing: 'its socket is missing', denied: 'access was denied', error: 'it returned an error' }
  return <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 px-3 py-2.5 text-sm text-amber-200">Docker is unreachable{host.docker_reason ? `: ${reasons[host.docker_reason] || reasons.error}` : ''}. Container data is not updating until it is back.</div>
}

function ContainerSection({ server, open, onToggle }) {
  const [query, setQuery] = useState('')
  const containers = [...server.containers].sort(byName)
  const count = (predicate) => containers.filter(predicate).length
  const running = count((c) => c.status === 'running')
  const unhealthy = count((c) => c.healthcheck_status === 'unhealthy')
  const restarting = count((c) => c.status === 'restarting')
  const stopped = count((c) => c.status === 'exited' || c.status === 'dead')
  const needle = query.trim().toLowerCase()
  const shown = needle ? containers.filter((c) => `${c.name} ${c.image || ''}`.toLowerCase().includes(needle)) : containers

  return (
    <div className="mt-4 rounded-xl border border-neutral-800">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-3 py-2.5 text-left text-sm transition hover:bg-white/3">
        <Chevron open={open} />
        <span className="text-white">Containers <span className="text-[#7f8589]">({containers.length})</span></span>
        <span className="text-[#9aa0a6]">{running} running</span>
        {unhealthy > 0 && <span className="text-red-300">{unhealthy} unhealthy</span>}
        {restarting > 0 && <span className="text-red-300">{restarting} restarting</span>}
        {stopped > 0 && <span className="text-amber-300">{stopped} stopped</span>}
      </button>
      {open && (
        <div className="border-t border-neutral-800 px-3 pb-2">
          {containers.length > CONTAINER_SEARCH_MIN && <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter by name or image" aria-label={`Filter containers on ${server.hostname}`} className="mt-3 w-full rounded-lg border border-neutral-800 bg-[#0d1013] px-3 py-1.5 text-sm text-[#cfcfcf] outline-none focus:border-white/30" />}
          {shown.length ? <ContainerTable serverId={server.id} containers={shown} /> : <p className="py-3 text-sm text-[#9aa0a6]">No containers match “{query.trim()}”.</p>}
          {server.container_events?.length > 0 && (
            <div className="mt-2 border-t border-neutral-800 pt-3">
              <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#7f8589]">Recent changes · 7 days</div>
              <ul className="space-y-1.5 pb-1 text-sm">{server.container_events.map((event, index) => (
                <li key={`${event.time}-${event.kind}-${index}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                  <span className="w-24 shrink-0 text-xs text-[#7f8589]" title={new Date(event.time).toLocaleString()}>{formatAgo(event.time)}</span>
                  <span className={`w-24 shrink-0 text-xs font-medium ${CONTAINER_EVENT_CLASSES[event.kind] || 'text-[#cfcfcf]'}`}>{CONTAINER_EVENT_LABELS[event.kind] || event.kind}</span>
                  <span className="min-w-0 flex-1 basis-60 break-words text-[#bcbcbc]">{event.detail}</span>
                </li>
              ))}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const TAINT_FAULT_BITS = [4, 5, 7, 9, 14]
const SYSTEM_EVENT_LABELS = { reboot: 'Reboot', oom_kill: 'OOM kill', kernel_fault: 'Kernel fault', kernel_error: 'Kernel error', credentials_replaced: 'Credentials replaced' }
const SYSTEM_EVENT_CLASSES = { reboot: 'text-sky-300', oom_kill: 'text-amber-300', kernel_fault: 'text-red-300', kernel_error: 'text-amber-300', credentials_replaced: 'text-amber-300' }
const CHIP_CLASSES = { ok: 'text-emerald-300', warn: 'text-amber-300', info: 'text-sky-300', unknown: 'text-[#7f8589]' }

function formatAgo(value) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 90) return 'just now'
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

function systemChips(host, events) {
  const oomRecently = events.some((event) => event.kind === 'oom_kill' && Date.now() - new Date(event.time).getTime() < 24 * 3600 * 1000)
  const faulty = host.kernel_tainted != null && TAINT_FAULT_BITS.some((bit) => (host.kernel_tainted >> bit) & 1)
  return [
    ['Booted', host.system_uptime_seconds == null ? 'unknown' : formatAgo(Date.now() - host.system_uptime_seconds * 1000), host.system_uptime_seconds == null ? 'unknown' : events.some((event) => event.kind === 'reboot' && Date.now() - new Date(event.time).getTime() < 3600 * 1000) ? 'info' : 'ok'],
    ['Last shutdown', host.last_shutdown == null || host.last_shutdown === 'unknown' ? 'unknown' : host.last_shutdown, host.last_shutdown === 'clean' ? 'ok' : host.last_shutdown === 'unclean' ? 'warn' : 'unknown'],
    ['Clock', host.ntp_synced == null ? 'unknown' : host.ntp_synced ? 'synced' : 'not synced', host.ntp_synced == null ? 'unknown' : host.ntp_synced ? 'ok' : 'warn'],
    ['OOM kills (24h)', host.oom_kill_total == null ? 'unknown' : oomRecently ? 'yes' : 'none', host.oom_kill_total == null ? 'unknown' : oomRecently ? 'warn' : 'ok'],
    ['Kernel', host.kernel_tainted == null ? 'unknown' : faulty ? 'faults reported' : 'clean', host.kernel_tainted == null ? 'unknown' : faulty ? 'warn' : 'ok'],
    ['Updates', host.reboot_required == null ? 'unknown' : host.reboot_required ? 'reboot needed' : 'applied', host.reboot_required == null ? 'unknown' : host.reboot_required ? 'info' : 'ok'],
  ]
}

function SystemSection({ server, open, onToggle }) {
  const host = server.host
  const events = server.system_events || []
  const chips = systemChips(host, events)
  return (
    <div className="mt-4 rounded-xl border border-neutral-800">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-3 py-2.5 text-left text-sm transition hover:bg-white/3">
        <Chevron open={open} />
        <span className="text-white">System</span>
        {chips.map(([label, value, tone]) => <span key={label} className="whitespace-nowrap text-xs text-[#7f8589]">{label} <span className={`font-medium ${CHIP_CLASSES[tone]}`}>{value}</span></span>)}
      </button>
      {open && (
        <div className="border-t border-neutral-800 px-3 py-3">
          <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#7f8589]">Recent events · 7 days</div>
          {events.length ? <ul className="space-y-1.5 text-sm">{events.map((event, index) => (
            <li key={`${event.time}-${event.kind}-${index}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="w-24 shrink-0 text-xs text-[#7f8589]" title={event.count > 1 ? `Last seen ${new Date(event.time).toLocaleString()}` : new Date(event.time).toLocaleString()}>{formatAgo(event.time)}</span>
              <span className={`w-24 shrink-0 text-xs font-medium ${SYSTEM_EVENT_CLASSES[event.kind] || 'text-[#cfcfcf]'}`}>{SYSTEM_EVENT_LABELS[event.kind] || event.kind}</span>
              <span className="min-w-0 flex-1 basis-60 break-words text-[#bcbcbc]">{event.detail}{event.count > 1 && <span className="ml-2 whitespace-nowrap rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-medium text-[#cfcfcf]" title={`Seen ${event.count} times · first ${formatAgo(event.first_time)}, last ${formatAgo(event.time)}`}>×{event.count}</span>}</span>
            </li>
          ))}</ul> : <p className="text-sm text-[#9aa0a6]">No reboots, OOM kills or kernel faults in the last 7 days.</p>}
          {events.some((event) => event.count > 1) && <p className="mt-2 text-xs text-[#7f8589]">Repeated kernel messages are shown once with a count; the time is when each was last seen.</p>}
          {host.last_shutdown == null && <p className="mt-3 text-xs text-[#7f8589]">“Unknown” means the collector couldn't read that value on this host, not that anything is wrong. Last shutdown, OOM process names and kernel errors need the optional system helper (see SECURITY.md).</p>}
        </div>
      )}
    </div>
  )
}

const NETWORK_EVENT_LABELS = { port_opened: 'Port opened', port_closed: 'Port closed', firewall_changed: 'Firewall', firewall_weakened: 'Firewall', network_notice: 'Notice' }
const NETWORK_EVENT_CLASSES = { port_opened: 'text-amber-300', port_closed: 'text-[#9aa0a6]', firewall_changed: 'text-sky-300', firewall_weakened: 'text-red-300', network_notice: 'text-[#9aa0a6]' }
const LISTENER_SCOPE_LABELS = { loopback: 'loopback only', any: 'all interfaces', specific: 'one address' }
const INTERNET_LABELS = { ok: ['reachable', 'ok'], degraded: ['partly reachable', 'warn'], dns_failed: ['DNS failing', 'warn'], down: ['down', 'warn'] }

function networkChips(server) {
  const host = server.host
  const network = server.network || { listeners: [], firewall: [], events: [] }
  const internet = INTERNET_LABELS[host.internet_status]
  const exposed = network.listeners.filter((listener) => listener.scope !== 'loopback').length
  const ufw = network.firewall.find((file) => file.name === 'ufw-conf')
  const ruleFiles = network.firewall.filter((file) => file.name !== 'ufw-conf' && file.rules > 0)
  const firewall = [ufw ? (ufw.enabled === true ? 'ufw enabled' : ufw.enabled === false ? 'ufw disabled' : null) : null, ...ruleFiles.map((file) => `${file.label.split(' ')[0]} ${file.rules} rules`)].filter(Boolean)
  return [
    ['Internet', internet ? internet[0] : 'not checked', internet ? internet[1] : 'unknown'],
    ['Listening', network.listeners.length ? `${network.listeners.length} ports · ${exposed} exposed` : 'unknown', network.listeners.length ? 'ok' : 'unknown'],
    ['Firewall', firewall.length ? firewall.join(' · ') : 'no config found', ufw?.enabled === false ? 'warn' : firewall.length ? 'ok' : 'unknown'],
  ]
}

function NetworkSection({ server, open, onToggle }) {
  const network = server.network || { listeners: [], firewall: [], events: [] }
  const chips = networkChips(server)
  const exposed = network.listeners.filter((listener) => listener.scope !== 'loopback')
  const local = network.listeners.filter((listener) => listener.scope === 'loopback')
  const listenerLine = (listener) => <li key={`${listener.proto}-${listener.port}-${listener.scope}`} className="whitespace-nowrap rounded-md bg-white/5 px-2 py-1 font-mono text-xs text-[#cfcfcf]" title={LISTENER_SCOPE_LABELS[listener.scope]}>{listener.proto}/{listener.port}</li>
  return (
    <div className="mt-4 rounded-xl border border-neutral-800">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-3 py-2.5 text-left text-sm transition hover:bg-white/3">
        <Chevron open={open} />
        <span className="text-white">Network</span>
        {chips.map(([label, value, tone]) => <span key={label} className="whitespace-nowrap text-xs text-[#7f8589]">{label} <span className={`font-medium ${CHIP_CLASSES[tone]}`}>{value}</span></span>)}
      </button>
      {open && (
        <div className="space-y-4 border-t border-neutral-800 px-3 py-3">
          {network.listeners.length > 0 && <div>
            <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#7f8589]">Listening ports</div>
            {exposed.length > 0 && <div className="mb-2"><div className="mb-1 text-xs text-amber-300">Reachable from other machines ({exposed.length})</div><ul className="flex flex-wrap gap-1.5">{exposed.map(listenerLine)}</ul></div>}
            {local.length > 0 && <div><div className="mb-1 text-xs text-[#7f8589]">This machine only ({local.length})</div><ul className="flex flex-wrap gap-1.5">{local.map(listenerLine)}</ul></div>}
          </div>}
          <div>
            <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#7f8589]">Recent changes · 7 days</div>
            {network.events.length ? <ul className="space-y-1.5 text-sm">{network.events.map((event, index) => (
              <li key={`${event.time}-${event.kind}-${index}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <span className="w-24 shrink-0 text-xs text-[#7f8589]" title={new Date(event.time).toLocaleString()}>{formatAgo(event.time)}</span>
                <span className={`w-24 shrink-0 text-xs font-medium ${NETWORK_EVENT_CLASSES[event.kind] || 'text-[#cfcfcf]'}`}>{NETWORK_EVENT_LABELS[event.kind] || event.kind}</span>
                <span className="min-w-0 flex-1 basis-60 break-words text-[#bcbcbc]">{event.detail}</span>
              </li>
            ))}</ul> : <p className="text-sm text-[#9aa0a6]">No new listeners or firewall changes in the last 7 days.</p>}
          </div>
          <p className="text-xs text-[#7f8589]">The firewall chip reads configuration files, not the live ruleset. The internet check is off unless <code>HOMELAB_CONNECTIVITY_TARGETS</code> is set on that machine's collector.</p>
        </div>
      )}
    </div>
  )
}

const HEALTH_CLASSES = { healthy: 'text-[#7f8589]', unhealthy: 'text-red-300', starting: 'text-amber-300' }

const ALERT_STYLES = {
  CRITICAL: { box: 'border-red-400/25 bg-red-500/5', badge: 'bg-red-500/15 text-red-300', title: 'text-red-200' },
  WARNING: { box: 'border-amber-400/20 bg-amber-400/5', badge: 'bg-amber-400/15 text-amber-300', title: 'text-amber-200' },
  INFO: { box: 'border-sky-400/20 bg-sky-400/5', badge: 'bg-sky-400/15 text-sky-300', title: 'text-sky-200' },
}

function DashboardPage({ user, onLogout }) {
  const [overview, setOverview] = useState({ servers: [], alerts: [] })
  const [error, setError] = useState('')
  const [showConnect, setShowConnect] = useState(false)
  const [deletingServerId, setDeletingServerId] = useState(null)
  // Recent samples of the machine running the dashboard: loaded from the backend, then extended live.
  const [history, setHistory] = useState({ serverId: null, samples: [] })

const refresh = async () => {
  // Ako je zahtjev već u tijeku, preskoči ovaj poziv
  if (isFetchingOverview) return
  isFetchingOverview = true

  try {
    const data = await apiRequest('/monitoring/overview', { method: 'GET' })
    setOverview(data)
    setError('')
    
    const host = (data.servers || []).find((server) => server.is_dashboard_host)
    if (host?.host) {
      const sample = { time: host.host.time, cpu: host.host.cpu_utilization_pct, ram: host.host.ram_pct, disk: host.host.disk_usage_pct }
      setHistory((current) => current.serverId === host.id ? { ...current, samples: appendHostSample(current.samples, sample) } : current)
    }
  } catch (requestError) { 
    // Ako backend vrati 401 (neautoriziran/odjavljen korisnik), odjavi ga i prekini petlju
    if (requestError.status === 401 || requestError.message?.includes('401')) {
      if (typeof onLogout === 'function') {
        onLogout()
      }
      return
    }
    setError(requestError.message || 'Could not load monitoring data.') 
  } finally {
    // Oslobodi guard za sljedeći interval
    isFetchingOverview = false
  }
}

    // ✅ ISPRAVAK: Koristimo primitive `user?.id` umjesto cijelog `user` objekta
  useEffect(() => {
    if (!user) return

    // Inicijalno učitavanje
    refresh()

    // No polling while the tab is hidden; refresh straight away when it comes back.
    let timer = null
    const start = () => { if (timer === null) timer = setInterval(refresh, LIVE_GRAPH_REFRESH_INTERVAL_SECONDS * 1000) }
    const stop = () => { if (timer !== null) { clearInterval(timer); timer = null } }
    const onVisibilityChange = () => {
      if (document.hidden) { stop(); return }
      refresh()
      start()
    }
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibilityChange)

    // Obavezno čišćenje timera pri unmountu
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [user?.id]) // <-- Stabilna instanca, okida se samo pri promjeni ID-a

  const servers = overview.servers || []
  // Remembered per browser: which server cards are collapsed and which container lists are open.
  const [collapsedServers, toggleServer, setCollapsedServers] = useStoredIdSet('homelab.collapsedServers')
  const [openContainerLists, toggleContainerList] = useIdSet()
  const [openSystemLists, toggleSystemList] = useIdSet()
  const [openNetworkLists, toggleNetworkList] = useIdSet()
  const allCollapsed = servers.length > 0 && servers.every((server) => collapsedServers.has(server.id))
  const dashboardHost = servers.find((server) => server.is_dashboard_host)
  const dashboardHostId = dashboardHost?.id

  useEffect(() => {
    if (!dashboardHostId) return
    let cancelled = false
    apiRequest(`/monitoring/servers/${dashboardHostId}/history?minutes=${HOST_HISTORY_MINUTES}`, { method: 'GET' })
      .then((data) => { if (!cancelled) setHistory({ serverId: dashboardHostId, samples: data.samples }) })
      .catch(() => { if (!cancelled) setHistory({ serverId: dashboardHostId, samples: [] }) })
    return () => { cancelled = true }
  }, [dashboardHostId])

  const changeInterval = async (server, value) => {
    try {
      await apiRequest(`/monitoring/servers/${server.id}/interval`, { method: 'PATCH', body: JSON.stringify({ check_interval_seconds: Number(value) }) })
      await refresh()
    } catch (requestError) { setError(requestError.message || 'Could not update interval.') }
  }
  const deleteServer = async (server) => {
    if (!window.confirm(`Delete ${server.hostname} and all its stored metrics and incidents? This cannot be undone.`)) return
    setDeletingServerId(server.id)
    try {
      await apiRequest(`/monitoring/servers/${server.id}`, { method: 'DELETE' })
      await refresh()
    } catch (requestError) {
      setError(requestError.message || 'Could not delete server.')
    } finally {
      setDeletingServerId(null)
    }
  }
  const online = servers.filter((server) => server.last_seen_at && Date.now() - new Date(server.last_seen_at).getTime() < server.check_interval_seconds * 3000).length
  const hostSamples = history.serverId === dashboardHostId ? history.samples : []
  const hostMetric = dashboardHost?.host
  const runningContainers = servers.reduce((sum, server) => sum + server.containers.filter((container) => container.status === 'running').length, 0)

  // Regular users get a read-only view; the backend enforces the same rules.
  const isAdmin = user?.role === 'admin'

  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="flex flex-col gap-5">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div><div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Infrastructure overview</div><h1 className="mt-1 text-3xl font-semibold text-white">Dashboard</h1><p className="mt-2 text-sm text-[#9aa0a6]">Live host and container health · updated every {LIVE_GRAPH_REFRESH_INTERVAL_SECONDS} seconds.</p></div>
            {isAdmin && <button type="button" onClick={() => setShowConnect((current) => !current)} aria-expanded={showConnect} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950">{showConnect ? 'Close' : 'Connect a server'}</button>}
          </div>
          {error && <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
          {isAdmin && showConnect && <ConnectServerPanel />}
        </header>

        <section className="grid grid-cols-3 gap-2 sm:gap-4">
          {[
            ['Servers online', `${online} / ${servers.length}`, 'Based on last check-in'],
            ['Containers running', String(runningContainers), 'Across monitored servers'],
            ['Active incidents', String(overview.alert_count ?? overview.alerts?.length ?? 0), 'Needs attention'],
          ].map(([label, value, detail]) => <div key={label} className="glass-panel rounded-2xl p-3 sm:rounded-[24px] sm:p-5"><div className="text-xs leading-4 text-[#9aa0a6] sm:text-sm">{label}</div><div className="mt-2 text-2xl font-semibold text-white sm:mt-3 sm:text-3xl">{value}</div><div className="mt-2 hidden text-xs text-[#7f8589] sm:block">{detail}</div></div>)}
        </section>

        <section>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 px-1">
            <div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">This machine{dashboardHost && <span className="ml-2 normal-case tracking-normal text-[#cfcfcf]">{dashboardHost.hostname}</span>}</div>
            <span className="text-xs text-[#7f8589]">Last {HOST_HISTORY_MINUTES} minutes</span>
          </div>
          {dashboardHost ? <div className="grid gap-4 xl:grid-cols-3">
            {[
              { key: 'cpu', title: 'CPU utilization', color: '#34d399', latest: hostMetric?.cpu_utilization_pct },
              { key: 'ram', title: 'Memory utilization', color: '#60a5fa', latest: hostMetric?.ram_pct },
              { key: 'disk', title: 'Disk usage (/)', color: '#c084fc', latest: hostMetric?.disk_usage_pct },
            ].map((chart) => <MetricTrend key={chart.key} title={chart.title} color={chart.color} value={chart.latest == null ? '—' : Number(chart.latest).toFixed(1)} points={hostSamples.map((sample) => sample[chart.key])} times={hostSamples.map((sample) => sample.time)} />)}
          </div> : <div className="glass-panel rounded-[24px] p-5 text-sm text-[#9aa0a6]">The machine running the dashboard isn't reporting yet. Its collector starts with the dashboard's compose stack and shows up here once it's added under <span className="text-white">New servers detected</span>.</div>}
        </section>

        <section className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="mb-4 flex items-center justify-between"><div><div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Fleet</div><h2 className="mt-1 text-xl font-semibold text-white">Monitored servers</h2></div><div className="flex items-center gap-3"><span className="text-sm text-[#9aa0a6]">{servers.length} configured</span>{servers.length > 1 && <button type="button" onClick={() => setCollapsedServers(allCollapsed ? [] : servers.map((server) => server.id))} className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-[#cfcfcf] transition hover:bg-white/10">{allCollapsed ? 'Expand all' : 'Collapse all'}</button>}</div></div>
          {isAdmin && <PendingServers pending={overview.pending_collectors || []} onChanged={refresh} />}
          {!servers.length ? <div className="rounded-2xl border border-dashed border-neutral-700 p-8 text-center"><div className="text-lg font-medium text-white">No servers registered</div><p className="mt-2 text-sm text-[#9aa0a6]">{isAdmin ? <>Click <span className="text-white">Connect a server</span> above and run the command on each machine you want to monitor.</> : 'An admin needs to connect a server before it shows up here.'}</p></div> : <div className="space-y-3">{servers.map((server) => {
            const metric = server.host
            const mounts = serverMounts(server)
            const fullest = fullestMount(mounts)
            const fresh = server.last_seen_at && Date.now() - new Date(server.last_seen_at).getTime() < server.check_interval_seconds * 3000
            const collapsed = collapsedServers.has(server.id)
            const alertBadge = server.active_alert_count > 0 && <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-amber-300">{server.active_alert_count} {server.active_alert_count === 1 ? 'incident' : 'incidents'}</span>
            return <article key={server.id} className={`rounded-2xl border border-neutral-800 bg-[#0d1013]/70 ${collapsed ? 'px-4 py-3 md:px-5' : 'p-4 md:p-5'}`}>
              <div className={`flex flex-wrap justify-between gap-3 ${collapsed ? 'items-center' : 'items-start'}`}>
                <button type="button" onClick={() => toggleServer(server.id)} aria-expanded={!collapsed} className="flex min-w-0 max-w-full items-start gap-2 py-1 text-left">
                  <Chevron open={!collapsed} className="mt-2" />
                  <span className="min-w-0">
                    <span className="flex min-w-0 items-center gap-2"><span aria-label={fresh ? 'Online' : 'Offline'} className={`h-2.5 w-2.5 shrink-0 rounded-full ${fresh ? 'bg-emerald-400' : 'bg-neutral-600'}`} /><span className="truncate text-lg font-medium text-white" title={server.hostname}>{server.hostname}</span></span>
                    {(server.is_dashboard_host || alertBadge) && <span className="mt-1.5 flex flex-wrap items-center gap-2">{server.is_dashboard_host && <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] uppercase tracking-wider text-[#cfcfcf]">This machine</span>}{alertBadge}</span>}
                    {!collapsed && <span className="mt-1 block text-sm text-[#9aa0a6]">{server.os_info || server.ip_address || 'Waiting for first collector check-in'}{server.cpu_cores ? ` · ${server.cpu_cores} cores` : ''}</span>}
                  </span>
                </button>
                {collapsed ? (metric ? <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#9aa0a6]">{[['CPU', metric.cpu_utilization_pct], ['RAM', metric.ram_pct], [fullest ? `Disk ${fullest.mount}` : 'Disk', fullest?.used_pct]].map(([label, value]) => <span key={label} className="whitespace-nowrap">{label} <span className="font-medium text-white">{value == null ? '—' : `${Math.round(value)}%`}</span></span>)}</div> : <span className="text-sm text-[#7f8589]">No measurement yet</span>)
                  : isAdmin ? <div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2 text-xs text-[#9aa0a6]">Check interval<select value={server.check_interval_seconds} onChange={(e) => changeInterval(server, e.target.value)} className="min-h-10 sm:min-h-0 rounded-lg border border-neutral-700 bg-[#111417] px-2 py-1.5 text-sm text-white"><option value="15">15 sec</option><option value="30">30 sec</option><option value="60">1 min</option><option value="300">5 min</option><option value="900">15 min</option></select></label><button type="button" onClick={() => deleteServer(server)} disabled={deletingServerId === server.id} className="min-h-10 sm:min-h-0 rounded-lg border border-red-400/30 bg-red-500/5 px-3 py-1.5 text-xs text-red-300 transition hover:bg-red-500/10 disabled:opacity-50">{deletingServerId === server.id ? 'Deleting…' : 'Delete server'}</button></div> : <span className="text-xs text-[#9aa0a6]">Checks every {server.check_interval_seconds < 60 ? `${server.check_interval_seconds} sec` : `${Math.round(server.check_interval_seconds / 60)} min`}</span>}
              </div>
              {!collapsed && <>
                {metric ? <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">{[['CPU', metric.cpu_utilization_pct, '%'], ['RAM', metric.ram_pct, '%'], [fullest ? `Disk · ${fullest.mount}` : 'Disk', fullest?.used_pct, '%'], ['Load (1m)', metric.load_1m, '']].map(([label, value, unit]) => <div key={label} className="rounded-xl bg-[#171b1f] p-3"><div className="truncate text-xs text-[#9aa0a6]" title={label}>{label}</div><div className="mt-1 text-xl font-semibold text-white">{value == null ? '—' : `${Number(value).toFixed(1)}${unit}`}</div></div>)}</div> : <div className="mt-4 rounded-xl bg-[#171b1f] p-4 text-sm text-[#9aa0a6]">Collector has not sent a measurement yet.</div>}
                {metric && <StorageBars mounts={mounts} thresholds={overview.disk_thresholds} mountThresholds={overview.mount_thresholds} />}
                {metric?.docker_status === 'unreachable' && <DockerNotice host={metric} />}
                {metric && <SystemSection server={server} open={openSystemLists.has(server.id)} onToggle={() => toggleSystemList(server.id)} />}
                {metric && <NetworkSection server={server} open={openNetworkLists.has(server.id)} onToggle={() => toggleNetworkList(server.id)} />}
                {server.containers.length > 0 && <ContainerSection server={server} open={openContainerLists.has(server.id)} onToggle={() => toggleContainerList(server.id)} />}
                <div className="mt-3 text-xs text-[#7f8589]">Last check: {server.last_seen_at ? new Date(server.last_seen_at).toLocaleString() : 'Never'}</div>
              </>}
            </article>
          })}</div>}
        </section>
        <section className="glass-panel rounded-[28px] p-5 md:p-6"><div className="mb-4"><div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Alerts</div><h2 className="mt-1 text-xl font-semibold text-white">Active incidents</h2></div>{overview.alerts?.length ? <div className="space-y-2">{overview.alerts.map((alert) => { const style = ALERT_STYLES[alert.severity] || ALERT_STYLES.WARNING; return <div key={alert.id} className={`rounded-xl border p-3 ${style.box}`}><div className="flex items-center gap-2"><span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wider ${style.badge}`}>{alert.severity}</span><div className={`font-medium ${style.title}`}>{alert.title}</div></div><div className="mt-1 text-xs text-[#9aa0a6]">{alert.hostname || `Server ${alert.server_id}`} · since {new Date(alert.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</div><div className="mt-1 text-sm text-[#bcbcbc]">{alert.description}</div></div> })}{overview.alert_count > overview.alerts.length ? <p className="text-xs text-[#9aa0a6]">Showing the {overview.alerts.length} most severe of {overview.alert_count} incidents.</p> : null}</div> : <p className="text-sm text-[#9aa0a6]">No active incidents recorded.</p>}</section>
      </div>
    </AppShell>
  )
}

function CopyableCommand({ label, text }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between gap-2 text-xs text-[#9aa0a6]"><span>{label}</span><button type="button" onClick={copy} className="rounded-lg border border-neutral-700 px-2 py-1 text-[11px] text-[#cfcfcf] hover:bg-white/5">{copied ? 'Copied' : 'Copy'}</button></div>
      <pre className="custom-scroll mt-1 overflow-x-auto rounded-xl bg-[#090b0d] p-3 text-xs text-[#cfcfcf]">{text}</pre>
    </div>
  )
}

function EnrollmentKeySettings() {
  const [keyInfo, setKeyInfo] = useState(null)
  const [enrollKey, setEnrollKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmRevoke, setConfirmRevoke] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    apiRequest('/monitoring/enrollment-key', { method: 'GET' })
      .then(setKeyInfo)
      .catch((requestError) => setError(requestError.message || 'Could not load enrollment key.'))
  }, [])

  const generateKey = async () => {
    setBusy(true); setError('')
    try {
      const data = await apiRequest('/monitoring/enrollment-key', { method: 'POST' })
      setEnrollKey(data.enroll_key)
      setKeyInfo({ exists: true, created_at: data.created_at })
    } catch (requestError) { setError(requestError.message || 'Could not create enrollment key.') }
    finally { setBusy(false) }
  }

  const revokeKey = async () => {
    setBusy(true); setError('')
    try {
      await apiRequest('/monitoring/enrollment-key', { method: 'DELETE' })
      setEnrollKey(''); setKeyInfo({ exists: false, created_at: null }); setConfirmRevoke(false)
    } catch (requestError) { setError(requestError.message || 'Could not revoke enrollment key.') }
    finally { setBusy(false) }
  }

  return (
    <div className="mt-3">
      <p className="text-xs text-[#9aa0a6]">For scripted installs where nobody clicks Add: collectors started with <code>-e HOMELAB_ENROLL_KEY=…</code> join immediately. Servers already connected are unaffected by rotating or revoking.</p>
      {error && <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
      {!keyInfo ? <p className="mt-3 text-sm text-[#9aa0a6]">Loading…</p> : enrollKey ? (
        <>
          <p className="mt-3 text-xs text-amber-100">Copy this key now. It is shown only once.</p>
          <CopyableCommand label="Enrollment key" text={enrollKey} />
          <CopyableCommand label="Add to the command above" text={`-e HOMELAB_ENROLL_KEY=${enrollKey}`} />
        </>
      ) : keyInfo.exists ? (
        <p className="mt-3 text-sm text-[#bcbcbc]">A key was created {new Date(keyInfo.created_at).toLocaleString()}. Rotate it to see a new one.</p>
      ) : null}
      {keyInfo && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={generateKey} disabled={busy} className="rounded-lg border border-neutral-700 px-3 py-1.5 text-xs text-[#cfcfcf] disabled:opacity-50">{keyInfo.exists ? 'Rotate key' : 'Generate key'}</button>
          {keyInfo.exists && (confirmRevoke ? (
            <>
              <button type="button" onClick={revokeKey} disabled={busy} className="rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-300 disabled:opacity-50">Yes, revoke</button>
              <button type="button" onClick={() => setConfirmRevoke(false)} className="rounded-lg border border-neutral-700 px-3 py-1.5 text-xs text-[#cfcfcf]">Cancel</button>
            </>
          ) : <button type="button" onClick={() => setConfirmRevoke(true)} disabled={busy} className="rounded-lg border border-red-400/30 bg-red-500/5 px-3 py-1.5 text-xs text-red-300 disabled:opacity-50">Revoke key</button>)}
        </div>
      )}
    </div>
  )
}

function buildJoinCommand({ url, token, collectorImage, proxyImage }) {
  return `docker compose -p homelab-collector -f - up -d <<'EOF'
services:
  collector:
    image: ${collectorImage}
    container_name: homelab-collector
    restart: unless-stopped
    environment:
      HOMELAB_API_URL: ${url}
      HOMELAB_JOIN_TOKEN: ${token}
      HOMELAB_DOCKER_HOST: /run/docker-proxy/docker.sock
    volumes:
      - /proc:/host/proc:ro
      - /:/hostfs:ro
      - collector_state:/var/lib/homelab-collector
      - docker_proxy_socket:/run/docker-proxy:ro
    cap_drop: [ALL]
    read_only: true
    security_opt: [no-new-privileges:true]
  docker-proxy:
    image: ${proxyImage}
    container_name: homelab-docker-proxy
    restart: unless-stopped
    user: "0:0"
    network_mode: none
    cap_drop: [ALL]
    read_only: true
    security_opt: [no-new-privileges:true]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - docker_proxy_socket:/run/docker-proxy
volumes:
  collector_state:
  docker_proxy_socket:
EOF`
}

function ConnectServerPanel() {
  const [info, setInfo] = useState(null)
  const [apiUrl, setApiUrl] = useState('')
  const [token, setToken] = useState(null)
  const [expiresAt, setExpiresAt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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
      setToken(data.join_token)
      setExpiresAt(Date.now() + data.expires_in_seconds * 1000)
    } catch (requestError) { setError(requestError.message || 'Could not create a join token.') }
    finally { setBusy(false) }
  }

  const url = apiUrl.trim().replace(/\/$/, '')
  const isLocalhost = /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(url)
  const ready = info?.collector_image && info?.proxy_image
  const minutes = expiresAt ? Math.max(1, Math.round((expiresAt - Date.now()) / 60000)) : null
  const command = token && ready ? buildJoinCommand({ url, token, collectorImage: info.collector_image, proxyImage: info.proxy_image }) : null

  return (
    <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4">
      <div className="font-medium text-cyan-100">Connect a server</div>
      <p className="mt-1 text-sm text-[#bcbcbc]">Generate a command, run it once on the machine you want to monitor, then click <span className="text-white">Add</span> when it appears below under <span className="text-white">New servers detected</span>. The command carries a single-use token that expires, so only you can add servers.</p>

      <label className="mt-4 block text-xs text-[#9aa0a6]">Address the new machine will use to reach the dashboard
        <input value={apiUrl} onChange={(e) => { setApiUrl(e.target.value); setToken(null) }} className="mt-1 w-full rounded-xl border border-neutral-700 bg-[#0d1013] px-3 py-2 text-sm text-white" aria-describedby="api-url-hint" />
      </label>
      {info?.collector_url
        ? <p id="api-url-hint" className="mt-1 text-[11px] text-[#7f8589]">Your configured collector (VPN) address.</p>
        : info?.strict
          ? <p id="api-url-hint" className="mt-1 text-[11px] text-amber-200">Collectors can't use the public address. Set <code>COLLECTOR_PUBLIC_URL</code> to the collector listener on your VPN (for example <code>http://10.8.0.1:8081/api</code>).</p>
          : isLocalhost
            ? <p id="api-url-hint" className="mt-1 text-[11px] text-amber-200">Other machines can't reach <code>localhost</code>. Use this machine's LAN IP, VPN address or domain (e.g. <code>http://192.168.1.10:5173/api</code>).</p>
            : <p id="api-url-hint" className="mt-1 text-[11px] text-[#7f8589]">Prefilled with the address you opened this dashboard at. Over plain HTTP, use it only on a trusted LAN or VPN.</p>}

      {info && !ready && <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">No collector images are configured, so there is nothing safe to install from. Set <code>COLLECTOR_IMAGE</code> and <code>DOCKER_PROXY_IMAGE</code> in <code>.env</code> to your published, digest-pinned images (see SECURITY.md). The dashboard never serves collector code itself.</div>}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={generate} disabled={busy || !ready || !url} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-40">{busy ? 'Generating…' : token ? 'Generate a new command' : 'Generate command'}</button>
        {token && <span className="text-xs text-[#9aa0a6]">Single use · expires in about {minutes} min. A new command makes this one's token stop working once it is used.</span>}
      </div>
      {error && <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}

      {command && <>
        <CopyableCommand label="Run on the new machine (needs Docker with the compose plugin)" text={command} />
        <p className="mt-2 text-[11px] text-[#7f8589]">Installs the collector with no Docker socket and no capabilities; a separate read-only proxy is the only container that touches the socket. The machine running this dashboard is detected automatically.</p>
      </>}

      <details className="mt-4 rounded-xl border border-neutral-800 p-3">
        <summary className="cursor-pointer text-xs text-[#9aa0a6]">Advanced: skip approval with an enrollment key</summary>
        <EnrollmentKeySettings />
      </details>
    </div>
  )
}

function PendingServers({ pending, onChanged }) {
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState('')
  const waiting = pending.filter((item) => item.status === 'pending')
  const dismissed = pending.filter((item) => item.status === 'dismissed')
  if (!pending.length) return null

  const act = async (item, action) => {
    // Approving a machine that matches an existing server replaces that server's credentials and signs out its collector.
    if (action === 'approve' && item.replaces && !window.confirm(`This machine matches the existing server "${item.replaces.hostname}". Adding it replaces that server's credentials and signs its current collector out. Only continue if you are re-installing that machine.`)) return
    setBusyId(item.id); setError('')
    try {
      await apiRequest(`/monitoring/pending-collectors/${item.id}/${action}`, { method: 'POST', body: JSON.stringify({ replace: Boolean(item.replaces) }) })
      await onChanged()
    } catch (requestError) { setError(requestError.message || 'Could not update the server.') }
    finally { setBusyId(null) }
  }

  const row = (item) => (
    <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-neutral-800 bg-[#0d1013]/70 p-3">
      <div>
        <div className="flex flex-wrap items-center gap-2"><span className="font-medium text-white">{item.hostname}</span>{item.verified ? <span className="rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-emerald-300">JOIN TOKEN</span> : <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-amber-300">NO TOKEN</span>}</div>
        {item.replaces && <div className="mt-1 text-xs text-amber-200">Replaces the existing server “{item.replaces.hostname}”: its current collector will be signed out.</div>}
        <div className="mt-0.5 text-xs text-[#9aa0a6]">{[item.ip_address, item.os_info].filter(Boolean).join(' · ')} · code <span className="font-mono text-[#cfcfcf]">{item.pairing_code}</span></div>
      </div>
      <div className="flex w-full gap-2 sm:w-auto">
        <button type="button" onClick={() => act(item, 'approve')} disabled={busyId === item.id} className="min-h-10 flex-1 rounded-lg bg-white px-3 py-1.5 text-xs sm:min-h-0 sm:flex-none font-medium text-slate-950 disabled:opacity-50">{busyId === item.id ? 'Adding…' : 'Add'}</button>
        {item.status === 'pending' && <button type="button" onClick={() => act(item, 'dismiss')} disabled={busyId === item.id} className="min-h-10 flex-1 rounded-lg border border-neutral-700 px-3 py-1.5 text-xs sm:min-h-0 sm:flex-none text-[#cfcfcf] disabled:opacity-50">Dismiss</button>}
      </div>
    </div>
  )

  return (
    <div className="mb-4">
      {waiting.length > 0 && (
        <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4">
          <div className="text-sm font-medium text-cyan-100">New servers detected</div>
          <p className="mt-1 text-xs text-[#9aa0a6]">These machines are running the collector and asking to join. The pairing code is also printed in the collector's log if you want to double-check.</p>
          <div className="mt-3 space-y-2">{waiting.map(row)}</div>
        </div>
      )}
      {dismissed.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-xs text-[#7f8589]">Dismissed ({dismissed.length})</summary>
          <div className="mt-2 space-y-2">{dismissed.map(row)}</div>
        </details>
      )}
      {error && <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
    </div>
  )
}

function formatMegabytes(value) {
  if (value == null) return '—'
  return value >= 1024 ? `${(value / 1024).toFixed(2)} GB` : `${Number(value).toFixed(1)} MB`
}

function formatUptime(startedAt) {
  const started = startedAt ? new Date(startedAt).getTime() : NaN
  if (Number.isNaN(started) || started <= 0) return '—'
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000))
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`
}

function ContainerStatus({ container }) {
  const stopped = container.status === 'exited' || container.status === 'dead'
  return <span className="whitespace-nowrap"><span className={container.status === 'running' ? 'text-emerald-300' : container.status === 'restarting' ? 'text-red-300' : 'text-amber-300'}>{container.status}</span>{stopped && container.exit_code != null && <span className={container.oom_killed ? 'text-red-300' : container.exit_code === 0 ? 'text-[#7f8589]' : 'text-amber-300'}> · {container.oom_killed ? 'out of memory' : `code ${container.exit_code}`}</span>}{container.healthcheck_status && <span className={HEALTH_CLASSES[container.healthcheck_status] || 'text-[#7f8589]'}> · {container.healthcheck_status}</span>}</span>
}

const formatContainerCpu = (c) => (c.cpu_pct == null ? '—' : `${c.cpu_pct}%`)
const formatContainerMemory = (c) => (c.memory_used_mb == null ? '—' : c.memory_used_mb >= 1024 ? `${(c.memory_used_mb / 1024).toFixed(1)} GB` : `${Math.round(c.memory_used_mb)} MB`)

function ContainerTable({ serverId, containers }) {
  const [expandedId, setExpandedId] = useState(null)
  const toggle = (containerId) => setExpandedId((current) => (current === containerId ? null : containerId))

  return <>
    {/* Phones: one tappable two-line row per container. */}
    <ul className="divide-y divide-neutral-800 md:hidden">
      {containers.map((c) => {
        const expanded = expandedId === c.container_id
        return (
          <li key={c.container_id}>
            <button type="button" onClick={() => toggle(c.container_id)} aria-expanded={expanded} className="flex w-full flex-col gap-1 py-3 text-left">
              <span className="flex w-full items-center justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2"><Chevron open={expanded} /><span className="truncate text-sm text-white">{c.name}</span></span>
                <span className="text-xs"><ContainerStatus container={c} /></span>
              </span>
              <span className="flex gap-4 pl-[18px] text-xs text-[#7f8589]">
                <span>CPU <span className="text-[#cfcfcf]">{formatContainerCpu(c)}</span></span>
                <span>Mem <span className="text-[#cfcfcf]">{formatContainerMemory(c)}</span></span>
                <span>Restarts <span className={c.restart_count ? 'text-amber-300' : 'text-[#cfcfcf]'}>{c.restart_count}</span></span>
              </span>
            </button>
            {expanded && <div className="pb-4"><ContainerDetails serverId={serverId} container={c} /></div>}
          </li>
        )
      })}
    </ul>

    <div className="hidden overflow-x-auto md:block">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-[#7f8589]"><tr><th className="py-2">Container</th><th>Status</th><th>CPU</th><th>Memory</th><th>Restarts</th></tr></thead>
        <tbody>
          {containers.map((c) => {
            const expanded = expandedId === c.container_id
            return [
              <tr
                key={c.container_id}
                onClick={() => toggle(c.container_id)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(c.container_id) } }}
                tabIndex={0}
                aria-expanded={expanded}
                className={`cursor-pointer border-t border-neutral-800 text-[#cfcfcf] transition hover:bg-white/3 focus:outline-none focus-visible:bg-white/5 ${expanded ? 'bg-white/3' : ''}`}
              >
                <td className="py-2"><Chevron open={expanded} className="mr-2" />{c.name}</td>
                <td><ContainerStatus container={c} /></td>
                <td>{formatContainerCpu(c)}</td>
                <td>{formatContainerMemory(c)}</td>
                <td>{c.restart_count}</td>
              </tr>,
              expanded && (
                <tr key={`${c.container_id}-details`}>
                  <td colSpan={5} className="pb-4"><ContainerDetails serverId={serverId} container={c} /></td>
                </tr>
              ),
            ]
          })}
        </tbody>
      </table>
    </div>
  </>
}

function ContainerDetails({ serverId, container }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  // Re-fetch whenever the overview poll brings in a newer sample for this container.
  const sampleTime = container.time

  useEffect(() => {
    let active = true
    apiRequest(`/monitoring/servers/${serverId}/containers/${encodeURIComponent(container.container_id)}?limit=60`, { method: 'GET' })
      .then((data) => { if (active) { setDetail(data); setError('') } })
      .catch((requestError) => { if (active) setError(requestError.message || 'Could not load container details.') })
    return () => { active = false }
  }, [serverId, container.container_id, sampleTime])

  if (error) return <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>
  if (!detail) return <div className="rounded-xl bg-[#171b1f] p-4 text-sm text-[#9aa0a6]">Loading container details…</div>

  const latest = detail.latest
  const history = detail.history
  const times = history.map((sample) => sample.time)
  const stats = [
    ['CPU', latest.cpu_pct == null ? '—' : `${latest.cpu_pct}%`],
    ['Memory', latest.memory_limit_mb ? `${formatMegabytes(latest.memory_used_mb)} / ${formatMegabytes(latest.memory_limit_mb)}` : formatMegabytes(latest.memory_used_mb)],
    ['Memory %', latest.memory_pct == null ? '—' : `${latest.memory_pct}%`],
    ['Processes', latest.pids ?? '—'],
    ['Network in', formatMegabytes(latest.net_rx_mb)],
    ['Network out', formatMegabytes(latest.net_tx_mb)],
    ['Disk read', formatMegabytes(latest.block_read_mb)],
    ['Disk write', formatMegabytes(latest.block_write_mb)],
  ]

  return (
    <div className="rounded-2xl border border-neutral-800 bg-[#090b0d]/80 p-4">
      <div className="grid gap-x-6 gap-y-1 text-xs text-[#9aa0a6] sm:grid-cols-2">
        <div><span className="text-[#7f8589]">Image:</span> <span className="break-all text-[#cfcfcf]">{detail.image || '—'}</span></div>
        <div><span className="text-[#7f8589]">ID:</span> <span className="font-mono text-[#cfcfcf]">{detail.container_id.slice(0, 12)}</span></div>
        <div><span className="text-[#7f8589]">Uptime:</span> <span className="text-[#cfcfcf]">{formatUptime(detail.started_at)}</span></div>
        <div><span className="text-[#7f8589]">Health:</span> <span className="text-[#cfcfcf]">{latest.healthcheck_status || 'no healthcheck'}</span></div>
        <div className="sm:col-span-2"><span className="text-[#7f8589]">Ports:</span> <span className="break-all text-[#cfcfcf]">{detail.ports || 'none published'}</span></div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map(([label, value]) => <div key={label} className="rounded-xl bg-[#171b1f] p-3"><div className="text-xs text-[#9aa0a6]">{label}</div><div className="mt-1 text-base font-semibold text-white">{value}</div></div>)}
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <MetricTrend title="Container CPU" color="#34d399" value={latest.cpu_pct == null ? '—' : latest.cpu_pct} points={history.map((s) => s.cpu_pct)} times={times} />
        <MetricTrend title="Container memory" color="#60a5fa" value={latest.memory_pct == null ? '—' : latest.memory_pct} points={history.map((s) => s.memory_pct)} times={times} />
      </div>
      <p className="mt-3 text-[10px] text-[#7f8589]">Network and disk figures are totals since the container started. CPU can exceed 100% when a container uses more than one core.</p>
    </div>
  )
}

// Plot area spans y=3..97 of the 0..100 viewBox so the line isn't clipped at 0% or 100%.
const TREND_Y_TICKS = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 0]
const trendY = (percent) => 3 + (100 - Math.max(0, Math.min(100, Number(percent) || 0))) * 0.94

function MetricTrend({ title, color, value, points, times }) {
  // Used in url(#…), so keep only characters that are safe there (titles like "Disk usage (/)" aren't).
  const fillId = `fill-${title.replace(/[^a-zA-Z0-9]+/g, '-')}`
  const chartPoints = points.map((point, index) => `${points.length > 1 ? (index / (points.length - 1)) * 320 : 320},${trendY(point)}`).join(' ')
  const areaPoints = points.length ? `0,${trendY(0)} ${chartPoints} 320,${trendY(0)}` : ''
  return (
    <article className="glass-panel rounded-[24px] p-5">
      <div className="flex items-start justify-between gap-3"><div><div className="text-sm text-[#9aa0a6]">{title}</div><div className="mt-2 text-2xl font-semibold text-white">{value === '—' ? '—' : `${value}%`}</div></div><span className="rounded-full px-2.5 py-1 text-[10px] uppercase tracking-wider" style={{ color, backgroundColor: `${color}18` }}>Live</span></div>
      <div className="mt-4 flex h-28 w-full gap-2">
        <div className="relative w-8 shrink-0 text-right text-[10px] tabular-nums text-[#7f8589]" aria-hidden="true">
          {TREND_Y_TICKS.filter((tick) => tick % 20 === 0).map((tick) => <span key={tick} className="absolute right-0 -translate-y-1/2 leading-none" style={{ top: `${trendY(tick)}%` }}>{tick}%</span>)}
        </div>
        <div className="min-w-0 flex-1">
          {points.length > 1 ? <svg viewBox="0 0 320 100" preserveAspectRatio="none" className="h-full w-full" role="img" aria-label={`${title} recent trend`}><defs><linearGradient id={fillId} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.28"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>{TREND_Y_TICKS.map((tick) => <line key={tick} x1="0" x2="320" y1={trendY(tick)} y2={trendY(tick)} stroke="#ffffff" strokeOpacity={tick % 20 === 0 ? 0.12 : 0.05} strokeWidth="1" vectorEffect="non-scaling-stroke" />)}<polygon points={areaPoints} fill={`url(#${fillId})`}/><polyline points={chartPoints} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/></svg> : <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-neutral-800 text-xs text-[#7f8589]">Trend appears as live samples arrive</div>}
        </div>
      </div>
      <div className="mt-2 flex justify-between pl-10 text-[10px] text-[#7f8589]"><span>{times[0] ? new Date(times[0]).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Waiting for data'}</span><span>{times.at(-1) ? new Date(times.at(-1)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}</span></div>
    </article>
  )
}

// Placeholder data until the agent diagnoses incidents on the backend.
const placeholderIncidents = [
  {
    id: 'inc-1',
    severity: 'CRITICAL',
    server: 'homelab-01',
    detectedAt: '2026-09-25T01:42:00',
    status: 'diagnosed',
    summary: 'GitLab container keeps restarting',
    details: 'The gitlab container restarted 6 times in the last 30 minutes. Each restart follows an out-of-memory kill while Puma workers are starting, so the service never becomes healthy.',
    actions: [
      'Raise the container memory limit to at least 6 GB, or lower puma[\'worker_processes\'] in gitlab.rb.',
      'Run docker logs gitlab --since 30m and confirm the OOM kill in dmesg.',
      'After restarting, confirm the healthcheck turns healthy and the restart count stops increasing.',
    ],
  },
  {
    id: 'inc-2',
    severity: 'WARNING',
    server: 'homelab-01',
    detectedAt: '2026-09-25T00:15:00',
    status: 'diagnosed',
    summary: 'Root disk is 94% full',
    details: 'Disk usage on / grew from 81% to 94% over the last 3 days. Most of the growth is in /var/lib/docker, from unused images and build cache.',
    actions: [
      'Run docker system df to see how much space images, containers and build cache use.',
      'Remove unused images and build cache with docker image prune -a and docker builder prune.',
      'Set up log rotation for container logs so they cannot fill the disk again.',
    ],
  },
  {
    id: 'inc-3',
    severity: 'WARNING',
    server: 'media-server',
    detectedAt: '2026-09-24T22:03:00',
    status: 'analyzing',
    summary: 'High CPU usage on jellyfin',
    details: 'The jellyfin container has used over 350% CPU for 20 minutes, which matches software video transcoding.',
    actions: [],
  },
]

const placeholderChat = [
  { id: 'm1', role: 'agent', text: 'Hi! I review new incidents automatically and suggest how to fix them. Ask me about any report on the left, or about your servers.' },
  { id: 'm2', role: 'user', text: 'Why does GitLab keep restarting?' },
  { id: 'm3', role: 'agent', text: 'It runs out of memory while its Puma workers start, so the kernel kills it and Docker restarts it. Raising the memory limit or running fewer workers should fix it. The full steps are in the report.' },
]

function IncidentCard({ incident, expanded, onToggle }) {
  const critical = incident.severity === 'CRITICAL'

  return (
    <article className={`message-card rounded-[20px] transition ${expanded ? 'border-white/15' : 'hover:border-white/15'}`}>
      <button type="button" onClick={onToggle} aria-expanded={expanded} className="flex w-full items-start gap-3 p-4 text-left">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${critical ? 'bg-red-400' : 'bg-amber-400'}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[#9aa0a6]">
            <time dateTime={incident.detectedAt}>{new Date(incident.detectedAt).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</time>
            <span>·</span>
            <span>{incident.server}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${critical ? 'bg-red-400/10 text-red-300' : 'bg-amber-400/10 text-amber-200'}`}>
              {incident.severity}
            </span>
          </div>
          <h3 className="mt-1.5 font-medium text-white">{incident.summary}</h3>
        </div>
        <span className={`mt-1 text-[#9aa0a6] transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden="true">⌄</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pl-9">
          <p className="text-sm leading-6 break-words text-[#bcbcbc]">{incident.details}</p>
          <div className="mt-4 rounded-2xl border border-cyan-300/15 bg-cyan-300/5 p-4">
            <div className="text-xs uppercase tracking-[0.18em] text-cyan-200">Recommended actions</div>
            {incident.status === 'analyzing' ? (
              <p className="mt-3 flex items-center gap-2 text-sm text-[#9aa0a6]">
                <span className="h-2 w-2 animate-pulse rounded-full bg-cyan-300" />
                The agent is still diagnosing this incident.
              </p>
            ) : (
              <ol className="mt-3 space-y-2 text-sm leading-6 text-[#d1d5db]">
                {incident.actions.map((action, index) => (
                  <li key={action} className="flex gap-3">
                    <span className="font-mono text-cyan-300">0{index + 1}</span>
                    <span className="min-w-0 break-words">{action}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}
    </article>
  )
}

function AgentPage({ user, onLogout }) {
  const [expandedId, setExpandedId] = useState(placeholderIncidents[0]?.id)
  const [chatEnabled, setChatEnabled] = useState(true)

  useEffect(() => {
    apiRequest('/agent/config', { method: 'GET' })
      .then((data) => setChatEnabled(data.chat_enabled))
      .catch(() => {})
  }, [])

  return (
    <AppShell user={user} onLogout={onLogout} fitViewport>
      <div className="flex flex-col gap-5 xl:h-full">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Diagnostics</div>
          <h1 className="mt-1 text-3xl font-semibold text-white">Agent</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[#9aa0a6]">
            The agent reviews each incident, diagnoses the cause and recommends how to fix it. Showing placeholder data for now.
          </p>
        </header>

        <div className="grid grid-cols-1 gap-5 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] xl:grid-rows-[minmax(0,1fr)]">
          <section className="glass-panel flex flex-col rounded-[28px] p-5 md:p-6 xl:min-h-0">
            <div className="mb-4 flex items-end justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Incident reports</div>
                <h2 className="mt-1 text-xl font-semibold text-white">Recent problems</h2>
              </div>
              <span className="text-xs text-[#9aa0a6]">{placeholderIncidents.length} reports</span>
            </div>
            <div className="custom-scroll -mr-2 space-y-3 pr-2 xl:min-h-0 xl:flex-1 xl:overflow-y-auto">
              {placeholderIncidents.map((incident) => (
                <IncidentCard
                  key={incident.id}
                  incident={incident}
                  expanded={expandedId === incident.id}
                  onToggle={() => setExpandedId((current) => (current === incident.id ? null : incident.id))}
                />
              ))}
            </div>
          </section>

          <section className="glass-panel flex h-150 flex-col rounded-[28px] p-5 md:p-6 xl:h-auto xl:min-h-0">
            <div className="mb-4 flex items-end justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Chat</div>
                <h2 className="mt-1 text-xl font-semibold text-white">Ask the agent</h2>
              </div>
              {chatEnabled
                ? <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[10px] uppercase tracking-[0.2em] text-[#9aa0a6]">Preview</span>
                : <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-1 text-[10px] uppercase tracking-[0.2em] text-amber-200">Off</span>}
            </div>
            {!chatEnabled && (
              <div className="mb-4 rounded-2xl border border-amber-400/20 bg-amber-400/5 px-4 py-3 text-sm text-amber-200">
                Chat with the agent has been turned off by an admin. Incident reports still work.
              </div>
            )}
            <div className="custom-scroll -mr-2 min-h-0 flex-1 space-y-3 overflow-y-auto pr-2">
              {placeholderChat.map((message) => (
                <div key={message.id} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === 'user' ? 'bg-white/10 text-white' : 'message-card text-[#d1d5db]'}`}>
                    {message.text}
                  </div>
                </div>
              ))}
            </div>
            <form className="mt-4 flex gap-2" onSubmit={(event) => event.preventDefault()}>
              <input
                disabled
                placeholder={chatEnabled ? 'Chat is not connected yet' : 'Chat is turned off'}
                className="min-w-0 flex-1 rounded-2xl border border-[#1f2326] bg-[#0d1013]/80 px-4 py-3 text-sm text-[#cfcfcf] outline-none placeholder:text-[#6b7075] disabled:cursor-not-allowed"
              />
              <button type="submit" disabled className="rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-3 text-sm font-medium text-[#0b0b0b] disabled:cursor-not-allowed disabled:opacity-40">
                Send
              </button>
            </form>
          </section>
        </div>
      </div>
    </AppShell>
  )
}

// Placeholder data for the admin page until its backend endpoints exist.
const placeholderAdmin = {
  users: [
    { id: 1, name: 'Admin', email: 'admin@example.com', role: 'admin', lastLogin: '2026-09-25T01:58:00', active: true, sessions: 2 },
    { id: 2, name: 'Ana', email: 'ana@example.com', role: 'user', lastLogin: '2026-09-23T18:12:00', active: true, sessions: 1 },
    { id: 3, name: 'Old account', email: 'old@example.com', role: 'user', lastLogin: '2026-06-02T09:40:00', active: false, sessions: 0 },
  ],
  servers: [
    { id: 1, name: 'homelab-01', online: true, lastSeen: '2026-09-25T02:10:00', interval: 60, collectorVersion: '0.4.0' },
    { id: 2, name: 'media-server', online: true, lastSeen: '2026-09-25T02:09:30', interval: 60, collectorVersion: '0.4.0' },
    { id: 3, name: 'backup-nas', online: false, lastSeen: '2026-09-24T14:22:00', interval: 300, collectorVersion: '0.3.2' },
  ],
  pending: [{ id: 7, hostname: 'raspberrypi', ip: '192.168.1.44', requestedAt: '2026-09-25T01:30:00' }],
  services: [
    { name: 'Backend API', status: 'ok', detail: 'Responding' },
    { name: 'PostgreSQL', status: 'ok', detail: '412 MB' },
    { name: 'Redis', status: 'ok', detail: '3 active sessions' },
  ],
  audit: [
    { id: 1, time: '2026-09-25T01:58:00', actor: 'Admin', event: 'Signed in' },
    { id: 2, time: '2026-09-25T01:31:00', actor: 'system', event: 'New server requested to join: raspberrypi' },
    { id: 3, time: '2026-09-24T20:05:00', actor: 'Admin', event: 'Changed check interval of backup-nas to 300 s' },
    { id: 4, time: '2026-09-24T19:47:00', actor: 'Admin', event: 'Approved server media-server' },
  ],
}

const adminTabs = [
  { id: 'users', label: 'Users' },
  { id: 'servers', label: 'Servers' },
  { id: 'agent', label: 'Agent' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'system', label: 'System' },
]

const formatDateTime = (value) => new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })

const inputClass = 'w-full rounded-2xl border border-[#1f2326] bg-[#0d1013]/80 px-4 py-2.5 text-sm text-[#cfcfcf] outline-none transition focus:border-white/30'
const secondaryButtonClass = 'min-h-10 sm:min-h-0 rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-[#cfcfcf] transition hover:bg-white/10'
const dangerButtonClass = 'min-h-10 sm:min-h-0 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-300 transition hover:bg-red-500/15'

function AdminSection({ title, description, action, children }) {
  return (
    <section className="glass-panel rounded-[28px] p-5 md:p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          {description && <p className="mt-1 max-w-2xl text-sm text-[#9aa0a6]">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function Toggle({ label, description, defaultOn = false, checked, onChange, disabled = false }) {
  const [localOn, setLocalOn] = useState(defaultOn)
  const controlled = checked !== undefined
  const on = controlled ? checked : localOn
  const setOn = controlled ? (update) => onChange(update(on)) : setLocalOn

  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3">
      <div>
        <div className="text-sm text-white">{label}</div>
        {description && <div className="mt-0.5 text-xs text-[#9aa0a6]">{description}</div>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        disabled={disabled}
        onClick={() => setOn((value) => !value)}
        className={`relative h-6 w-11 shrink-0 rounded-full transition before:absolute before:-inset-2.5 before:content-[''] disabled:cursor-not-allowed disabled:opacity-50 ${on ? 'bg-emerald-500/70' : 'bg-white/10'}`}
      >
        <span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${on ? 'left-6' : 'left-1'}`} />
      </button>
    </div>
  )
}

function StatusDot({ ok }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-red-400'}`} />
}

function SaveButton() {
  return (
    <button type="button" disabled title="Not connected yet" className="rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-2.5 text-sm font-medium text-[#0b0b0b] disabled:cursor-not-allowed disabled:opacity-40">
      Save changes
    </button>
  )
}

function TwoFactorPolicySetting() {
  const [state, setState] = useState(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    apiRequest('/admin/settings', { method: 'GET' })
      .then((data) => setState({ required: data.require_2fa_for_admins, configured: data.secrets_configured }))
      .catch((error) => setMessage({ ok: false, text: error.message || 'Could not load the setting.' }))
  }, [])

  const change = async (next) => {
    setSaving(true)
    setMessage(null)
    try {
      const data = await apiRequest('/admin/settings', { method: 'PUT', body: JSON.stringify({ require_2fa_for_admins: next }) })
      setState({ required: data.require_2fa_for_admins, configured: data.secrets_configured })
      setMessage({ ok: true, text: data.require_2fa_for_admins ? 'Administrators must now use two-factor. Those without it are asked to set it up at their next sign-in.' : 'Two-factor is optional for administrators.' })
    } catch (error) {
      setMessage({ ok: false, text: error.message || 'Could not save the setting.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <AdminSection title="Two-factor" description="Protects sign-in with a code from an authenticator app, on top of the password.">
      <Toggle label="Require two-factor for administrators" description="An administrator without it can only reach the two-factor setup until it is done." checked={state?.required ?? false} onChange={change} disabled={state === null || saving} />
      {state && !state.configured && <p className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200">SECRETS_KEY is not set on the server, so two-factor can't be set up and this is <strong>not being enforced</strong> yet. Set it in <code>.env</code> (see docs/accounts.md, Two-factor sign-in) and restart.</p>}
      {message && <p className={`mt-3 text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
    </AdminSection>
  )
}

function AdminUsersTab() {
  return (
    <div className="space-y-5">
      <TwoFactorPolicySetting />

      <AdminSection title="Users" description="Change roles, disable accounts or sign someone out everywhere.">
        <ul className="space-y-2 md:hidden">
          {placeholderAdmin.users.map((account) => (
            <li key={account.id} className={`rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 p-4 ${account.active ? '' : 'opacity-60'}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="truncate text-white">{account.name}</div><div className="truncate text-xs text-[#9aa0a6]">{account.email}</div></div>
                <span className="inline-flex shrink-0 items-center gap-2 text-xs text-[#bcbcbc]"><StatusDot ok={account.active} />{account.active ? 'Active' : 'Disabled'}</span>
              </div>
              <div className="mt-1 text-xs text-[#7f8589]">Last login {formatDateTime(account.lastLogin)}</div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                <select defaultValue={account.role} aria-label={`Role of ${account.name}`} className="h-10 min-w-0 rounded-xl border border-[#1f2326] bg-[#0d1013] px-3 text-sm text-[#cfcfcf]">
                  <option value="admin">Admin</option>
                  <option value="user">User</option>
                </select>
                <button type="button" className={`${secondaryButtonClass} h-10`} disabled={!account.sessions}>Sign out ({account.sessions})</button>
                <button type="button" className={`${account.active ? dangerButtonClass : secondaryButtonClass} h-10`}>{account.active ? 'Disable' : 'Enable'}</button>
              </div>
            </li>
          ))}
        </ul>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-160 text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-[#7f8589]">
              <tr><th className="pb-3 font-medium">User</th><th className="pb-3 font-medium">Role</th><th className="pb-3 font-medium">Last login</th><th className="pb-3 font-medium">Status</th><th className="pb-3 text-right font-medium">Actions</th></tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {placeholderAdmin.users.map((account) => (
                <tr key={account.id} className={account.active ? '' : 'opacity-60'}>
                  <td className="py-3"><div className="text-white">{account.name}</div><div className="text-xs text-[#9aa0a6]">{account.email}</div></td>
                  <td className="py-3">
                    <select defaultValue={account.role} aria-label={`Role of ${account.name}`} className="rounded-xl border border-[#1f2326] bg-[#0d1013] px-2 py-1 text-xs text-[#cfcfcf]">
                      <option value="admin">Admin</option>
                      <option value="user">User</option>
                    </select>
                  </td>
                  <td className="py-3 text-[#bcbcbc]">{formatDateTime(account.lastLogin)}</td>
                  <td className="py-3"><span className="inline-flex items-center gap-2 text-[#bcbcbc]"><StatusDot ok={account.active} />{account.active ? 'Active' : 'Disabled'}</span></td>
                  <td className="py-3">
                    <div className="flex justify-end gap-2">
                      <button type="button" className={secondaryButtonClass} disabled={!account.sessions}>Sign out ({account.sessions})</button>
                      <button type="button" className={account.active ? dangerButtonClass : secondaryButtonClass}>{account.active ? 'Disable' : 'Enable'}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </AdminSection>
    </div>
  )
}

function AdminServersTab() {
  return (
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-2">
        <AdminSection title="Enrollment key" description="Servers that connect with this key are approved automatically.">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input readOnly value="hlk_••••••••••••••••3f9a" aria-label="Enrollment key" className={`${inputClass} font-mono`} />
            <div className="flex gap-2">
              <button type="button" className={`${secondaryButtonClass} h-10 flex-1 sm:h-auto sm:flex-none`}>Regenerate</button>
              <button type="button" className={`${dangerButtonClass} h-10 flex-1 sm:h-auto sm:flex-none`}>Revoke</button>
            </div>
          </div>
        </AdminSection>

        <AdminSection title="Waiting for approval" description="Collectors that asked to join without a key.">
          <div className="space-y-2">
            {placeholderAdmin.pending.map((request) => (
              <div key={request.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-400/20 bg-amber-400/5 px-4 py-3">
                <div><div className="text-sm text-white">{request.hostname}</div><div className="text-xs text-[#9aa0a6]">{request.ip} · {formatDateTime(request.requestedAt)}</div></div>
                <div className="flex w-full gap-2 sm:w-auto"><button type="button" className={`${secondaryButtonClass} h-10 flex-1 sm:h-auto`}>Approve</button><button type="button" className={`${dangerButtonClass} h-10 flex-1 sm:h-auto`}>Dismiss</button></div>
              </div>
            ))}
          </div>
        </AdminSection>
      </div>

      <AdminSection title="Servers" description="Every monitored machine, its collector and how often it reports.">
        <ul className="space-y-2 md:hidden">
          {placeholderAdmin.servers.map((server) => (
            <li key={server.id} className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex min-w-0 items-center gap-2 text-white"><StatusDot ok={server.online} /><span className="truncate">{server.name}</span></span>
                <span className="shrink-0 font-mono text-xs text-[#9aa0a6]">v{server.collectorVersion}</span>
              </div>
              <div className="mt-1 text-xs text-[#7f8589]">Last seen {formatDateTime(server.lastSeen)}</div>
              <div className="mt-3 flex items-center gap-2">
                <select defaultValue={server.interval} aria-label={`Check interval of ${server.name}`} className="h-10 flex-1 rounded-xl border border-[#1f2326] bg-[#0d1013] px-3 text-sm text-[#cfcfcf]">
                  {[15, 30, 60, 120, 300].map((seconds) => <option key={seconds} value={seconds}>Every {seconds} s</option>)}
                </select>
                <button type="button" className={`${dangerButtonClass} h-10`}>Remove</button>
              </div>
            </li>
          ))}
        </ul>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-160 text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-[#7f8589]">
              <tr><th className="pb-3 font-medium">Server</th><th className="pb-3 font-medium">Last seen</th><th className="pb-3 font-medium">Collector</th><th className="pb-3 font-medium">Interval</th><th className="pb-3 text-right font-medium">Actions</th></tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {placeholderAdmin.servers.map((server) => (
                <tr key={server.id}>
                  <td className="py-3"><span className="inline-flex items-center gap-2 text-white"><StatusDot ok={server.online} />{server.name}</span></td>
                  <td className="py-3 text-[#bcbcbc]">{formatDateTime(server.lastSeen)}</td>
                  <td className="py-3 font-mono text-xs text-[#bcbcbc]">v{server.collectorVersion}</td>
                  <td className="py-3">
                    <select defaultValue={server.interval} aria-label={`Check interval of ${server.name}`} className="rounded-xl border border-[#1f2326] bg-[#0d1013] px-2 py-1 text-xs text-[#cfcfcf]">
                      {[15, 30, 60, 120, 300].map((seconds) => <option key={seconds} value={seconds}>{seconds} s</option>)}
                    </select>
                  </td>
                  <td className="py-3"><div className="flex justify-end"><button type="button" className={dangerButtonClass}>Remove</button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </AdminSection>
    </div>
  )
}

function AgentChatSetting() {
  const [enabled, setEnabled] = useState(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    apiRequest('/admin/settings', { method: 'GET' })
      .then((data) => setEnabled(data.agent_chat_enabled))
      .catch((error) => setMessage({ ok: false, text: error.message || 'Could not load the setting.' }))
  }, [])

  // Saves straight away, like a light switch.
  const change = async (next) => {
    setSaving(true)
    setMessage(null)
    try {
      const data = await apiRequest('/admin/settings', { method: 'PUT', body: JSON.stringify({ agent_chat_enabled: next }) })
      setEnabled(data.agent_chat_enabled)
      setMessage({ ok: true, text: data.agent_chat_enabled ? 'Chat is on for everyone.' : 'Chat is off. Nobody can send messages to the agent.' })
    } catch (error) {
      setMessage({ ok: false, text: error.message || 'Could not save the setting.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <AdminSection title="Chat" description="Lets users talk to the agent on the Agent page. Automatic incident diagnosis is not affected.">
      <Toggle label="Allow chat with the agent" description="Applies to every user, including admins." checked={enabled ?? false} onChange={change} disabled={enabled === null || saving} />
      {message && <p className={`mt-3 text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
    </AdminSection>
  )
}

function AdminAgentTab() {
  return (
    <div className="space-y-5">
      <AgentChatSetting />
      <div className="grid gap-5 xl:grid-cols-2">
        <AdminSection title="Model" description="Which Gemini model the agent uses.">
          <div className="space-y-3">
            <select defaultValue="models/gemini-3.5-flash" aria-label="Agent model" className={inputClass}>
              <option value="models/gemini-3.5-flash">gemini-3.5-flash</option>
              <option value="models/gemini-3.5-pro">gemini-3.5-pro</option>
            </select>
            <div className="flex items-center justify-between rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm">
              <span className="text-[#bcbcbc]">API key</span>
              <span className="inline-flex items-center gap-2 text-white"><StatusDot ok />Set in .env</span>
            </div>
          </div>
        </AdminSection>

        <AdminSection title="Automatic diagnosis" description="Send new incidents to the agent for review without asking.">
          <div className="space-y-3">
            <Toggle label="Diagnose new incidents automatically" defaultOn />
            <label className="block">
              <span className="mb-2 block text-sm text-[#bcbcbc]">Minimum severity</span>
              <select defaultValue="WARNING" className={inputClass}>
                <option value="CRITICAL">Critical only</option>
                <option value="WARNING">Warning and critical</option>
              </select>
            </label>
          </div>
        </AdminSection>
      </div>

      <AdminSection title="System prompt" description="Instructions the agent follows in every diagnosis and chat." action={<SaveButton />}>
        <textarea rows={8} defaultValue={adminSettings.systemPrompt} aria-label="System prompt" className={`${inputClass} resize-y leading-6`} />
      </AdminSection>

      <AdminSection title="Safety rules" description="Built into the agent and always on. They can't be changed from the dashboard.">
        <div className="grid gap-2 md:grid-cols-2">
          {adminSettings.safetyRules.map((rule) => (
            <div key={rule} className="flex items-center justify-between gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm text-slate-200">
              {rule}
              <span className="rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] text-emerald-300">Enforced</span>
            </div>
          ))}
        </div>
      </AdminSection>
    </div>
  )
}

// [label, hint, warning key, critical key (null when the rule only warns), unit, step]
const thresholdRows = [
  ['Disk usage', 'Default for every mount', 'disk_warning_pct', 'disk_critical_pct', '%', 1],
  ['Inode usage', 'Root filesystem', 'inodes_warning_pct', 'inodes_critical_pct', '%', 1],
  ['Memory usage', null, 'memory_warning_pct', 'memory_critical_pct', '%', 1],
  ['Swap usage', 'Warning only', 'swap_warning_pct', null, '%', 1],
  ['Load per CPU core', '5-minute average', 'load_warning_per_core', 'load_critical_per_core', '×', 0.1],
]

const thresholdInputClass = 'h-10 sm:h-8 w-20 rounded-xl border border-[#1f2326] bg-[#0d1013] px-2 py-1 text-right text-white outline-none focus:border-white/30 disabled:opacity-50'

function ThresholdInput({ label, value, step, onChange, disabled, caption }) {
  const input = <input type="number" inputMode="decimal" aria-label={label} value={value ?? ''} step={step} min={0} disabled={disabled} onChange={(event) => onChange(event.target.value === '' ? '' : Number(event.target.value))} className={thresholdInputClass} />
  if (!caption) return input
  // On phones the Warning/Critical column headings are hidden, so each field carries its own.
  return <span className="flex flex-col gap-1"><span className={`text-[10px] uppercase tracking-wider sm:hidden ${caption === 'Warning' ? 'text-amber-300/80' : 'text-red-300/80'}`}>{caption}</span>{input}</span>
}

function IncidentThresholdsSection() {
  const [saved, setSaved] = useState(null)
  const [values, setValues] = useState(null)
  const [savedMounts, setSavedMounts] = useState([])
  const [mountRows, setMountRows] = useState([])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  // The API keys overrides by mount; the form edits them as rows so a path can be typed freely.
  const toRows = (mountThresholds) => Object.entries(mountThresholds || {}).map(([mount, limits]) => ({ mount, warning: limits.disk_warning_pct, critical: limits.disk_critical_pct }))
  const toMountThresholds = (rows) => Object.fromEntries(rows.map((row) => [row.mount.trim(), { disk_warning_pct: row.warning, disk_critical_pct: row.critical }]))

  useEffect(() => {
    apiRequest('/admin/settings', { method: 'GET' })
      .then((data) => {
        setSaved(data.alert_thresholds)
        setValues(data.alert_thresholds)
        setSavedMounts(toRows(data.mount_thresholds))
        setMountRows(toRows(data.mount_thresholds))
      })
      .catch((error) => setMessage({ ok: false, text: error.message || 'Could not load the thresholds.' }))
  }, [])

  const set = (key) => (value) => setValues((current) => ({ ...current, [key]: value }))
  const setMountRow = (index, field) => (value) => setMountRows((rows) => rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)))

  const save = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const data = await apiRequest('/admin/settings', { method: 'PUT', body: JSON.stringify({ alert_thresholds: values, mount_thresholds: toMountThresholds(mountRows) }) })
      setSaved(data.alert_thresholds)
      setValues(data.alert_thresholds)
      setSavedMounts(toRows(data.mount_thresholds))
      setMountRows(toRows(data.mount_thresholds))
      setMessage({ ok: true, text: 'Saved. New thresholds apply from each server\'s next check-in.' })
    } catch (error) {
      setMessage({ ok: false, text: error.message || 'Could not save the thresholds.' })
    } finally {
      setSaving(false)
    }
  }

  const loading = values === null
  const unchanged = loading || (JSON.stringify(values) === JSON.stringify(saved) && JSON.stringify(mountRows) === JSON.stringify(savedMounts))
  const mountPaths = mountRows.map((row) => row.mount.trim())
  const incomplete = !loading && (Object.values(values).some((value) => value === '') || mountRows.some((row) => !row.mount.trim() || row.warning === '' || row.critical === '') || new Set(mountPaths).size !== mountPaths.length)

  return (
    <AdminSection
      title="Incident thresholds"
      description="When monitoring opens an incident. Applies to every server."
      action={
        <button type="button" onClick={save} disabled={unchanged || incomplete || saving} className="rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-2.5 text-sm font-medium text-[#0b0b0b] disabled:cursor-not-allowed disabled:opacity-40">
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      }
    >
      <div className="space-y-2">
        <div className="hidden justify-end gap-2 px-4 text-[10px] uppercase tracking-wider text-[#7f8589] sm:flex"><span className="w-20 text-right text-amber-300/80">Warning</span><span className="w-20 text-right text-red-300/80">Critical</span><span className="w-4" /></div>
        {thresholdRows.map(([label, hint, warningKey, criticalKey, unit, step]) => (
          <div key={label} className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
            <span><span className="text-[#bcbcbc]">{label}</span>{hint && <span className="block text-xs text-[#7f8589]">{hint}</span>}</span>
            <span className="flex items-end gap-2 sm:items-center">
              <ThresholdInput label={`${label} warning`} caption="Warning" value={values?.[warningKey]} step={step} onChange={set(warningKey)} disabled={loading} />
              {criticalKey ? <ThresholdInput label={`${label} critical`} caption="Critical" value={values?.[criticalKey]} step={step} onChange={set(criticalKey)} disabled={loading} /> : <span className="hidden w-20 text-right text-[#7f8589] sm:inline">—</span>}
              <span className="w-4 pb-2 text-xs text-[#9aa0a6] sm:pb-0">{unit}</span>
            </span>
          </div>
        ))}
        <label className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
          <span><span className="text-[#bcbcbc]">Check-ins in a row</span><span className="block text-xs text-[#7f8589]">How long a host metric must stay over before an incident opens</span></span>
          <ThresholdInput label="Check-ins in a row" value={values?.sustained_checks} step={1} onChange={set('sustained_checks')} disabled={loading} />
        </label>
        <div className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
          <span className="shrink-0"><span className="text-[#bcbcbc]">Container restart loop</span><span className="block text-xs text-[#7f8589]">Critical incident</span></span>
          <span className="flex flex-wrap items-center gap-2 text-xs text-[#9aa0a6]">
            <ThresholdInput label="Restarts" value={values?.restart_loop_count} step={1} onChange={set('restart_loop_count')} disabled={loading} />restarts in
            <ThresholdInput label="Restart window in minutes" value={values?.restart_loop_window_minutes} step={1} onChange={set('restart_loop_window_minutes')} disabled={loading} />min
          </span>
        </div>
        <label className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
          <span><span className="text-[#bcbcbc]">Event incidents</span><span className="block text-xs text-[#7f8589]">Minutes an incident stays open after a reboot, OOM kill, container exit, removal or image change</span></span>
          <ThresholdInput label="Event incident window in minutes" value={values?.event_window_minutes} step={1} onChange={set('event_window_minutes')} disabled={loading} />
        </label>
        <label className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
          <span><span className="text-[#bcbcbc]">Slow Docker</span><span className="block text-xs text-[#7f8589]">Milliseconds to list containers before Docker counts as slow (sustained)</span></span>
          <ThresholdInput label="Slow Docker threshold in milliseconds" value={values?.docker_slow_ms} step={100} onChange={set('docker_slow_ms')} disabled={loading} />
        </label>
        <label className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
          <span><span className="text-[#bcbcbc]">Slow collection</span><span className="block text-xs text-[#7f8589]">Warning when gathering data takes more than this percent of the check interval (sustained)</span></span>
          <ThresholdInput label="Slow collection threshold in percent of the check interval" value={values?.collect_slow_pct} step={5} onChange={set('collect_slow_pct')} disabled={loading} />
        </label>
        <div className="flex flex-col gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-2.5">
          <span className="shrink-0"><span className="text-[#bcbcbc]">Possible memory leak</span><span className="block text-xs text-[#7f8589]">Warning when a container's memory climbs steadily</span></span>
          <span className="flex flex-wrap items-center gap-2 text-xs text-[#9aa0a6]">
            <ThresholdInput label="Leak window in hours" value={values?.leak_window_hours} step={1} onChange={set('leak_window_hours')} disabled={loading} />h, at least
            <ThresholdInput label="Leak minimum MB per hour" value={values?.leak_min_mb_per_hour} step={1} onChange={set('leak_min_mb_per_hour')} disabled={loading} />MB/h and
            <ThresholdInput label="Leak minimum growth percent" value={values?.leak_min_growth_pct} step={1} onChange={set('leak_min_growth_pct')} disabled={loading} />%
          </span>
        </div>
        <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span><span className="text-[#bcbcbc]">Disk usage per mount</span><span className="block text-xs text-[#7f8589]">Replaces the default disk thresholds for that mount, on every server</span></span>
            <button type="button" disabled={loading} onClick={() => setMountRows((rows) => [...rows, { mount: '', warning: values?.disk_warning_pct ?? 85, critical: values?.disk_critical_pct ?? 95 }])} className={secondaryButtonClass}>Add override</button>
          </div>
          {mountRows.length > 0 && <div className="mt-3 space-y-2">{mountRows.map((row, index) => (
            <div key={index} className="flex flex-wrap items-end gap-2 sm:items-center">
              <input type="text" aria-label="Mount path" placeholder="/mnt/data" value={row.mount} onChange={(event) => setMountRow(index, 'mount')(event.target.value)} className="h-10 min-w-0 flex-1 basis-40 rounded-xl border border-[#1f2326] bg-[#0d1013] px-3 font-mono text-xs text-white outline-none focus:border-white/30 sm:h-8" />
              <ThresholdInput label={`${row.mount || 'Mount'} warning`} caption="Warning" value={row.warning} step={1} onChange={setMountRow(index, 'warning')} />
              <ThresholdInput label={`${row.mount || 'Mount'} critical`} caption="Critical" value={row.critical} step={1} onChange={setMountRow(index, 'critical')} />
              <span className="pb-2 text-xs text-[#9aa0a6] sm:pb-0">%</span>
              <button type="button" aria-label={`Remove override for ${row.mount || 'mount'}`} onClick={() => setMountRows((rows) => rows.filter((_, i) => i !== index))} className="pb-2 text-lg leading-none text-[#9aa0a6] hover:text-red-300 sm:pb-0">×</button>
            </div>
          ))}</div>}
        </div>
      </div>
      {message && <p className={`mt-3 text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
    </AdminSection>
  )
}

function AdminAlertsTab() {
  return (
    <IncidentThresholdsSection />
  )
}

const retentionOptions = [7, 30, 90, 365]

function DataRetentionSection() {
  const [savedDays, setSavedDays] = useState(null)
  const [days, setDays] = useState(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    apiRequest('/admin/settings', { method: 'GET' })
      .then((data) => {
        setSavedDays(data.metrics_retention_days)
        setDays(data.metrics_retention_days)
      })
      .catch((error) => setMessage({ ok: false, text: error.message || 'Could not load the setting.' }))
  }, [])

  const save = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const data = await apiRequest('/admin/settings', { method: 'PUT', body: JSON.stringify({ metrics_retention_days: days }) })
      setSavedDays(data.metrics_retention_days)
      setMessage({ ok: true, text: 'Saved. Older metrics are removed within the hour.' })
    } catch (error) {
      setMessage({ ok: false, text: error.message || 'Could not save the setting.' })
    } finally {
      setSaving(false)
    }
  }

  const options = days && !retentionOptions.includes(days) ? [...retentionOptions, days].sort((a, b) => a - b) : retentionOptions
  const unchanged = days === null || days === savedDays

  return (
    <AdminSection
      title="Data retention"
      description="Older metrics are deleted automatically to keep the database small."
      action={
        <button type="button" onClick={save} disabled={unchanged || saving} className="rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-2.5 text-sm font-medium text-[#0b0b0b] disabled:cursor-not-allowed disabled:opacity-40">
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      }
    >
      <select value={days ?? ''} disabled={days === null} onChange={(event) => setDays(Number(event.target.value))} aria-label="Keep metrics for" className={inputClass}>
        {days === null && <option value="">Loading…</option>}
        {options.map((value) => <option key={value} value={value}>Keep metrics for {value === 365 ? '1 year' : `${value} days`}</option>)}
      </select>
      {message && <p className={`mt-3 text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
    </AdminSection>
  )
}

function AdminSystemTab() {
  return (
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-2">
        <AdminSection title="Services">
          <div className="space-y-2">
            {placeholderAdmin.services.map((service) => (
              <div key={service.name} className="flex items-center justify-between rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm">
                <span className="inline-flex items-center gap-2 text-white"><StatusDot ok={service.status === 'ok'} />{service.name}</span>
                <span className="text-xs text-[#9aa0a6]">{service.detail}</span>
              </div>
            ))}
          </div>
        </AdminSection>

        <DataRetentionSection />
      </div>

      <AdminSection title="Audit log" description="Who did what, and when.">
        <ul className="divide-y divide-white/5">
          {placeholderAdmin.audit.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2.5 text-sm">
              <time className="w-40 shrink-0 text-xs text-[#7f8589]">{formatDateTime(entry.time)}</time>
              <span className="w-20 shrink-0 text-[#9aa0a6]">{entry.actor}</span>
              <span className="text-[#d1d5db]">{entry.event}</span>
            </li>
          ))}
        </ul>
      </AdminSection>
    </div>
  )
}

const adminTabContent = {
  users: AdminUsersTab,
  servers: AdminServersTab,
  agent: AdminAgentTab,
  alerts: AdminAlertsTab,
  system: AdminSystemTab,
}

function AdminPage({ user, onLogout }) {
  const [activeTab, setActiveTab] = useState('users')
  const TabContent = adminTabContent[activeTab]

  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="flex flex-col gap-5">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Configuration</div>
          <h1 className="mt-1 text-3xl font-semibold text-white">Admin</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[#9aa0a6]">Manage users, servers, the agent and system settings. Showing placeholder data for now; changes are not saved.</p>
          <div role="tablist" aria-label="Admin sections" className="no-scrollbar -mx-5 mt-5 flex gap-2 overflow-x-auto px-5 md:mx-0 md:flex-wrap md:px-0">
            {adminTabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`shrink-0 rounded-2xl px-4 py-2.5 text-sm transition ${activeTab === tab.id ? 'bg-[#171b1f] text-white ring-1 ring-white/10' : 'text-[#bcbcbc] hover:bg-[#111417] hover:text-white'}`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </header>

        <TabContent />
      </div>
    </AppShell>
  )
}

function RequireAuth({ user, children }) {
  const location = useLocation()

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  // An administrator who still has to set up two-factor can only use the account page.
  if (user.setup_required && location.pathname !== '/account') {
    return <Navigate to="/account" replace />
  }

  return children
}

function SecretsBox({ children }) {
  return <div className="rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 p-4">{children}</div>
}

function PasswordSection() {
  const [form, setForm] = useState({ current: '', next: '', repeat: '' })
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    setMessage(null)
    if (form.next.length < MIN_PASSWORD_LENGTH) return setMessage({ ok: false, text: `The new password must be at least ${MIN_PASSWORD_LENGTH} characters.` })
    if (form.next !== form.repeat) return setMessage({ ok: false, text: 'The two new passwords do not match.' })
    setBusy(true)
    try {
      const data = await apiRequest('/auth/password', { method: 'POST', body: JSON.stringify({ current_password: form.current, new_password: form.next }) })
      setForm({ current: '', next: '', repeat: '' })
      setMessage({ ok: true, text: `Password changed. ${data.other_sessions_signed_out} other ${data.other_sessions_signed_out === 1 ? 'session was' : 'sessions were'} signed out.` })
    } catch (error) {
      setMessage({ ok: false, text: error.message || 'Could not change the password.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <AdminSection title="Password" description={`At least ${MIN_PASSWORD_LENGTH} characters; a few random words make a good passphrase. Changing it signs you out everywhere else.`}>
      <form onSubmit={submit} className="max-w-md space-y-3">
        <input type="password" autoComplete="current-password" aria-label="Current password" placeholder="Current password" value={form.current} onChange={set('current')} className={inputClass} required />
        <input type="password" autoComplete="new-password" aria-label="New password" placeholder={`New password (${MIN_PASSWORD_LENGTH}+ characters)`} value={form.next} onChange={set('next')} className={inputClass} required />
        <input type="password" autoComplete="new-password" aria-label="Repeat the new password" placeholder="Repeat the new password" value={form.repeat} onChange={set('repeat')} className={inputClass} required />
        <button type="submit" disabled={busy} className="rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-2.5 text-sm font-medium text-[#0b0b0b] disabled:opacity-50">{busy ? 'Changing…' : 'Change password'}</button>
        {message && <p className={`text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
      </form>
    </AdminSection>
  )
}

function RecoveryCodesPanel({ codes, onDone, doneLabel }) {
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(codes.join('\n')); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { setCopied(false) }
  }
  return (
    <SecretsBox>
      <div className="text-sm font-medium text-white">Save your recovery codes</div>
      <p className="mt-1 text-xs text-[#9aa0a6]">Each code works once if you lose your authenticator. They are shown only now; store them somewhere safe, such as a password manager.</p>
      <ul className="mt-3 grid grid-cols-1 gap-1.5 font-mono text-sm text-[#cfcfcf] sm:grid-cols-2">{codes.map((code) => <li key={code} className="rounded-lg bg-white/5 px-3 py-1.5">{code}</li>)}</ul>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" onClick={copy} className={secondaryButtonClass}>{copied ? 'Copied' : 'Copy codes'}</button>
        <label className="inline-flex items-center gap-2 text-xs text-[#bcbcbc]"><input type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} className="h-4 w-4" />I have saved these codes</label>
        <button type="button" disabled={!saved} onClick={onDone} className="rounded-xl bg-white px-3 py-1.5 text-xs font-medium text-slate-950 disabled:opacity-40">{doneLabel}</button>
      </div>
    </SecretsBox>
  )
}

function TwoFactorSection({ status, reload, onEnrolled }) {
  const [setup, setSetup] = useState(null)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState(null)
  const [form, setForm] = useState({ password: '', code: '' })
  const [action, setAction] = useState(null)
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)

  const run = async (work) => {
    setBusy(true)
    setMessage(null)
    try { await work() } catch (error) { setMessage({ ok: false, text: error.message || 'Something went wrong.' }) } finally { setBusy(false) }
  }
  const [askPassword, setAskPassword] = useState(false)
  const [setupPassword, setSetupPassword] = useState('')
  // A session that has just signed in (the forced first enrolment) needs no password; a normal one must give it again.
  const start = () => {
    if (!status.setup_required && !askPassword) { setAskPassword(true); return }
    return run(async () => {
      setSetup(await apiRequest('/auth/2fa/setup', { method: 'POST', body: JSON.stringify(status.setup_required ? {} : { password: setupPassword }) }))
      setSetupPassword(''); setAskPassword(false)
    })
  }
  const enable = (event) => { event.preventDefault(); run(async () => {
    const data = await apiRequest('/auth/2fa/enable', { method: 'POST', body: JSON.stringify({ code: code.trim() }) })
    setCodes(data.recovery_codes); setSetup(null); setCode('')
    await reload()
  }) }
  const reauth = (path, onData) => (event) => { event.preventDefault(); run(async () => {
    const data = await apiRequest(path, { method: 'POST', body: JSON.stringify({ password: form.password, code: form.code.trim() }) })
    setForm({ password: '', code: '' }); setAction(null); onData(data)
    await reload()
  }) }
  const secretText = setup ? setup.secret.match(/.{1,4}/g).join(' ') : ''

  return (
    <AdminSection title="Two-factor sign-in" description="A 6-digit code from an authenticator app (such as Aegis, Authy or Google Authenticator) is asked for after your password.">
      {!status.available && <p className="rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">Two-factor can't be set up because the server has no <code>SECRETS_KEY</code>. An administrator needs to set one in <code>.env</code> (see docs/accounts.md, Two-factor sign-in).</p>}

      {status.available && codes && <RecoveryCodesPanel codes={codes} doneLabel={status.setup_required || onEnrolled ? 'Continue' : 'Done'} onDone={() => { setCodes(null); if (onEnrolled) onEnrolled() }} />}

      {status.available && !codes && !status.enabled && (
        <div className="space-y-3">
          {status.required && <p className="text-sm text-amber-200">Two-factor is required for administrators on this server.</p>}
          {!setup && askPassword && (
            <form onSubmit={(event) => { event.preventDefault(); start() }} className="flex flex-wrap items-center gap-2">
              <input type="password" autoComplete="current-password" autoFocus aria-label="Your current password" placeholder="Your current password" value={setupPassword} onChange={(event) => setSetupPassword(event.target.value)} className="min-h-10 w-64 max-w-full rounded-xl border border-white/10 bg-[#111417] px-3 text-sm text-white" />
              <button type="submit" disabled={busy || !setupPassword} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-50">Continue</button>
              <button type="button" onClick={() => { setAskPassword(false); setSetupPassword('') }} className="text-xs text-[#9aa0a6] hover:text-white">Cancel</button>
              <span className="w-full text-xs text-[#7f8589]">Asked again because turning on two-factor is a sensitive change.</span>
            </form>
          )}
          {!setup && !askPassword && <button type="button" onClick={start} disabled={busy} className="rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-2.5 text-sm font-medium text-[#0b0b0b] disabled:opacity-50">Set up two-factor</button>}
          {setup && (
            <SecretsBox>
              <ol className="list-decimal space-y-3 pl-5 text-sm text-[#bcbcbc]">
                <li>In your authenticator app, add an account and enter this key (time-based, 6 digits):
                  <div className="mt-2 break-all rounded-lg bg-white/5 px-3 py-2 font-mono text-base tracking-wider text-white select-all">{secretText}</div>
                  <a href={setup.otpauth_uri} className="mt-1 inline-block text-xs text-emerald-300 underline">or open it in an authenticator app on this device</a>
                </li>
                <li>Enter the code the app shows now to confirm:
                  <form onSubmit={enable} className="mt-2 flex flex-wrap items-center gap-2">
                    <input type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={8} aria-label="Code from the authenticator app" placeholder="123456" value={code} onChange={(event) => setCode(event.target.value)} className={`${inputClass} w-40 font-mono tracking-widest`} required />
                    <button type="submit" disabled={busy} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-50">Turn on</button>
                  </form>
                </li>
              </ol>
            </SecretsBox>
          )}
        </div>
      )}

      {status.available && !codes && status.enabled && (
        <div className="space-y-3">
          <p className="text-sm text-emerald-300">Two-factor is on. <span className="text-[#9aa0a6]">{status.recovery_codes_left} recovery {status.recovery_codes_left === 1 ? 'code' : 'codes'} left.</span></p>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => setAction(action === 'codes' ? null : 'codes')} className={secondaryButtonClass}>New recovery codes</button>
            {!status.required && <button type="button" onClick={() => setAction(action === 'off' ? null : 'off')} className="min-h-10 sm:min-h-0 rounded-xl border border-red-400/30 bg-red-500/5 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10">Turn off</button>}
          </div>
          {status.required && <p className="text-xs text-[#7f8589]">It can't be turned off while it is required for administrators.</p>}
          {action && (
            <form onSubmit={action === 'off' ? reauth('/auth/2fa/disable', () => setMessage({ ok: true, text: 'Two-factor is off. Your other sessions were signed out.' })) : reauth('/auth/2fa/recovery-codes', (data) => setCodes(data.recovery_codes))} className="max-w-md space-y-2">
              <p className="text-xs text-[#9aa0a6]">Confirm with your password and a current code (or a recovery code).</p>
              <input type="password" autoComplete="current-password" aria-label="Password" placeholder="Password" value={form.password} onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))} className={inputClass} required />
              <input type="text" autoComplete="one-time-code" aria-label="Code" placeholder="Code" maxLength={32} value={form.code} onChange={(event) => setForm((current) => ({ ...current, code: event.target.value }))} className={`${inputClass} font-mono`} required />
              <button type="submit" disabled={busy} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-50">{action === 'off' ? 'Turn off two-factor' : 'Generate new codes'}</button>
            </form>
          )}
        </div>
      )}
      {message && <p className={`mt-3 text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
    </AdminSection>
  )
}

function SessionsSection() {
  const [sessions, setSessions] = useState(null)
  const [message, setMessage] = useState(null)
  const load = () => apiRequest('/auth/sessions', { method: 'GET' }).then((data) => setSessions(data.sessions)).catch((error) => setMessage({ ok: false, text: error.message || 'Could not load the sessions.' }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const end = async (id) => {
    setMessage(null)
    try { await apiRequest(`/auth/sessions/${id}`, { method: 'DELETE' }); await load() } catch (error) { setMessage({ ok: false, text: error.message || 'Could not sign that session out.' }) }
  }
  const endOthers = async () => {
    setMessage(null)
    try {
      const data = await apiRequest('/auth/sessions/revoke-others', { method: 'POST' })
      setMessage({ ok: true, text: `Signed out ${data.signed_out} other ${data.signed_out === 1 ? 'session' : 'sessions'}.` })
      await load()
    } catch (error) { setMessage({ ok: false, text: error.message || 'Could not sign the other sessions out.' }) }
  }

  return (
    <AdminSection title="Where you're signed in" description="Sign out any session you don't recognise." action={sessions?.length > 1 ? <button type="button" onClick={endOthers} className={secondaryButtonClass}>Sign out all others</button> : null}>
      {sessions === null ? <p className="text-sm text-[#9aa0a6]">Loading…</p> : (
        <ul className="space-y-2">{sessions.map((session) => (
          <li key={session.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-3 text-sm">
            <div className="min-w-0">
              <div className="truncate text-white">{session.user_agent || 'Unknown browser'}{session.current && <span className="ml-2 rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-emerald-300">THIS SESSION</span>}</div>
              <div className="text-xs text-[#9aa0a6]">{session.ip || 'unknown address'} · signed in {session.created_at ? formatDateTime(session.created_at) : 'earlier'}</div>
            </div>
            {!session.current && <button type="button" onClick={() => end(session.id)} className={secondaryButtonClass}>Sign out</button>}
          </li>
        ))}</ul>
      )}
      <p className="mt-3 text-xs text-[#7f8589]">Sessions that started before this feature was added don't appear here and expire on their own within a day.</p>
      {message && <p className={`mt-3 text-xs ${message.ok ? 'text-emerald-300' : 'text-amber-200'}`}>{message.text}</p>}
    </AdminSection>
  )
}

function AccountPage({ user, onLogout, onUserChange }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  // Remember that this visit began as a required setup, so Continue leads on to the dashboard once it is done.
  const wasRequired = useRef(false)
  if (status?.setup_required) wasRequired.current = true
  const reload = () => apiRequest('/auth/2fa/status', { method: 'GET' }).then((data) => { setStatus(data); onUserChange({ setup_required: data.setup_required, two_factor_enabled: data.enabled }) }).catch((requestError) => setError(requestError.message || 'Could not load your account.'))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload() }, [])

  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="flex flex-col gap-5">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Your account</div>
          <h1 className="mt-1 text-3xl font-semibold text-white">Account</h1>
          <p className="mt-2 text-sm text-[#9aa0a6]">{user.name} · {user.email} · {user.role}</p>
          {status?.setup_required && <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">Two-factor is required for administrators. Set it up below to continue to the dashboard.</div>}
          {error && <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
        </header>
        {status && <TwoFactorSection status={status} reload={reload} onEnrolled={wasRequired.current ? () => navigate('/') : null} />}
        {status && !status.setup_required && <PasswordSection />}
        {status && !status.setup_required && <SessionsSection />}
      </div>
    </AppShell>
  )
}

function App() {
  const [user, setUser] = useState(() => getStoredUser())
  const [users, setUsers] = useState(() => getStoredUsers())

  useEffect(() => {
    if (user) {
      window.localStorage.setItem(AUTH_KEY, JSON.stringify(user))
    } else {
      window.localStorage.removeItem(AUTH_KEY)
    }
  }, [user])

  useEffect(() => {
    if (!user) return

    apiRequest('/auth/me', {
      method: 'GET',
    }).then((data) => {
      setUser((currentUser) => ({ ...(currentUser || {}), ...data.user }))
    }).catch(() => {
      setUser(null)
    })
  }, [])

  useEffect(() => {
    window.localStorage.setItem(USERS_KEY, JSON.stringify(users))
  }, [users])

  const handleLogin = (nextUser) => {
    setUser(nextUser)
  }

  const handleRegister = (nextUser) => {
    setUser(nextUser)
  }

  const handleLogout = async () => {
    try {
      await apiRequest('/auth/logout', { method: 'POST' })
    } catch {
      // Clear local state even if the session has already expired.
    }
    setUser(null)
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage onLogin={handleLogin} />} />
        <Route path="/register" element={user ? <Navigate to="/" replace /> : <RegisterPage onRegister={handleRegister} />} />

        <Route
          path="/"
          element={
            <RequireAuth user={user}>
              <DashboardPage user={user} onLogout={handleLogout} />
            </RequireAuth>
          }
        />
        <Route path="/dashboard" element={<RequireAuth user={user}><Navigate to="/" replace /></RequireAuth>} />
        <Route
          path="/agent"
          element={
            <RequireAuth user={user}>
              <AgentPage user={user} onLogout={handleLogout} />
            </RequireAuth>
          }
        />
        <Route
          path="/statistics"
          element={
            <RequireAuth user={user}>
              <AppShell user={user} onLogout={handleLogout}>
                <StatisticsContent request={apiRequest} />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/account"
          element={
            <RequireAuth user={user}>
              <AccountPage user={user} onLogout={handleLogout} onUserChange={(patch) => setUser((current) => (current ? { ...current, ...patch } : current))} />
            </RequireAuth>
          }
        />
        <Route path="/reports" element={<RequireAuth user={user}><Navigate to="/agent" replace /></RequireAuth>} />
        <Route path="/logs" element={<RequireAuth user={user}><Navigate to="/agent" replace /></RequireAuth>} />
        <Route
          path="/admin"
          element={
            <RequireAuth user={user}>
              {user?.role === 'admin' ? <AdminPage user={user} onLogout={handleLogout} /> : <Navigate to="/" replace />}
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App
