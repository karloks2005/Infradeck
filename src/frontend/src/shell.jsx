// The frame around every signed-in page: sidebar with fleet status, top bar with the live indicator and the
// jump-to palette. Tablets get an icon rail instead of the sidebar; phones a page header and a tab bar
// (Overview, Statistics, Agent, More), with Account, Admin and the fleet under More.
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { LIVE_REFRESH_SECONDS, apiRequest } from './api.js'
import { liveRefreshSeconds } from './prefs.js'
import { isOnline } from './fleetLogic.js'
import { Icon } from './icons.jsx'
import { formatQuiet, listenerRows, serverState, serverStateLabel, sortServers } from './dashboard/logic.js'
import { useOverview } from './overviewStore.js'

const SIDEBAR_REFRESH_SECONDS = 15
// "Stale" only once a few refreshes in a row were missed, however slow the chosen refresh is.
const staleAfter = () => Math.max(30, liveRefreshSeconds(LIVE_REFRESH_SECONDS) * 2 + 5)

const primaryNav = [
  { label: 'Overview', to: '/', icon: 'overview', keys: 'O' },
  { label: 'Statistics', to: '/statistics', icon: 'statistics', keys: 'S' },
  { label: 'Agent', to: '/agent', icon: 'agent', keys: 'A' },
]
const secondaryNav = [
  { label: 'Account', to: '/account', icon: 'account' },
  { label: 'Admin', to: '/admin', icon: 'admin', adminOnly: true },
]
const pageTitles = { '/': 'Overview', '/statistics': 'Statistics', '/agent': 'Agent', '/account': 'Account', '/admin': 'Admin' }

export const serverLink = (serverId, tab) => `/?server=${serverId}${tab && tab !== 'overview' ? `&tab=${tab}` : ''}`

// First and last name: Karlo Kajba Šimanić -> KŠ.
const initials = (name) => { const parts = (name || '?').trim().split(/\s+/); return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase() }
const roleLabel = (role) => (role === 'admin' ? 'Owner' : 'Member')
const instanceName = (user) => `${(user?.name || 'my').trim().split(/\s+/)[0].toLowerCase()}'s homelab`
const isTyping = (target) => target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))

const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }

// The share of expected check-ins that arrived this week, averaged over the fleet. Asked for rarely: it changes slowly.
let weekAvailability = { value: null, loadedAt: 0 }
function useWeekAvailability(enabled) {
  const [value, setValue] = useState(weekAvailability.value)
  useEffect(() => {
    if (!enabled) return undefined
    let active = true
    const load = () => {
      if (Date.now() - weekAvailability.loadedAt < 10 * 60 * 1000) return
      apiRequest('/stats/fleet?range=7d', { method: 'GET' }).then((data) => {
        const known = (data.availability || []).map((row) => row.percent).filter((percent) => percent != null)
        weekAvailability = { value: known.length ? known.reduce((sum, percent) => sum + percent, 0) / known.length : null, loadedAt: Date.now() }
        if (active) setValue(weekAvailability.value)
      }).catch(() => {})
    }
    load()
    const timer = setInterval(load, 60 * 1000)
    return () => { active = false; clearInterval(timer) }
  }, [enabled])
  return value
}

function NavItem({ item, badge }) {
  return (
    <NavLink to={item.to} end={item.to === '/'} className={({ isActive }) => `relative flex h-8 items-center gap-3 rounded-md px-2.5 text-[13px] font-medium transition-colors ${isActive ? 'bg-raised text-paper' : 'text-soft hover:bg-ink hover:text-paper'}`}>
      {({ isActive }) => <>
        {isActive && <span aria-hidden="true" className="absolute -left-3.5 top-0 h-full w-[3px] rounded-r-sm bg-signal" />}
        <Icon name={item.icon} className={isActive ? 'text-paper' : 'text-muted'} />
        <span className="flex-1">{item.label}</span>
        {badge ? <span className="flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-signal px-1 text-[11px] font-semibold text-bg" aria-label={`${badge} open incidents`}>{badge}</span>
          : item.keys && <span aria-hidden="true" className="mono text-[10px] tracking-[0.25em] text-dim">G {item.keys}</span>}
      </>}
    </NavLink>
  )
}

