import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'

const AUTH_KEY = 'homie-auth-user'
const USERS_KEY = 'homie-auth-users'
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function createChatThread() {
  return {
    id: typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `chat-${Date.now()}`,
    title: 'New chat',
    preview: 'Start a task with Homie',
    time: 'now',
    unread: 0,
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
    throw new Error(errorBody.detail || 'Request failed.')
  }

  return response.json()
}

function parseAgentMessage(text) {
  const commandMatch = text.match(/\[COMMAND\]:\s*(.+)/)
  const riskMatch = text.match(/\[RISK\]:\s*(VERY LOW|LOW|MEDIUM|HIGH|VERY HIGH)/)
  const explanation = text.split('[RUN]')[0].trim() || text

  return {
    text: explanation,
    command: commandMatch ? commandMatch[1].trim() : undefined,
    risk: riskMatch ? riskMatch[1].toLowerCase().replace(' ', '-') : 'low',
  }
}

const navigation = [
  { label: 'Landing', to: '/', icon: '◈' },
  { label: 'Dashboard', to: '/dashboard', icon: '▣' },
  { label: 'Logs', to: '/logs', icon: '☰' },
  { label: 'Admin', to: '/admin', icon: '⚙' },
]

const conversationThreads = [
  {
    id: 1,
    title: 'Container drift',
    preview: 'Nginx still returning 502 after restart',
    time: '2m ago',
    unread: 4,
    messages: [
      {
        role: 'user',
        text: 'Check nginx and explain what happened before the 502 started.',
        risk: 'neutral',
      },
      {
        role: 'agent',
        text: 'The upstream config file was stale after a config change, and nginx kept routing to a dead socket. I would reload the config and confirm the service is listening on the expected port before restarting the container.',
        risk: 'low',
        command:
          '[RUN]\n[RISK]: LOW\n[COMMAND-LOG]: Check nginx upstream and reload config\n[COMMAND]: docker logs nginx --tail 80 && nginx -t && systemctl reload nginx\n[EOT]',
      },
      {
        role: 'user',
        text: 'Can you continue from there and fix it safely?',
        risk: 'neutral',
      },
      {
        role: 'agent',
        text: 'The service is healthy again. I kept the change non-destructive and only reloaded the config after verifying that the socket target was valid. There was no broad system change, only targeted nginx recovery.',
        risk: 'medium',
        command:
          '[PARTIAL]\n[RUN]\n[RISK]: LOW\n[COMMAND-LOG]: Validate recovered upstream and confirm status\n[COMMAND]: systemctl status nginx --no-pager && ss -lnt | grep 443\n[EOT]',
      },
    ],
  },
  {
    id: 2,
    title: 'SSL renewal',
    preview: 'Cert expired on staging domain',
    time: '14m ago',
    unread: 1,
    messages: [
      { role: 'user', text: 'Renew the staging certificate and confirm the domain is valid.', risk: 'neutral' },
      { role: 'agent', text: 'The cert was close to expiry and the renewal check failed. I will re-run certbot in dry-run mode and confirm the config before updating the certificate.', risk: 'medium', command: '[RUN]\n[RISK]: MEDIUM\n[COMMAND-LOG]: Validate cert renewal and dry-run update\n[COMMAND]: certbot renew --dry-run --deploy-hook "/bin/systemctl reload nginx"\n[EOT]' },
    ],
  },
  {
    id: 3,
    title: 'Backups',
    preview: 'Local backup check failed on RAID 1',
    time: '1h ago',
    unread: 0,
    messages: [
      { role: 'user', text: 'The backup job failed on the RAID volume. What should I check?', risk: 'neutral' },
      { role: 'agent', text: 'The backup target is likely out of space or the mount is stale. I flagged the volume check first, then the scrub status, before suggesting a full restore path.', risk: 'low', command: '[RUN]\n[RISK]: LOW\n[COMMAND-LOG]: Check volume health and backup path\n[COMMAND]: df -h /srv && lsblk && mdadm --detail /dev/md0\n[EOT]' },
    ],
  },
  {
    id: 4,
    title: 'Docker cleanup',
    preview: 'Orphan volumes consuming 14GB',
    time: '3h ago',
    unread: 0,
    messages: [
      { role: 'user', text: 'Clean up the unused Docker volumes safely and keep the app healthy.', risk: 'neutral' },
      { role: 'agent', text: 'I avoided removing anything still referenced by a running container. The cleanup plan is selective and should only remove orphaned volume entries not attached to active services.', risk: 'high', command: '[RUN]\n[RISK]: HIGH\n[COMMAND-LOG]: Inspect and prune orphaned data volumes\n[COMMAND]: docker volume ls && docker volume prune -f\n[EOT]' },
    ],
  },
]

