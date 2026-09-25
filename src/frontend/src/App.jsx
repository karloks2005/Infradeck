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
  { label: 'Agent', to: '/agent', icon: '◷' },
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

function AppShell({ children, user, onLogout, fitViewport = false }) {
  return (
    <div className={`${fitViewport ? 'min-h-screen xl:h-screen xl:overflow-hidden' : 'min-h-screen'} bg-[#0d1013] text-[#e6e7ea]`}>
      <div className={`${fitViewport ? 'xl:h-full xl:min-h-0' : ''} mx-auto flex max-w-[1700px] gap-6 px-4 py-5 lg:px-6`}>
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

  // Regular users get a read-only view; the backend enforces the same rules.
  const isAdmin = user?.role === 'admin'

  return (
    <AppShell user={user} onLogout={onLogout}>
      <div className="flex flex-col gap-5">
        <header className="glass-panel rounded-[28px] p-5 md:p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div><div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Infrastructure overview</div><h1 className="mt-1 text-3xl font-semibold text-white">Dashboard</h1><p className="mt-2 text-sm text-[#9aa0a6]">Live host and container health · updated every 15 seconds.</p></div>
            {isAdmin && <button type="button" onClick={() => setShowConnect((current) => !current)} aria-expanded={showConnect} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950">{showConnect ? 'Close' : 'Connect a server'}</button>}
          </div>
          {error && <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error}</div>}
          {isAdmin && showConnect && <ConnectServerPanel />}
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
          {isAdmin && <PendingServers pending={overview.pending_collectors || []} onChanged={refresh} />}
          {!servers.length ? <div className="rounded-2xl border border-dashed border-neutral-700 p-8 text-center"><div className="text-lg font-medium text-white">No servers registered</div><p className="mt-2 text-sm text-[#9aa0a6]">{isAdmin ? <>Click <span className="text-white">Connect a server</span> above and run the command on each machine you want to monitor.</> : 'An admin needs to connect a server before it shows up here.'}</p></div> : <div className="space-y-4">{servers.map((server) => {
            const metric = server.host
            const fresh = server.last_seen_at && Date.now() - new Date(server.last_seen_at).getTime() < server.check_interval_seconds * 3000
            return <article key={server.id} className="rounded-2xl border border-neutral-800 bg-[#0d1013]/70 p-4 md:p-5">
              <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className={`h-2.5 w-2.5 rounded-full ${fresh ? 'bg-emerald-400' : 'bg-neutral-600'}`} /><h3 className="text-lg font-medium text-white">{server.hostname}</h3></div><div className="mt-1 text-sm text-[#9aa0a6]">{server.os_info || server.ip_address || 'Waiting for first collector check-in'}{server.cpu_cores ? ` · ${server.cpu_cores} cores` : ''}</div></div>{isAdmin ? <div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2 text-xs text-[#9aa0a6]">Check interval<select value={server.check_interval_seconds} onChange={(e) => changeInterval(server, e.target.value)} className="rounded-lg border border-neutral-700 bg-[#111417] px-2 py-1.5 text-sm text-white"><option value="15">15 sec</option><option value="30">30 sec</option><option value="60">1 min</option><option value="300">5 min</option><option value="900">15 min</option></select></label><button type="button" onClick={() => deleteServer(server)} disabled={deletingServerId === server.id} className="rounded-lg border border-red-400/30 bg-red-500/5 px-3 py-1.5 text-xs text-red-300 transition hover:bg-red-500/10 disabled:opacity-50">{deletingServerId === server.id ? 'Deleting…' : 'Delete server'}</button></div> : <span className="text-xs text-[#9aa0a6]">Checks every {server.check_interval_seconds < 60 ? `${server.check_interval_seconds} sec` : `${Math.round(server.check_interval_seconds / 60)} min`}</span>}</div>
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
          <p className="text-sm leading-6 text-[#bcbcbc]">{incident.details}</p>
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
                    <span>{action}</span>
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

        <div className="grid gap-5 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] xl:grid-rows-[minmax(0,1fr)]">
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
  storage: [
    ['Host metrics', '1,204,332 rows'],
    ['Container metrics', '8,930,118 rows'],
    ['Incidents', '214 rows'],
    ['Audit log', '3,871 rows'],
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
const secondaryButtonClass = 'rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-[#cfcfcf] transition hover:bg-white/10'
const dangerButtonClass = 'rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-300 transition hover:bg-red-500/15'

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
        className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 ${on ? 'bg-emerald-500/70' : 'bg-white/10'}`}
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

function AdminUsersTab() {
  return (
    <div className="space-y-5">
      <AdminSection title="Registration" description="Controls whether new people can create an account.">
        <Toggle label="Allow registration" description="When off, only the first account can be created. Replaces ALLOW_REGISTRATION in .env." />
      </AdminSection>

      <AdminSection title="Users" description="Change roles, disable accounts or sign someone out everywhere.">
        <div className="overflow-x-auto">
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
          <div className="flex gap-2">
            <input readOnly value="hlk_••••••••••••••••3f9a" aria-label="Enrollment key" className={`${inputClass} font-mono`} />
            <button type="button" className={secondaryButtonClass}>Regenerate</button>
            <button type="button" className={dangerButtonClass}>Revoke</button>
          </div>
        </AdminSection>

        <AdminSection title="Waiting for approval" description="Collectors that asked to join without a key.">
          <div className="space-y-2">
            {placeholderAdmin.pending.map((request) => (
              <div key={request.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-400/20 bg-amber-400/5 px-4 py-3">
                <div><div className="text-sm text-white">{request.hostname}</div><div className="text-xs text-[#9aa0a6]">{request.ip} · {formatDateTime(request.requestedAt)}</div></div>
                <div className="flex gap-2"><button type="button" className={secondaryButtonClass}>Approve</button><button type="button" className={dangerButtonClass}>Dismiss</button></div>
              </div>
            ))}
          </div>
        </AdminSection>
      </div>

      <AdminSection title="Servers" description="Every monitored machine, its collector and how often it reports.">
        <div className="overflow-x-auto">
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

function AdminAlertsTab() {
  const thresholds = [
    ['Disk usage above', 90, '%'],
    ['Memory usage above', 90, '%'],
    ['CPU usage above (for 10 min)', 95, '%'],
    ['Container restarts within 30 min', 3, 'restarts'],
  ]

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <AdminSection title="Webhook" description="Receives a JSON POST for every critical incident. Replaces ADMIN_ALERT_WEBHOOK_URL in .env." action={<SaveButton />}>
        <div className="space-y-3">
          <input type="url" placeholder="https://your-alert-endpoint.example/hook" aria-label="Webhook URL" className={inputClass} />
          <button type="button" className={secondaryButtonClass}>Send test alert</button>
        </div>
      </AdminSection>

      <AdminSection title="Incident thresholds" description="When monitoring opens an incident." action={<SaveButton />}>
        <div className="space-y-2">
          {thresholds.map(([label, value, unit]) => (
            <label key={label} className="flex items-center justify-between gap-4 rounded-2xl border border-[#1f2326] bg-[#0d1013]/60 px-4 py-2.5 text-sm">
              <span className="text-[#bcbcbc]">{label}</span>
              <span className="flex items-center gap-2">
                <input type="number" defaultValue={value} min={0} className="w-20 rounded-xl border border-[#1f2326] bg-[#0d1013] px-2 py-1 text-right text-white outline-none focus:border-white/30" />
                <span className="w-16 text-xs text-[#9aa0a6]">{unit}</span>
              </span>
            </label>
          ))}
        </div>
      </AdminSection>
    </div>
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
      <div className="grid gap-5 xl:grid-cols-3">
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

        <AdminSection title="Database">
          <div className="space-y-2">
            {placeholderAdmin.storage.map(([label, value]) => (
              <div key={label} className="flex items-center justify-between text-sm"><span className="text-[#9aa0a6]">{label}</span><span className="text-white">{value}</span></div>
            ))}
          </div>
        </AdminSection>
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
          <div role="tablist" aria-label="Admin sections" className="mt-5 flex flex-wrap gap-2">
            {adminTabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`rounded-2xl px-4 py-2 text-sm transition ${activeTab === tab.id ? 'bg-[#171b1f] text-white ring-1 ring-white/10' : 'text-[#bcbcbc] hover:bg-[#111417] hover:text-white'}`}
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
          path="/agent"
          element={
            <RequireAuth user={user}>
              <AgentPage user={user} onLogout={handleLogout} />
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