function AgentCard({ overview, error, availability }) {
  const servers = overview?.servers || []
  const open = (overview?.alerts_by_severity?.CRITICAL || 0) + (overview?.alerts_by_severity?.WARNING || 0)
  const online = servers.filter((server) => isOnline(server)).length
  let status = 'Online'
  let dot = 'dot-ok'
  let line
  if (error && !overview) { status = 'Unreachable'; dot = ''; line = 'The dashboard backend is not answering.' }
  else if (!servers.length) { status = 'Waiting'; dot = ''; line = 'No collectors yet · nothing to watch' }
  else if (open === 0 && overview?.quiet) line = `Nothing open · quiet for ${formatQuiet((Date.now() - new Date(overview.quiet.quiet_since).getTime()) / 1000)}`
  else line = `Collectors ${online}/${servers.length} reporting${availability != null ? ` · ${Number(availability.toFixed(2))}% this week` : ''}`
  return (
    <div className="rounded-md border border-line px-3 py-2.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-paper">Incident agent</span>
        <span className="flex items-center gap-1.5 text-[11px] text-soft"><span className={`dot ${dot}`} style={{ width: 6, height: 6 }} />{status}</span>
      </div>
      <div className="mt-1 text-[11px] leading-[17px] text-muted">{line}</div>
    </div>
  )
}

function Sidebar({ user, onLogout, overview, error }) {
  const location = useLocation()
  const availability = useWeekAvailability(Boolean(user))
  const servers = useMemo(() => sortServers(overview?.servers || []), [overview])
  const open = (overview?.alerts_by_severity?.CRITICAL || 0) + (overview?.alerts_by_severity?.WARNING || 0)
  const selected = location.pathname === '/' ? new URLSearchParams(location.search).get('server') : null
  return (
    <aside className="fixed inset-y-0 left-0 z-20 hidden w-[236px] flex-col border-r border-line bg-bg px-3.5 pb-3 pt-[22px] xl:flex">
      <Link to="/" className="block px-2" aria-label="Infradeck, go to Overview"><img src="/logo.svg" alt="Infradeck" className="h-5 w-auto" /></Link>
      <div className="mt-2 truncate px-2 text-[11px] text-muted">{overview?.instance_name || instanceName(user)}{overview?.version ? ` · v${overview.version}` : ''}</div>

      <nav aria-label="Main" className="mt-6 space-y-0.5">
        {primaryNav.map((item) => <NavItem key={item.to} item={item} badge={item.to === '/agent' ? open : 0} />)}
      </nav>

      <div className="mt-6 flex items-center justify-between px-2 text-[11px] text-muted"><span>Fleet</span><span>{servers.length}</span></div>
      <div className="custom-scroll mt-1.5 min-h-0 space-y-px overflow-y-auto">
        {servers.map((server) => {
          const state = serverState(server)
          return (
            <Link key={server.id} to={serverLink(server.id)} className={`flex h-[29px] items-center gap-3 rounded-md px-2 text-[13px] transition-colors hover:bg-ink ${String(server.id) === selected ? 'bg-ink' : ''}`}>
              <span className={`dot ${STATE_DOT[state]}`} />
              <span className="min-w-0 flex-1 truncate font-medium text-paper">{server.hostname}</span>
              <span className="shrink-0 text-[11px] text-muted">{server.is_dashboard_host && state !== 'critical' && state !== 'offline' ? 'this machine' : serverStateLabel(server)}</span>
            </Link>
          )
        })}
        {overview && !servers.length && <div className="px-2 pt-1.5">
          <div className="text-xs text-muted">No servers yet.</div>
          {user?.role === 'admin' && <Link to="/" className="mt-2 block text-xs font-medium text-paper hover:underline">+ Connect a server</Link>}
        </div>}
      </div>

      <nav aria-label="Settings" className="mt-6 space-y-0.5">
        {secondaryNav.filter((item) => !item.adminOnly || user?.role === 'admin').map((item) => <NavItem key={item.to} item={item} />)}
      </nav>

      <div className="mt-auto pt-6"><AgentCard overview={overview} error={error} availability={availability} /></div>
      {user && <div className="mt-4 flex items-center gap-2.5 px-1">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-[11px] font-semibold text-paper">{initials(user.name)}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium leading-4 text-paper">{user.name}</span>
          <span className="block text-[11px] leading-4 text-muted">{roleLabel(user.role)}</span>
        </span>
        <button type="button" onClick={onLogout} aria-label="Sign out" title="Sign out" className="flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-ink hover:text-paper"><Icon name="signout" size={15} /></button>
      </div>}
    </aside>
  )
}

