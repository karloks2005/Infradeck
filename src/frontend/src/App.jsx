import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'

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
  { label: 'Agent reports', to: '/reports', icon: '◷' },
  { label: 'Admin', to: '/admin', icon: '⚙' },
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

function AppShell({ children, user, onLogout, fitViewport = false }) {
  return (
    <div className={`${fitViewport ? 'h-screen overflow-hidden' : 'min-h-screen'} bg-[#0d1013] text-[#e6e7ea]`}>
      <div className={`${fitViewport ? 'h-full min-h-0' : ''} mx-auto flex max-w-[1700px] gap-6 px-4 py-5 lg:px-6`}>
        <aside className="glass-panel hidden w-72 shrink-0 rounded-3xl p-4 lg:block">
          <div className="mb-6 flex items-center gap-3 px-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-linear-to-br from-white to-[#cfcfcf] text-sm font-bold text-slate-950">H</div>
            <div>
              <div className="text-sm font-medium text-[#cfcfcf]">Homie</div>
              <div className="text-xs text-[#9aa0a6]">Sysadmin agent</div>
            </div>
          </div>

          <nav className="space-y-2">
            {navigation.map((item) => (
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

        <main className={`${fitViewport ? 'min-h-0' : ''} flex-1`}>{children}</main>
      </div>
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
function useRegistrationEnabled() {
  const [enabled, setEnabled] = useState(null)
  useEffect(() => {
    let active = true
    apiRequest('/auth/config', { method: 'GET' })
      .then((data) => { if (active) setEnabled(Boolean(data.registration_enabled)) })
      .catch(() => { if (active) setEnabled(true) })
    return () => { active = false }
  }, [])
  return enabled
}

function LoginPage({ onLogin }) {
  const navigate = useNavigate()
  const registrationEnabled = useRegistrationEnabled()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')

    try {
      const data = await apiRequest('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })

      onLogin({
        id: data.user.id,
        name: data.user.name,
        email: data.user.email,
        role: data.user.role,
      })
      navigate('/')
    } catch (requestError) {
      setError(requestError.message || 'Invalid email or password.')
    }
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
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" defaultChecked className="h-4 w-4 rounded border-neutral-600 bg-neutral-800" />
            Remember me
          </label>
          <a href="#" className="text-emerald-300">Forgot password?</a>
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
  const registrationEnabled = useRegistrationEnabled()
  const [form, setForm] = useState({ name: '', email: '', password: '' })
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

    if (!trimmedName || !trimmedEmail || form.password.length < 6) {
      setError('Please provide a name, email, and a password with at least 6 characters.')
      return
    }

    try {
      const data = await apiRequest('/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          name: trimmedName,
          email: trimmedEmail,
          password: form.password,
        }),
      })

      onRegister({
        id: data.user.id,
        name: data.user.name,
        email: data.user.email,
        role: data.user.role,
      })
      navigate('/')
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
            placeholder="At least 6 characters"
            required
          />
        </label>

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

function DashboardPage({ user, onLogout }) {
  const [overview, setOverview] = useState({ servers: [], alerts: [] })
  const [error, setError] = useState('')
  const [showConnect, setShowConnect] = useState(false)
  const [deletingServerId, setDeletingServerId] = useState(null)
  const [history, setHistory] = useState([])

const refresh = async () => {
  // Ako je zahtjev već u tijeku, preskoči ovaj poziv
  if (isFetchingOverview) return
  isFetchingOverview = true

  try {
    const data = await apiRequest('/monitoring/overview', { method: 'GET' })
    setOverview(data)
    setError('')
    
    const metrics = (data.servers || []).map((server) => server.host).filter(Boolean)
    if (metrics.length) {
      const mean = (key) => metrics.reduce((sum, metric) => sum + (metric[key] || 0), 0) / metrics.length
      
      setHistory((current) => [
        ...current, 
        { 
          time: new Date(), 
          cpu: mean('cpu_utilization_pct'), 
          ram: mean('ram_pct'), 
          disk: mean('disk_usage_pct') 
        }
      ].slice(-40))
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

    const timer = setInterval(refresh, LIVE_GRAPH_REFRESH_INTERVAL_SECONDS * 1000)

    // Obavezno čišćenje timera pri unmountu
    return () => clearInterval(timer)
  }, [user?.id]) // <-- Stabilna instanca, okida se samo pri promjeni ID-a

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
  const servers = overview.servers || []
  const latestMetrics = servers.map((server) => server.host).filter(Boolean)
  const average = (key) => latestMetrics.length ? (latestMetrics.reduce((sum, metric) => sum + (metric[key] || 0), 0) / latestMetrics.length).toFixed(1) : '—'
  const online = servers.filter((server) => server.last_seen_at && Date.now() - new Date(server.last_seen_at).getTime() < server.check_interval_seconds * 3000).length
  const runningContainers = servers.reduce((sum, server) => sum + server.containers.filter((container) => container.status === 'running').length, 0)

  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="custom-scroll flex min-h-0 flex-col gap-5 overflow-y-auto pr-1">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div><div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Infrastructure overview</div><h1 className="mt-1 text-3xl font-semibold text-white">Dashboard</h1><p className="mt-2 text-sm text-[#9aa0a6]">Live host and container health · updated every 15 seconds.</p></div>
            <button type="button" onClick={() => setShowConnect((current) => !current)} aria-expanded={showConnect} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950">{showConnect ? 'Close' : 'Connect a server'}</button>
          </div>
          {error && <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
          {showConnect && <ConnectServerPanel />}
        </header>

        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[
            ['Servers online', `${online} / ${servers.length}`, 'Based on last check-in'],
            ['Average CPU', latestMetrics.length ? `${average('cpu_utilization_pct')}%` : '—', 'Across reporting servers'],
            ['Average memory', latestMetrics.length ? `${average('ram_pct')}%` : '—', 'RAM utilization'],
            ['Average disk', latestMetrics.length ? `${average('disk_usage_pct')}%` : '—', 'Disk utilization'],
            ['Containers running', String(runningContainers), 'Across monitored servers'],
            ['Active incidents', String(overview.alerts?.length || 0), 'Needs attention'],
          ].map(([label, value, detail]) => <div key={label} className="glass-panel rounded-[24px] p-5"><div className="text-sm text-[#9aa0a6]">{label}</div><div className="mt-3 text-3xl font-semibold text-white">{value}</div><div className="mt-2 text-xs text-[#7f8589]">{detail}</div></div>)}
        </section>

        <section className="grid gap-4 xl:grid-cols-3">
          {[
            { key: 'cpu', title: 'CPU utilization', color: '#34d399', latest: average('cpu_utilization_pct') },
            { key: 'ram', title: 'Memory utilization', color: '#60a5fa', latest: average('ram_pct') },
            { key: 'disk', title: 'Disk utilization', color: '#c084fc', latest: average('disk_usage_pct') },
          ].map((chart) => <MetricTrend key={chart.key} title={chart.title} color={chart.color} value={chart.latest} points={history.map((sample) => sample[chart.key])} times={history.map((sample) => sample.time)} />)}
        </section>

        <section className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="mb-4 flex items-center justify-between"><div><div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Fleet</div><h2 className="mt-1 text-xl font-semibold text-white">Monitored servers</h2></div><span className="text-sm text-[#9aa0a6]">{servers.length} configured</span></div>
          <PendingServers pending={overview.pending_collectors || []} onChanged={refresh} />
          {!servers.length ? <div className="rounded-2xl border border-dashed border-neutral-700 p-8 text-center"><div className="text-lg font-medium text-white">No servers registered</div><p className="mt-2 text-sm text-[#9aa0a6]">Click <span className="text-white">Connect a server</span> above and run the command on each machine you want to monitor.</p></div> : <div className="space-y-4">{servers.map((server) => {
            const metric = server.host
            const fresh = server.last_seen_at && Date.now() - new Date(server.last_seen_at).getTime() < server.check_interval_seconds * 3000
            return <article key={server.id} className="rounded-2xl border border-neutral-800 bg-[#0d1013]/70 p-4 md:p-5">
              <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className={`h-2.5 w-2.5 rounded-full ${fresh ? 'bg-emerald-400' : 'bg-neutral-600'}`} /><h3 className="text-lg font-medium text-white">{server.hostname}</h3></div><div className="mt-1 text-sm text-[#9aa0a6]">{server.os_info || server.ip_address || 'Waiting for first collector check-in'}{server.cpu_cores ? ` · ${server.cpu_cores} cores` : ''}</div></div><div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2 text-xs text-[#9aa0a6]">Check interval<select value={server.check_interval_seconds} onChange={(e) => changeInterval(server, e.target.value)} className="rounded-lg border border-neutral-700 bg-[#111417] px-2 py-1.5 text-sm text-white"><option value="15">15 sec</option><option value="30">30 sec</option><option value="60">1 min</option><option value="300">5 min</option><option value="900">15 min</option></select></label><button type="button" onClick={() => deleteServer(server)} disabled={deletingServerId === server.id} className="rounded-lg border border-red-400/30 bg-red-500/5 px-3 py-1.5 text-xs text-red-300 transition hover:bg-red-500/10 disabled:opacity-50">{deletingServerId === server.id ? 'Deleting…' : 'Delete server'}</button></div></div>
              {metric ? <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">{[['CPU', metric.cpu_utilization_pct, '%'], ['RAM', metric.ram_pct, '%'], ['Disk', metric.disk_usage_pct, '%'], ['Load (1m)', metric.load_1m, '']].map(([label, value, unit]) => <div key={label} className="rounded-xl bg-[#171b1f] p-3"><div className="text-xs text-[#9aa0a6]">{label}</div><div className="mt-1 text-xl font-semibold text-white">{value == null ? '—' : `${Number(value).toFixed(1)}${unit}`}</div></div>)}</div> : <div className="mt-4 rounded-xl bg-[#171b1f] p-4 text-sm text-[#9aa0a6]">Collector has not sent a measurement yet.</div>}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-[#7f8589]"><span>Last check: {server.last_seen_at ? new Date(server.last_seen_at).toLocaleString() : 'Never'}</span><span>{server.containers.length} containers</span></div>
              {server.containers.length > 0 && <ContainerTable server={server} />}
            </article>
          })}</div>}
        </section>
        <section className="glass-panel rounded-[28px] p-5 md:p-6"><div className="mb-4"><div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Alerts</div><h2 className="mt-1 text-xl font-semibold text-white">Active incidents</h2></div>{overview.alerts?.length ? <div className="space-y-2">{overview.alerts.map((alert) => <div key={alert.id} className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3"><div className="font-medium text-amber-200">{alert.title}</div><div className="mt-1 text-sm text-[#bcbcbc]">{alert.description}</div></div>)}</div> : <p className="text-sm text-[#9aa0a6]">No active incidents recorded.</p>}</section>
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

function ConnectServerPanel() {
  // API_BASE is usually relative ('/api'); collectors need the absolute address this dashboard is reached at.
  const [apiUrl, setApiUrl] = useState(() => new URL(API_BASE, window.location.origin).href.replace(/\/$/, ''))
  const url = apiUrl.trim().replace(/\/$/, '') || API_BASE
  const isLocalhost = /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(url)
  const dockerRun = `docker run -d --name homelab-collector --restart unless-stopped \\
  -e HOMELAB_API_URL=${url} \\
  -v /proc:/host/proc:ro -v /:/hostfs:ro \\
  -v /var/run/docker.sock:/var/run/docker.sock:ro \\
  -v homelab-collector:/var/lib/homelab-collector \\
  python:3.12-alpine sh -c 'wget -qO /tmp/collector.py "$HOMELAB_API_URL/collector.py" && exec python -u /tmp/collector.py'`

  return (
    <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4">
      <div className="font-medium text-cyan-100">Connect a server</div>
      <p className="mt-1 text-sm text-[#bcbcbc]">Run this once on the machine you want to monitor. Within a few seconds it shows up below under <span className="text-white">New servers detected</span>. Click <span className="text-white">Add</span> and it starts reporting. No keys, config files or restarts.</p>

      <label className="mt-4 block text-xs text-[#9aa0a6]">Dashboard address the new machine will use
        <input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} className="mt-1 w-full rounded-xl border border-neutral-700 bg-[#0d1013] px-3 py-2 text-sm text-white" aria-describedby="api-url-hint" />
      </label>
      {isLocalhost
        ? <p id="api-url-hint" className="mt-1 text-[11px] text-amber-200">Other machines can't reach <code>localhost</code>. Replace it with this machine's LAN IP, Tailscale address or domain (e.g. <code>http://192.168.1.10:5173/api</code>).</p>
        : <p id="api-url-hint" className="mt-1 text-[11px] text-[#7f8589]">Prefilled with the address you opened this dashboard at.</p>}

      <CopyableCommand label="Run on the new machine" text={dockerRun} />
      <p className="mt-2 text-[11px] text-[#7f8589]">The machine running this dashboard is detected automatically. The collector downloads itself from this dashboard on every start, so it stays up to date. Over plain HTTP, use it only on a trusted LAN or VPN.</p>

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
    setBusyId(item.id); setError('')
    try {
      await apiRequest(`/monitoring/pending-collectors/${item.id}/${action}`, { method: 'POST' })
      await onChanged()
    } catch (requestError) { setError(requestError.message || 'Could not update the server.') }
    finally { setBusyId(null) }
  }

  const row = (item) => (
    <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-neutral-800 bg-[#0d1013]/70 p-3">
      <div>
        <div className="font-medium text-white">{item.hostname}</div>
        <div className="mt-0.5 text-xs text-[#9aa0a6]">{[item.ip_address, item.os_info].filter(Boolean).join(' · ')} · code <span className="font-mono text-[#cfcfcf]">{item.pairing_code}</span></div>
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={() => act(item, 'approve')} disabled={busyId === item.id} className="rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-slate-950 disabled:opacity-50">{busyId === item.id ? 'Adding…' : 'Add'}</button>
        {item.status === 'pending' && <button type="button" onClick={() => act(item, 'dismiss')} disabled={busyId === item.id} className="rounded-lg border border-neutral-700 px-3 py-1.5 text-xs text-[#cfcfcf] disabled:opacity-50">Dismiss</button>}
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

function ContainerTable({ server }) {
  const [expandedId, setExpandedId] = useState(null)
  const toggle = (containerId) => setExpandedId((current) => (current === containerId ? null : containerId))

  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-[#7f8589]"><tr><th className="py-2">Container</th><th>Status</th><th>CPU</th><th>Memory</th><th>Restarts</th></tr></thead>
        <tbody>
          {server.containers.map((c) => {
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
                <td className="py-2"><span className={`mr-2 inline-block text-[10px] text-[#7f8589] transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>{c.name}</td>
                <td className={c.status === 'running' ? 'text-emerald-300' : 'text-amber-300'}>{c.status}</td>
                <td>{c.cpu_pct == null ? '—' : `${c.cpu_pct}%`}</td>
                <td>{c.memory_used_mb == null ? '—' : `${c.memory_used_mb} MB`}</td>
                <td>{c.restart_count}</td>
              </tr>,
              expanded && (
                <tr key={`${c.container_id}-details`}>
                  <td colSpan={5} className="pb-4"><ContainerDetails serverId={server.id} container={c} /></td>
                </tr>
              ),
            ]
          })}
        </tbody>
      </table>
    </div>
  )
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
          {points.length > 1 ? <svg viewBox="0 0 320 100" preserveAspectRatio="none" className="h-full w-full" role="img" aria-label={`${title} recent trend`}><defs><linearGradient id={`fill-${title.replaceAll(' ', '-')}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.28"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>{TREND_Y_TICKS.map((tick) => <line key={tick} x1="0" x2="320" y1={trendY(tick)} y2={trendY(tick)} stroke="#ffffff" strokeOpacity={tick % 20 === 0 ? 0.12 : 0.05} strokeWidth="1" vectorEffect="non-scaling-stroke" />)}<polygon points={areaPoints} fill={`url(#fill-${title.replaceAll(' ', '-')})`}/><polyline points={chartPoints} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/></svg> : <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-neutral-800 text-xs text-[#7f8589]">Trend appears as live samples arrive</div>}
        </div>
      </div>
      <div className="mt-2 flex justify-between pl-10 text-[10px] text-[#7f8589]"><span>{times[0] ? new Date(times[0]).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Waiting for data'}</span><span>{times.at(-1) ? new Date(times.at(-1)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}</span></div>
    </article>
  )
}

function getRecommendation(alert) {
  const context = `${alert.title} ${alert.description}`.toLowerCase()
  if (context.includes('unhealthy')) return ['Inspect the container healthcheck output and recent application logs.', 'Check whether a recent deployment or configuration change caused the failure.', 'After resolving the cause, verify the healthcheck returns to healthy.']
  if (context.includes('restart')) return ['Inspect the container logs and Docker exit status for the restart cause.', 'Check host memory and disk pressure, then review recent deployments.', 'Confirm the service stays running and its restart count stops increasing.']
  if (context.includes('cpu')) return ['Identify the process or container driving CPU usage.', 'Compare the increase with recent traffic and deployments.', 'Recheck CPU after mitigation and confirm the alert clears.']
  if (context.includes('memory') || context.includes('ram')) return ['Find the process or container consuming memory.', 'Check for a recent workload increase or memory leak.', 'Confirm available memory recovers after mitigation.']
  return ['Review the affected server and container metrics around this incident.', 'Inspect relevant service logs and recent changes.', 'Apply a fix, then confirm the next health checks are clear.']
}

function AgentReportsPage({ user, onLogout }) {
  const [overview, setOverview] = useState({ servers: [], alerts: [] })
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true

    const refresh = () => {
      apiRequest('/monitoring/overview', { method: 'GET' })
        .then((data) => { 
          if (active) { 
            setOverview(data)
            setError('') 
          } 
        })
        .catch((requestError) => { 
          if (active) setError(requestError.message || 'Could not load incident reports.') 
        })
    }

    refresh()
    const timer = setInterval(refresh, 15000)

    return () => { 
      active = false
      clearInterval(timer) 
    }
  }, [user?.id]) // <-- PROMIJENJENO: user?.id umjesto cijelog 'user' objekta

  const serverNames = Object.fromEntries((overview.servers || []).map((server) => [server.id, server.hostname]))
  const reports = overview.alerts || []

  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="custom-scroll flex min-h-0 flex-col gap-5 overflow-y-auto pr-1">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Incident center</div>
          <h1 className="mt-1 text-3xl font-semibold text-white">Agent reports</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[#9aa0a6]">
            Incident reports and suggested next steps. Recommendations are currently generated in the UI from incident details; no server-side AI analysis is connected.
          </p>
        </header>
        {error && <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
        {reports.length ? (
          <div className="space-y-4">
            {reports.map((report) => (
              <article key={report.id} className="glass-panel rounded-[24px] p-5 md:p-6">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${report.severity === 'CRITICAL' ? 'bg-red-400/10 text-red-300' : 'bg-amber-400/10 text-amber-200'}`}>
                        {report.severity}
                      </span>
                      <span className="text-xs text-[#9aa0a6]">{serverNames[report.server_id] || `Server ${report.server_id}`}</span>
                    </div>
                    <h2 className="mt-3 text-lg font-semibold text-white">{report.title}</h2>
                  </div>
                  <time className="text-xs text-[#7f8589]">{new Date(report.created_at).toLocaleString()}</time>
                </div>
                <p className="mt-2 text-sm text-[#bcbcbc]">{report.description}</p>
                <div className="mt-5 rounded-2xl border border-cyan-300/15 bg-cyan-300/5 p-4">
                  <div className="text-xs uppercase tracking-[0.18em] text-cyan-200">Suggested next steps</div>
                  <ol className="mt-3 space-y-2 text-sm leading-6 text-[#d1d5db]">
                    {getRecommendation(report).map((step, index) => (
                      <li key={step} className="flex gap-3">
                        <span className="font-mono text-cyan-300">0{index + 1}</span>
                        <span>{step}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="glass-panel rounded-[24px] p-10 text-center">
            <div className="text-lg font-medium text-white">No active incidents</div>
            <p className="mt-2 text-sm text-[#9aa0a6]">When monitoring detects an incident, it will appear here with suggested troubleshooting steps.</p>
          </div>
        )}
      </div>
    </AppShell>
  )
}

function AdminPage({ user, onLogout }) {
  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="grid gap-5 xl:grid-cols-[1.4fr_0.8fr]">
        <div className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="mb-4">
            <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Configuration</div>
            <h1 className="mt-1 text-3xl font-semibold text-white">Admin controls</h1>
          </div>

          <div className="space-y-5">
            <div>
              <label className="mb-2 block text-sm text-[#bcbcbc]">System prompt</label>
              <textarea
                rows={12}
                defaultValue={adminSettings.systemPrompt}
                className="w-full rounded-2xl border border-[#171b1f] bg-[#0d1013]/80 px-4 py-3 text-sm text-[#cfcfcf] outline-none transition focus:border-emerald-400"
              />
            </div>

            <div>
              <label className="mb-2 block text-sm text-[#bcbcbc]">Monitoring boundaries</label>
              <div className="space-y-3">
                {adminSettings.safetyRules.map((rule) => (
                  <label key={rule} className="flex items-center gap-3 rounded-2xl border border-[#111417] bg-[#0d1013]/60 px-3 py-2 text-sm text-slate-200">
                    <input type="checkbox" defaultChecked className="h-4 w-4 rounded border-[#1f2326] bg-[#111417]" />
                    {rule}
                  </label>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="mb-4 text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Safety preview</div>
          <div className="space-y-4">
            {[
              { label: 'Shell execution', value: 'Disabled', status: 'Enforced' },
              { label: 'Available checks', value: 'Closed allow-list', status: 'Enforced' },
              { label: 'Execution mode', value: 'Monitoring-only', status: 'Active' },
            ].map((item) => (
              <div key={item.label} className="rounded-2xl border border-[#111417] bg-[#0d1013]/70 p-3">
                <div className="text-sm text-[#bcbcbc]">{item.label}</div>
                <div className="mt-2 text-base font-medium text-white">{item.value}</div>
                <div className="mt-2 inline-flex rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1 text-[10px] uppercase tracking-[0.2em] text-emerald-300">{item.status}</div>
              </div>
            ))}

            <button className="mt-2 w-full rounded-2xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-3 font-medium text-[#0b0b0b]">Save changes</button>
          </div>
        </div>
      </div>
    </AppShell>
  )
}

function RequireAuth({ user, children }) {
  const location = useLocation()

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return children
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
          path="/reports"
          element={
            <RequireAuth user={user}>
              <AgentReportsPage user={user} onLogout={handleLogout} />
            </RequireAuth>
          }
        />
        <Route path="/logs" element={<RequireAuth user={user}><Navigate to="/reports" replace /></RequireAuth>} />
        <Route
          path="/admin"
          element={
            <RequireAuth user={user}>
              <AdminPage user={user} onLogout={handleLogout} />
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App