const stats = [
  { label: 'Commands run', value: '1,284', delta: '+17.2%', accent: 'emerald' },
  { label: 'Avg. response', value: '3.8s', delta: '-0.6s', accent: 'silver' },
  { label: 'Uptime', value: '99.97%', delta: '+0.3%', accent: 'violet' },
  { label: 'Guardrail blocks', value: '12', delta: '-5', accent: 'amber' },
]

const logEntries = [
  { id: 'nginx-502', type: 'COMPLETED', taskTitle: 'Resolve nginx 502 after upstream drift', message: 'Recovered the nginx upstream and confirmed the service was healthy.', time: '08:42:02', actor: 'homie', steps: ['Inspected nginx logs and the active upstream configuration.', 'Validated the target socket and tested the nginx configuration.', 'Reloaded nginx and confirmed the service was listening.'] },
  { id: 'ssl-renewal', type: 'COMPLETED', taskTitle: 'Validate staging certificate renewal', message: 'Validated the renewal path without changing the live certificate.', time: '07:51:16', actor: 'homie', steps: ['Checked the certificate expiry and renewal configuration.', 'Ran certbot in dry-run mode.', 'Confirmed the deployment hook would reload nginx safely.'] },
  { id: 'backup-check', type: 'ATTENTION', taskTitle: 'Investigate failed RAID backup', message: 'Checked the backup volume and identified the next recovery checks.', time: '06:20:44', actor: 'homie', steps: ['Checked available space on the backup mount.', 'Inspected block devices and RAID status.', 'Recorded the failed volume check for follow-up.'] },
]