// 768-1199 px: the sidebar shrinks to icons. The fleet list moves out; the pages and the account stay.
function Rail({ user, onLogout, overview }) {
  const open = (overview?.alerts_by_severity?.CRITICAL || 0) + (overview?.alerts_by_severity?.WARNING || 0)
  const items = [...primaryNav, ...secondaryNav].filter((item) => !item.adminOnly || user?.role === 'admin')
  return (
    <aside className="fixed inset-y-0 left-0 z-20 hidden w-16 flex-col items-center border-r border-line bg-bg pb-3 pt-[22px] md:flex xl:hidden">
      <Link to="/" aria-label="Infradeck, go to Overview" className="flex h-8 items-center"><LogoMark /></Link>
      <nav aria-label="Main" className="mt-6 flex flex-col items-center gap-1">
        {items.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === '/'} title={item.label} aria-label={item.label} className={({ isActive }) => `relative flex h-10 w-10 items-center justify-center rounded-md transition-colors ${isActive ? 'bg-raised text-paper' : 'text-muted hover:bg-ink hover:text-paper'}`}>
            {({ isActive }) => <>
              {isActive && <span aria-hidden="true" className="absolute -left-3 top-1 h-8 w-[3px] rounded-r-sm bg-signal" />}
              <Icon name={item.icon} size={17} />
              {item.to === '/agent' && open > 0 && <span className="absolute right-0.5 top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-signal px-0.5 text-[9px] font-semibold text-bg">{open}</span>}
            </>}
          </NavLink>
        ))}
      </nav>
      {user && <div className="mt-auto flex flex-col items-center gap-2">
        <span title={`${user.name} · ${roleLabel(user.role)}`} className="flex h-8 w-8 items-center justify-center rounded-md border border-line bg-raised text-[11px] font-semibold text-paper">{initials(user.name)}</span>
        <button type="button" onClick={onLogout} aria-label="Sign out" title="Sign out" className="flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-ink hover:text-paper"><Icon name="signout" size={15} /></button>
      </div>}
    </aside>
  )
}

// "Live · checked 4s ago", counting up between polls.
function LiveStatus({ overview, loadedAt, error }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000)
    return () => clearInterval(timer)
  }, [])
  if (!overview) return <span className="flex items-center gap-2 text-xs text-muted"><span className="dot" />{error ? 'Not connected' : 'Connecting…'}</span>
  if (!overview.servers.length) return <span className="flex items-center gap-2 text-xs text-muted"><span className="dot" />No collectors reporting</span>
  const seconds = Math.max(0, Math.round((Date.now() - loadedAt) / 1000))
  const stale = Boolean(error) || seconds > staleAfter()
  return <span className="flex items-center gap-2 whitespace-nowrap text-xs text-soft" title={error || undefined}><span className={`dot ${stale ? 'dot-warn' : 'dot-ok'}`} />{stale ? 'Stale' : 'Live'} · checked {seconds}s ago</span>
}

function paletteItems(overview, isAdmin) {
  const items = [
    { kind: 'Page', label: 'Overview', to: '/' }, { kind: 'Page', label: 'Statistics', to: '/statistics' }, { kind: 'Page', label: 'Agent', to: '/agent' }, { kind: 'Page', label: 'Account', to: '/account' },
    ...(isAdmin ? [{ kind: 'Page', label: 'Admin', to: '/admin' }] : []),
  ]
  for (const server of overview?.servers || []) {
    items.push({ kind: 'Server', label: server.hostname, hint: [server.ip_address, serverStateLabel(server)].filter(Boolean).join(' · '), to: serverLink(server.id) })
    for (const container of server.containers || []) items.push({ kind: 'Container', label: container.name, hint: `${server.hostname} · ${container.status}`, search: `${container.name} ${container.image || ''}`, to: serverLink(server.id, 'containers') })
    for (const row of listenerRows(server)) items.push({ kind: 'Port', label: `${row.proto}/${row.label}`, hint: [server.hostname, row.owner].filter(Boolean).join(' · '), search: `${row.proto}/${row.ports.join(' ')} ${row.ports.join(' ')} ${row.owner || ''}`, to: serverLink(server.id, 'network') })
  }
  return items
}

function CommandPalette({ overview, isAdmin, onClose }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const listRef = useRef(null)
  const all = useMemo(() => paletteItems(overview, isAdmin), [overview, isAdmin])
  const needle = query.trim().toLowerCase()
  const results = useMemo(() => (needle ? all.filter((item) => (item.search || item.label).toLowerCase().includes(needle)) : all.filter((item) => item.kind === 'Page' || item.kind === 'Server')).slice(0, 40), [all, needle])
  const active = Math.min(index, Math.max(0, results.length - 1))
  const go = (item) => { if (item) { navigate(item.to); onClose() } }

  useEffect(() => { listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [active])

  const onKeyDown = (event) => {
    if (event.key === 'Escape') onClose()
    else if (event.key === 'ArrowDown') { event.preventDefault(); setIndex(Math.min(active + 1, results.length - 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setIndex(Math.max(active - 1, 0)) }
    else if (event.key === 'Enter') { event.preventDefault(); go(results[active]) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[14vh]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label="Jump to" className="w-full max-w-[560px] overflow-hidden rounded-lg border border-edge bg-ink">
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Icon name="search" className="text-muted" />
          <input autoFocus value={query} onChange={(event) => { setQuery(event.target.value); setIndex(0) }} onKeyDown={onKeyDown} placeholder="Jump to server, container, port…" aria-label="Jump to server, container or port" role="combobox" aria-expanded="true" aria-controls="palette-results" className="h-12 flex-1 bg-transparent text-[13px] text-paper outline-none placeholder:text-dim" />
          <kbd className="mono rounded border border-line px-1.5 py-0.5 text-[10px] text-dim">esc</kbd>
        </div>
        <ul id="palette-results" ref={listRef} role="listbox" className="custom-scroll max-h-[46vh] overflow-y-auto p-1.5">
          {results.map((item, position) => (
            <li key={`${item.kind}-${item.to}-${item.label}-${item.hint || ''}`} role="option" aria-selected={position === active} onMouseEnter={() => setIndex(position)} onMouseDown={(event) => { event.preventDefault(); go(item) }} className={`flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 text-[13px] ${position === active ? 'bg-raised' : ''}`}>
              <span className="w-[68px] shrink-0 text-[11px] text-dim">{item.kind}</span>
              <span className={`min-w-0 truncate font-medium text-paper ${item.kind === 'Port' ? 'mono' : ''}`}>{item.label}</span>
              {item.hint && <span className="ml-auto min-w-0 truncate text-[11px] text-muted">{item.hint}</span>}
            </li>
          ))}
          {!results.length && <li className="px-2.5 py-6 text-center text-[13px] text-muted">Nothing matches “{query.trim()}”.</li>}
        </ul>
      </div>
    </div>
  )
}

function TopBar({ crumbs, overview, loadedAt, error, onOpenPalette }) {
  const mac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')
  return (
    <header className="sticky top-0 z-10 hidden h-14 items-center gap-5 border-b border-line bg-bg px-7 md:flex">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-3 text-sm font-semibold">
        {crumbs.map((crumb, position) => {
          const last = position === crumbs.length - 1
          return <span key={`${crumb.label}-${position}`} className="flex min-w-0 items-center gap-3">
            {position > 0 && <span aria-hidden="true" className="font-normal text-dim">/</span>}
            {crumb.to && !last ? <Link to={crumb.to} className="font-medium text-muted hover:text-paper">{crumb.label}</Link> : <span aria-current={last ? 'page' : undefined} className="truncate text-paper">{crumb.label}</span>}
          </span>
        })}
      </nav>
      <div className="ml-auto flex items-center gap-4">
        <LiveStatus overview={overview} loadedAt={loadedAt} error={error} />
        <button type="button" onClick={onOpenPalette} className="flex h-8 w-[300px] items-center md:w-[220px] lg:w-[300px] gap-3 rounded-md border border-line bg-bg px-3 text-left text-xs text-dim transition-colors hover:border-edge">
          <Icon name="search" size={14} className="text-muted" />
          <span className="flex-1 truncate">Jump to server, container, port…</span>
          <kbd className="mono rounded border border-line px-1 text-[10px] leading-4 text-dim">{mac ? '⌘K' : 'Ctrl K'}</kbd>
        </button>
      </div>
    </header>
  )
}

// The three blocks of the Infradeck mark, for places too small for the wordmark.
export function LogoMark({ className = 'h-[22px] w-auto' }) {
  return <svg viewBox="2.66 -32.01 8.27 32.01" className={className} aria-hidden="true"><rect x="2.66" y="-32.01" width="5.67" height="9.47" fill="var(--color-paper)" /><rect x="5.26" y="-20.74" width="5.67" height="9.47" fill="var(--color-signal)" /><rect x="2.66" y="-9.47" width="5.67" height="9.47" fill="var(--color-paper)" /></svg>
}

const ShellContext = createContext({ openPalette: () => {}, overview: null, loadedAt: 0, error: '' })
export const useShell = () => useContext(ShellContext)

// "4s ago" next to a green dot: the phone's short form of the live indicator.
function ShortLive() {
  const { overview, loadedAt, error } = useShell()
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000)
    return () => clearInterval(timer)
  }, [])
  if (!overview?.servers?.length) return null
  const seconds = Math.max(0, Math.round((Date.now() - loadedAt) / 1000))
  const stale = Boolean(error) || seconds > staleAfter()
  return <span className="flex items-center gap-1.5 whitespace-nowrap text-xs text-soft" title={error || undefined}><span className={`dot ${stale ? 'dot-warn' : 'dot-ok'}`} style={{ width: 7, height: 7 }} />{stale ? 'stale' : `${seconds}s ago`}</span>
}

export function RoundButton({ label, icon, onClick, children, ...rest }) {
  return <button type="button" onClick={onClick} aria-label={label} title={label} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-edge bg-transparent text-soft hover:text-paper" {...rest}>{children || <Icon name={icon} size={16} />}</button>
}

// A phone page's header: either the mark and the page name (top level), or a back link over the name (one level
// down). ``subtitle`` is a line under the name; ``action`` replaces the search button on the right.
export function PhoneHeader({ title, back, subtitle, action, live = !back, mark = false }) {
  const { openPalette } = useShell()
  return (
    <header className="px-4 pb-1 pt-[calc(14px+env(safe-area-inset-top))] md:hidden">
      {back && <Link to={back.to} className="-ml-1 mb-0.5 inline-flex h-7 items-center gap-1 text-[13px] text-muted hover:text-paper"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 3.5L5.5 8l4.5 4.5" /></svg>{back.label}</Link>}
      <div className="flex items-center gap-3">
        {mark && !back && <LogoMark />}
        <h1 className={`m-0 min-w-0 flex-1 text-[22px] font-semibold text-paper ${back ? 'line-clamp-2 break-words leading-7' : 'truncate leading-8'}`}>{title}</h1>
        {live && <ShortLive />}
        {action || <RoundButton label="Jump to server, container or port" icon="search" onClick={openPalette} />}
      </div>
      {subtitle && <div className="mt-1.5 text-[13px] leading-5 text-soft">{subtitle}</div>}
    </header>
  )
}

function MobileTabBar({ open }) {
  const location = useLocation()
  // More holds Account and Admin, so it stays lit on their pages.
  const inMore = ['/more', '/account', '/admin'].includes(location.pathname)
  const tab = (active, icon, label, badge) => <>
    <span className="relative"><Icon name={icon} size={20} />{badge > 0 && <span className="absolute -right-2.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-signal px-1 text-[10px] font-semibold text-bg">{badge}</span>}</span>
    <span className={active ? 'text-paper' : ''}>{label}</span>
  </>
  return (
    <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-bg pb-[env(safe-area-inset-bottom)] md:hidden">
      <div className="mx-auto grid max-w-md grid-cols-4">
        {primaryNav.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `flex h-[60px] flex-col items-center justify-center gap-1 text-[11px] font-medium ${isActive ? 'text-paper' : 'text-muted'}`}>
            {({ isActive }) => tab(isActive, item.icon, item.label, item.to === '/agent' ? open : 0)}
          </NavLink>
        ))}
        <Link to="/more" aria-current={inMore ? 'page' : undefined} className={`flex h-[60px] flex-col items-center justify-center gap-1 text-[11px] font-medium ${inMore ? 'text-paper' : 'text-muted'}`}>{tab(inMore, 'more', 'More', 0)}</Link>
      </div>
    </nav>
  )
}