const adminSettings = {
  systemPrompt: 'Name: Homie (Homelab sysadmin AI).\n\n[ROLE & OUTPUT]\nExpert Linux SysAdmin AI. Concise, technical, direct.\nStructure: 1. Short plain-text explanation. 2. Command block at VERY END (only if execution needed).',
  guardrails: [
    'Non-interactive only for shell commands',
    'No destructive filesystem actions',
    'Use safe restart checks before service changes',
    'Require explicit approval for high-risk tasks',
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
    if (!raw) {
      const demoUser = [{ id: 'demo-user', name: 'Demo User', email: 'admin@example.com', password: 'admin123' }]
      window.localStorage.setItem(USERS_KEY, JSON.stringify(demoUser))
      return demoUser
    }
    return JSON.parse(raw)
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

function LoginPage({ onLogin }) {
  const navigate = useNavigate()
  const [email, setEmail] = useState('admin@example.com')
  const [password, setPassword] = useState('admin123')
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
    <AuthCard title="Login" subtitle="Secure access" footerText="Need an account?" footerLink="/register" footerLinkText="Register here">
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
    </AuthCard>
  )
}

function LandingPage({ chatThreads, activeThreadId, onSelectChat, user, onLogout }) {
  const [threads, setThreads] = useState(() => (chatThreads.length ? chatThreads : [createChatThread()]))
  const [messageInput, setMessageInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const messageScrollerRef = useRef(null)
  const scrollAnimationRef = useRef(null)
  const activeThread = threads.find((thread) => thread.id === activeThreadId) || threads[0]

  useEffect(() => {
    const element = messageScrollerRef.current
    if (!element) return undefined

    if (scrollAnimationRef.current) {
      cancelAnimationFrame(scrollAnimationRef.current)
    }

    const startTop = element.scrollTop
    const targetTop = element.scrollHeight - element.clientHeight
    const distance = targetTop - startTop
    if (distance <= 1) return undefined

    const duration = Math.min(520, Math.max(260, distance * 0.7))
    const startTime = performance.now()
    const easeInOutCubic = (progress) => (
      progress < 0.5
        ? 4 * progress * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 3) / 2
    )

    const animate = (currentTime) => {
      const progress = Math.min((currentTime - startTime) / duration, 1)
      element.scrollTop = startTop + distance * easeInOutCubic(progress)
      if (progress < 1) {
        scrollAnimationRef.current = requestAnimationFrame(animate)
      } else {
        scrollAnimationRef.current = null
      }
    }

    scrollAnimationRef.current = requestAnimationFrame(animate)
    return () => {
      if (scrollAnimationRef.current) cancelAnimationFrame(scrollAnimationRef.current)
    }
  }, [activeThread?.id, activeThread?.messages.length])

  const handleSend = async (event) => {
    event.preventDefault()
    const message = messageInput.trim()
    if (!message || isSending || !activeThread) return

    setMessageInput('')
    setSendError('')
    setIsSending(true)
    setThreads((currentThreads) => currentThreads.map((thread) => (
      thread.id === activeThread.id
        ? {
            ...thread,
            title: thread.title === 'New chat' ? message.slice(0, 42) : thread.title,
            preview: message,
            messages: [...thread.messages, { role: 'user', text: message, risk: 'neutral' }],
          }
        : thread
    )))

    try {
      const data = await apiRequest('/agent/chat', {
        method: 'POST',
        body: JSON.stringify({ conversation_id: activeThread.id, message }),
      })
      const agentMessage = parseAgentMessage(data.message)
      setThreads((currentThreads) => currentThreads.map((thread) => (
        thread.id === activeThread.id
          ? { ...thread, messages: [...thread.messages, { role: 'agent', ...agentMessage }] }
          : thread
      )))
    } catch (requestError) {
      setSendError(requestError.message || 'Agent is unavailable.')
    } finally {
      setIsSending(false)
    }
  }

  return (
    <AppShell user={user} onLogout={onLogout} fitViewport>
      <div className="flex h-full min-h-0 flex-col gap-5 lg:flex-row">
        <aside className="glass-panel flex h-[250px] min-h-0 shrink-0 flex-col rounded-[28px] p-4 lg:h-auto lg:w-[330px]">
          <div className="mb-4 flex items-center justify-between px-2">
            <div>
              <div className="text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">Chats</div>
              <h2 className="mt-1 text-xl font-semibold text-white">Sessions</h2>
            </div>
            <button
              type="button"
              onClick={() => {
                const newThread = createChatThread()
                setThreads((currentThreads) => [newThread, ...currentThreads])
                onSelectChat(newThread.id)
                setMessageInput('')
                setSendError('')
              }}
              className="rounded-xl border border-neutral-700 bg-neutral-800/80 px-2.5 py-1.5 text-sm text-[#cfcfcf]"
            >
              + New
            </button>
          </div>

          <div className="custom-scroll min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {threads.map((chat) => (
              <button
                key={chat.id}
                onClick={() => onSelectChat(chat.id)}
                className={`w-full rounded-2xl border p-3 text-left transition ${
                  chat.id === activeThread.id
                    ? 'border-white/20 bg-white/5'
                    : 'border-neutral-700/80 bg-neutral-900/60 hover:border-neutral-600'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-white">{chat.title}</div>
                    <div className="mt-1 line-clamp-2 text-xs text-[#9aa0a6]">{chat.preview}</div>
                  </div>
                  {chat.unread > 0 && (
                    <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-emerald-400 px-1.5 text-[10px] font-semibold text-[#0b0b0b]">
                      {chat.unread}
                    </span>
                  )}
                </div>
                <div className="mt-3 text-[10px] uppercase tracking-[0.18em] text-[#7f8589]">{chat.time}</div>
              </button>
            ))}
          </div>
        </aside>

        <section className="glass-panel flex min-h-0 flex-1 flex-col rounded-[28px] p-4 md:p-5">
          <div className="flex flex-col gap-4 border-b border-neutral-800 pb-4 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Active chat</div>
              <h1 className="mt-1 text-2xl font-semibold text-white md:text-3xl">{activeThread?.title || 'New chat'}</h1>
            </div>
            <div className="flex items-center gap-2">
              <div className="inline-flex items-center rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300">
                <span className="mr-2 h-2 w-2 rounded-full bg-emerald-400" />
                Live
              </div>
              <button className="rounded-xl border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-[#cfcfcf]">Resume</button>
            </div>
          </div>

          <div ref={messageScrollerRef} className="custom-scroll min-h-0 flex-1 space-y-4 overflow-y-auto py-4 pr-1">
            {activeThread?.messages.map((message, index) => {
              const riskStyles = getRiskStyles(message.risk, message.role)

              return (
                <div key={index} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-4xl rounded-[22px] border px-4 py-3 md:px-5 ${
                      message.role === 'user'
                        ? 'bg-linear-to-r from-white to-[#cfcfcf] text-[#0b0b0b] shadow-lg shadow-white/10 border-white/60'
                        : `${riskStyles.card} message-card text-[#e6e7ea]`
                    }`}
                  >
                    <div className="mb-2 flex items-center justify-between gap-3 text-[10px] uppercase tracking-[0.2em] opacity-80">
                      <div className="flex items-center gap-2">
                        <span>{message.role === 'user' ? 'User' : 'Homie'}</span>
                        {message.role !== 'user' && (
                          <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 ${riskStyles.badge}`}>
                            <span className={`h-1.5 w-1.5 rounded-full ${riskStyles.dot}`} />
                            {message.risk || 'neutral'}
                          </span>
                        )}
                      </div>
                      <span>{message.role === 'user' ? 'now' : 'just now'}</span>
                    </div>
                    <div className="text-sm leading-7 md:text-[15px]">{message.text}</div>
                    {message.command && (
                      <pre className="command-block mt-3 overflow-x-auto rounded-2xl px-3 py-3 text-[11px] leading-6 whitespace-pre-wrap text-emerald-100">
                        {message.command}
                      </pre>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          <form onSubmit={handleSend} className="mt-4 rounded-[24px] border border-[#171b1f] bg-[#0d1013]/80 p-3">
            <div className="flex flex-col gap-3 md:flex-row md:items-center">
              <button type="button" className="rounded-xl border border-[#171b1f] bg-[#111417] px-3 py-2 text-sm text-[#cfcfcf]">Attach</button>
              <input
                type="text"
                value={messageInput}
                onChange={(event) => setMessageInput(event.target.value)}
                placeholder="Ask Homie to inspect a service or solve a task..."
                className="flex-1 rounded-xl border border-[#171b1f] bg-[#070809] px-4 py-3 text-sm text-[#cfcfcf] outline-none focus:border-emerald-400"
              />
              <button type="submit" disabled={isSending || !messageInput.trim()} className="rounded-xl bg-linear-to-r from-white to-[#cfcfcf] px-4 py-3 text-sm font-medium text-[#0b0b0b] disabled:cursor-not-allowed disabled:opacity-50">{isSending ? 'Working...' : 'Send'}</button>
            </div>
            {sendError && <div className="mt-2 text-sm text-red-300">{sendError}</div>}
          </form>
        </section>
      </div>
    </AppShell>
  )
}

function DashboardPage({ user, onLogout }) {
  const [summary, setSummary] = useState(null)

  useEffect(() => {
    apiRequest('/dashboard/summary', { method: 'GET' })
      .then((data) => setSummary(data))
      .catch(() => setSummary(null))
  }, [user])

  const dashboardStats = summary
    ? [
        { label: 'Commands run', value: summary.commands_run.toLocaleString(), delta: '+17.2%', accent: 'emerald' },
        { label: 'Total users', value: summary.total_users.toString(), delta: '+1', accent: 'silver' },
        { label: 'Uptime', value: summary.uptime, delta: '+0.3%', accent: 'violet' },
        { label: 'Guardrail blocks', value: summary.error_logs.toString(), delta: '-5', accent: 'amber' },
      ]
    : stats

  return (
    <AppShell user={user} onLogout={onLogout} fitViewport>
      <div className="custom-scroll flex h-full min-h-0 flex-col gap-5 overflow-y-auto pr-1">
        <div className="glass-panel shrink-0 rounded-[28px] p-5 md:p-6">
          <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Overview</div>
              <h1 className="mt-1 text-3xl font-semibold text-white">Agent operations</h1>
            </div>
            <button className="rounded-xl border border-[#171b1f] bg-[#0d1013] px-3 py-2 text-sm text-[#cfcfcf]">Export report</button>
          </div>
        </div>

        <div className="grid shrink-0 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {dashboardStats.map((stat) => (
            <div key={stat.label} className="glass-panel rounded-[24px] p-5">
              <div className={`inline-flex rounded-2xl bg-linear-to-r p-2 ring-1 ${getAccentClasses(stat.accent)}`}>
                <div className="text-lg">▣</div>
              </div>
              <div className="mt-4 text-3xl font-semibold text-white">{stat.value}</div>
              <div className="mt-2 flex items-center justify-between">
                <span className="text-sm text-[#bcbcbc]">{stat.label}</span>
                <span className="text-xs font-medium text-emerald-300">{stat.delta}</span>
              </div>
            </div>
          ))}
        </div>

        <div className="grid min-h-0 flex-1 grid-rows-2 gap-5 xl:grid-cols-[1.4fr_0.8fr] xl:grid-rows-1">
          <div className="glass-panel flex h-full min-h-0 flex-col rounded-[28px] p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Execution trend</div>
                <h2 className="mt-1 text-xl font-semibold text-white">This week</h2>
              </div>
              <div className="text-sm text-emerald-300">+28.4%</div>
            </div>
            <div className="flex min-h-0 flex-1 items-end gap-3">
              {[30, 48, 62, 55, 76, 84, 96].map((bar, index) => (
                <div key={index} className="flex flex-1 flex-col items-center gap-2">
                  <div className="w-full rounded-t-2xl bg-linear-to-t from-[#f5f5f6] to-[#cfcfcf]" style={{ height: `${bar}%` }} />
                  <span className="text-[10px] uppercase tracking-[0.2em] text-[#7f8589]">{['M', 'T', 'W', 'T', 'F', 'S', 'S'][index]}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="glass-panel h-full min-h-0 rounded-[28px] p-5">
            <div className="mb-4 text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Tasks</div>
            <div className="space-y-3">
              {[
                { label: 'Service checks', value: '168', color: 'bg-emerald-400' },
                { label: 'Docker cleanup', value: '34', color: 'bg-[#cfcfcf]' },
                { label: 'Backup validation', value: '17', color: 'bg-violet-400' },
              ].map((task) => (
                <div key={task.label}>
                  <div className="mb-1 flex items-center justify-between text-sm text-[#bcbcbc]">
                    <span>{task.label}</span>
                    <span>{task.value}</span>
                  </div>
                  <div className="h-2 rounded-full bg-[#111417]">
                    <div className={`${task.color} h-2 rounded-full`} style={{ width: `${task.value.includes('168') ? '82%' : task.value.includes('34') ? '58%' : '42%'}` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  )
}

function LogsPage({ onOpenThread, user, onLogout }) {
  const [entries, setEntries] = useState(logEntries)
  const [expandedId, setExpandedId] = useState(null)

  useEffect(() => {
    apiRequest('/logs', { method: 'GET' })
      .then((data) => {
        const mapped = (data.logs || []).map((entry) => ({
          id: entry.id,
          type: entry.level === 'ERROR' || entry.level === 'WARN' ? 'ATTENTION' : 'COMPLETED',
          taskTitle: entry.task_title || entry.message,
          message: entry.message,
          time: new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          actor: entry.source || 'system',
          steps: entry.steps || [],
        }))
        setEntries(mapped)
      })
      .catch(() => setEntries(logEntries))
  }, [user])

  return (
    <AppShell user={user} onLogout={onLogout} fitViewport>
      <div className="glass-panel flex h-full min-h-0 flex-col rounded-[28px] p-5 md:p-6">
        <div className="mb-5 flex shrink-0 flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.22em] text-[#9aa0a6]">Event stream</div>
            <h1 className="mt-1 text-3xl font-semibold text-white">Logs</h1>
          </div>
          <div className="flex gap-2">
            <button className="rounded-xl border border-[#171b1f] bg-[#0d1013] px-3 py-2 text-sm text-slate-200">All</button>
            <button className="rounded-xl border border-[#171b1f] bg-[#0d1013] px-3 py-2 text-sm text-slate-200">Errors</button>
            <button className="rounded-xl border border-[#171b1f] bg-[#0d1013] px-3 py-2 text-sm text-slate-200">Warnings</button>
          </div>
        </div>

        <div className="custom-scroll min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          {entries.map((entry) => (
            <article
              key={`${entry.time}-${entry.message}`}
              className="rounded-2xl border border-[#111417] bg-[#0d1013]/70 transition hover:border-[#1f2326] hover:bg-[#0d1013]"
            >
              <button
                type="button"
                aria-expanded={expandedId === entry.id}
                onClick={() => setExpandedId((currentId) => (currentId === entry.id ? null : entry.id))}
                className="flex w-full items-start gap-3 p-4 text-left"
              >
                <div className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${entry.type === 'ATTENTION' ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                    <div className="min-w-0">
                      <div className="text-base font-medium text-white">{entry.taskTitle}</div>
                      <div className="mt-1 line-clamp-1 text-sm text-[#9aa0a6]">{entry.message}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <span className={`text-xs uppercase tracking-[0.18em] ${entry.type === 'ATTENTION' ? 'text-amber-300' : 'text-emerald-300'}`}>{entry.type}</span>
                      <span className="text-xs text-[#7f8589]">{entry.time}</span>
                      <span className="text-xs text-[#7f8589]">{expandedId === entry.id ? 'Hide' : 'Details'}</span>
                    </div>
                  </div>
                  <div className="mt-2 text-xs uppercase tracking-[0.2em] text-[#7f8589]">Completed by {entry.actor}</div>
                </div>
              </button>
              {expandedId === entry.id && (
                <div className="border-t border-[#111417] px-4 pb-4 pt-3 pl-11">
                  <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#9aa0a6]">What Homie did</div>
                  <ul className="space-y-2 text-sm leading-6 text-[#cfcfcf]">
                    {entry.steps.length > 0 ? entry.steps.map((step) => <li key={step} className="flex gap-2"><span className="text-emerald-300">•</span><span>{step}</span></li>) : <li className="text-[#9aa0a6]">No step details were recorded for this task.</li>}
                  </ul>
                </div>
              )}
            </article>
          ))}
        </div>
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
              <label className="mb-2 block text-sm text-[#bcbcbc]">Guardrail rules</label>
              <div className="space-y-3">
                {adminSettings.guardrails.map((rule) => (
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
              { label: 'Auto approval', value: 'Low risk only', status: 'Enabled' },
              { label: 'Critical actions', value: 'Requires confirmation', status: 'Guarded' },
              { label: 'Execution mode', value: 'Read-only by default', status: 'Active' },
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
  const [activeThreadId, setActiveThreadId] = useState(null)
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
              <LandingPage
                chatThreads={[]}
                activeThreadId={activeThreadId}
                onSelectChat={setActiveThreadId}
                user={user}
                onLogout={handleLogout}
              />
            </RequireAuth>
          }
        />
        <Route
          path="/dashboard"
          element={
            <RequireAuth user={user}>
              <DashboardPage user={user} onLogout={handleLogout} />
            </RequireAuth>
          }
        />
        <Route
          path="/logs"
          element={
            <RequireAuth user={user}>
              <LogsPage onOpenThread={setActiveThreadId} user={user} onLogout={handleLogout} />
            </RequireAuth>
          }
        />
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