// ``crumbs``: [{ label, to? }] for the top bar; defaults to the page's own name.
// ``bare``: the page lays out its own padding (the redesigned pages); otherwise the shell pads the content.
// ``phoneHeader``: the page draws its own <PhoneHeader>; otherwise phones get the page name from the crumbs.
// ``phoneTabs``: false hides the phone tab bar, for screens that pin their own actions to the bottom.
export function AppShell({ children, user, onLogout, fitViewport = false, crumbs, bare = false, phoneHeader = false, phoneTabs = true }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const { data: overview, error, loadedAt } = useOverview(Boolean(user) && !user.setup_required, SIDEBAR_REFRESH_SECONDS, onLogout)
  const trail = crumbs || [{ label: pageTitles[location.pathname] || 'Infradeck' }]
  const open = (overview?.alerts_by_severity?.CRITICAL || 0) + (overview?.alerts_by_severity?.WARNING || 0)

  // ⌘K / Ctrl+K opens the palette; G then O, S or A jumps between the main pages.
  useEffect(() => {
    let armed = 0
    const onKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPaletteOpen(true); return }
      if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return
      const key = event.key.toLowerCase()
      if (key === 'g') { armed = Date.now(); return }
      const target = { o: '/', s: '/statistics', a: '/agent' }[key]
      if (target && Date.now() - armed < 1200) { event.preventDefault(); navigate(target) }
      armed = 0
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate])

  const last = trail[trail.length - 1]
  const parent = trail.length > 1 ? [...trail].reverse().find((crumb) => crumb.to) : null
  const shell = useMemo(() => ({ openPalette: () => setPaletteOpen(true), overview, loadedAt, error }), [overview, loadedAt, error])

  return (
    <ShellContext.Provider value={shell}>
      <div className={`${fitViewport ? 'xl:h-screen xl:overflow-hidden' : ''} min-h-screen bg-bg text-paper`}>
        <Sidebar user={user} onLogout={onLogout} overview={overview} error={error} />
        <Rail user={user} onLogout={onLogout} overview={overview} />
        <div className={`${fitViewport ? 'xl:flex xl:h-full xl:flex-col' : ''} md:pl-16 xl:pl-[236px]`}>
          <TopBar crumbs={trail} overview={overview} loadedAt={loadedAt} error={error} onOpenPalette={() => setPaletteOpen(true)} />
          {!phoneHeader && <PhoneHeader title={last?.label || 'Infradeck'} back={parent ? { label: parent.label, to: parent.to } : null} />}
          <main className={`${fitViewport ? 'xl:min-h-0 xl:flex-1' : ''} min-w-0 ${bare ? '' : 'px-4 pt-4 md:px-7 md:pt-6'} ${phoneTabs ? 'pb-[calc(5.5rem+env(safe-area-inset-bottom))]' : ''} md:pb-7`}>{children}</main>
        </div>
        {phoneTabs && <MobileTabBar open={open} />}
        {paletteOpen && <CommandPalette overview={overview} isAdmin={user?.role === 'admin'} onClose={() => setPaletteOpen(false)} />}
      </div>
    </ShellContext.Provider>
  )
}
