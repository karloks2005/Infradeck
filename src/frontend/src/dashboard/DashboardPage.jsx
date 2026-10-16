// The Overview page: what needs you first, then the fleet as a rack, then one server in depth.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { LIVE_REFRESH_SECONDS, apiRequest } from '../api.js'
import { liveRefreshSeconds } from '../prefs.js'
import { Icon } from '../icons.jsx'
import { refreshOverview, useOverview } from '../overviewStore.js'
import { AppShell } from '../shell.jsx'
import { ContainersCard, ContainersTab } from './Containers.jsx'
import { AnotherMachineCard, EmptyFleet, ThisMachineCard } from './EmptyFleet.jsx'
import { EventsTab } from './EventsTab.jsx'
import { FleetRack, rangeLabel } from './FleetRack.jsx'
import { NeedsAttention, nextToWatch } from './NeedsAttention.jsx'
import { NetworkCard, NetworkTab, listeningCount } from './Network.jsx'
import { ServerPanel, TABS } from './ServerPanel.jsx'
import { SystemTab } from './SystemTab.jsx'
import { useForecasts, usePolled } from './data.js'
import { fullestMount, serverMounts, sortServers } from './logic.js'
import { Drawer, usePhone } from '../ui.jsx'
import { PhoneHeader } from '../shell.jsx'
import { PhoneOverview, PhoneServer, headlineParts } from './Phone.jsx'

const RANGES = [15, 30, 60, 180]
const RANGE_KEY = 'infradeck.overview.minutes'

function rememberedRange() {
  try {
    const stored = Number(window.localStorage.getItem(RANGE_KEY))
    return RANGES.includes(stored) ? stored : 30
  } catch {
    return 30
  }
}

const headline = (servers) => headlineParts(servers, ', ').join(' ')

export default function DashboardPage({ user, onLogout }) {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // Account › Display › Live refresh; read when the page opens.
  const [refresh] = useState(() => liveRefreshSeconds(LIVE_REFRESH_SECONDS))
  const { data: overview, error } = useOverview(Boolean(user), refresh, onLogout)
  // A phone shows the fleet and one server as separate screens; the desktop shows both, with one server picked.
  const phone = usePhone()
  const [minutes, setMinutes] = useState(rememberedRange)
  const [showConnect, setShowConnect] = useState(false)
  const [actionError, setActionError] = useState('')
  const [deletingId, setDeletingId] = useState(null)
  const panelRef = useRef(null)
  // Regular users get a read-only view; the backend enforces the same rules.
  const isAdmin = user?.role === 'admin'

  const servers = useMemo(() => sortServers(overview?.servers || []), [overview])
  const requested = Number(params.get('server'))
  const selected = servers.find((server) => server.id === requested) || (phone ? null : servers.find((server) => server.is_dashboard_host) || servers[0]) || null
  const tab = TABS.includes(params.get('tab')) ? params.get('tab') : 'overview'
  const selectedId = selected?.id ?? null

  const go = (serverId, nextTab, scroll = false) => {
    const next = new URLSearchParams()
    next.set('server', String(serverId))
    if (nextTab && nextTab !== 'overview') next.set('tab', nextTab)
    setParams(next, { replace: serverId === selectedId })
    if (scroll) requestAnimationFrame(() => panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  useEffect(() => {
    try { window.localStorage.setItem(RANGE_KEY, String(minutes)) } catch { /* private windows: keep it for this visit only */ }
  }, [minutes])

  // The live chart follows the server's check-ins (new points come no faster), but never refreshes more often than chosen.
  const pace = Math.max(refresh, 5, Math.min(selected?.check_interval_seconds || 15, 30))
  const { data: fleetLines } = usePolled(servers.length ? `/monitoring/sparklines?minutes=${minutes}` : null, 15)
  const { data: live } = usePolled(selectedId ? `/monitoring/servers/${selectedId}/live?minutes=${minutes}` : null, pace)
  const showsContainers = selected?.containers?.length > 0 && (tab === 'overview' || tab === 'containers')
  const { data: containerLines } = usePolled(selectedId && showsContainers ? `/monitoring/servers/${selectedId}/containers/sparklines?minutes=15` : null, 15)
  const wanted = useMemo(() => {
    const list = []
    for (const server of servers) {
      const mounts = serverMounts(server).filter((mount) => mount.total_gb != null)
      const fullest = fullestMount(mounts)
      for (const mount of server.id === selectedId ? mounts : fullest ? [fullest] : []) list.push({ serverId: server.id, mount: mount.mount })
    }
    return list
  }, [servers, selectedId])
  const forecasts = useForecasts(wanted)

  const changeInterval = async (server, seconds) => {
    setActionError('')
    try {
      await apiRequest(`/monitoring/servers/${server.id}/interval`, { method: 'PATCH', body: JSON.stringify({ check_interval_seconds: seconds }) })
      await refreshOverview()
    } catch (requestError) { setActionError(requestError.message || 'Could not update the check interval.') }
  }
  const deleteServer = async (server) => {
    if (!window.confirm(`Remove ${server.hostname} and all its stored metrics and incidents? This cannot be undone.`)) return
    setDeletingId(server.id); setActionError('')
    try {
      await apiRequest(`/monitoring/servers/${server.id}`, { method: 'DELETE' })
      setParams(new URLSearchParams(), { replace: true })
      await refreshOverview()
    } catch (requestError) { setActionError(requestError.message || 'Could not remove the server.') } finally { setDeletingId(null) }
  }
  const onAttention = (alert, action) => {
    if (action.kind === 'network' || action.kind === 'containers') go(alert.server_id, action.kind, true)
    else if (action.kind === 'trend') navigate(`/statistics?server=${alert.server_id}`)
    else navigate(`/agent?incident=${alert.id}`)
  }
  const openConnect = () => setShowConnect(true)

  const crumbs = selected && (tab !== 'overview' || phone) ? [{ label: 'Overview', to: `/?server=${selected.id}` }, { label: selected.hostname }] : [{ label: 'Overview' }]
  const pending = overview?.pending_collectors || []
  const waiting = pending.filter((item) => item.status === 'pending')
  const calm = !(overview?.alerts_by_severity?.CRITICAL || overview?.alerts_by_severity?.WARNING)
  const samples = live?.samples || []

  let body
  if (!overview) {
    body = <p className="m-0 py-24 text-center text-[13px] text-muted">{error || 'Loading the fleet…'}</p>
  } else if (!servers.length) {
    body = <EmptyFleet overview={overview} isAdmin={isAdmin} onChanged={refreshOverview} />
  } else {
    body = <>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="m-0 text-[21px] font-semibold leading-7 text-paper">Overview</h1>
          <p className="mb-0 mt-1.5 text-[13px] text-soft">{headline(servers)}</p>
        </div>
        <div className="flex items-center gap-4">
          <select className="select" aria-label="Chart range" value={minutes} onChange={(event) => setMinutes(Number(event.target.value))}>{RANGES.map((value) => <option key={value} value={value}>Last {rangeLabel(value)}</option>)}</select>
          {isAdmin && <button type="button" className="btn btn-primary" aria-haspopup="dialog" aria-expanded={showConnect} onClick={openConnect}><Icon name="plus" size={14} />Connect server</button>}
        </div>
      </header>

      {(error || actionError) && <div role="alert" className="rounded-md border border-warn/40 px-3.5 py-2.5 text-xs text-warn">{actionError || error}</div>}
      {isAdmin && waiting.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-edge bg-ink px-3.5 py-2.5 text-xs text-soft">
          <span className="dot" style={{ background: 'var(--color-paper)' }} />
          <span><span className="font-medium text-paper">{waiting.map((item) => item.hostname).join(', ')}</span> {waiting.length === 1 ? 'is' : 'are'} asking to join the fleet.</span>
          <button type="button" className="btn btn-sm ml-auto" onClick={openConnect}>Review</button>
        </div>
      )}
      <NeedsAttention overview={overview} watch={calm ? nextToWatch(servers, forecasts, (server) => fullestMount(serverMounts(server))) : null} onAction={onAttention} />
      <FleetRack overview={overview} servers={servers} selectedId={selectedId} sparklines={fleetLines?.servers} forecasts={forecasts} minutes={minutes} onSelect={(id) => go(id, tab)} />

      {selected && <div ref={panelRef} className="scroll-mt-20 space-y-7">
        <ServerPanel server={selected} overview={overview} tab={tab} onTab={(next) => go(selected.id, next)} isAdmin={isAdmin} onInterval={changeInterval} samples={samples} forecasts={forecasts} listening={listeningCount(selected)}>
          {tab === 'containers' && <ContainersTab key={selected.id} server={selected} sparklines={containerLines?.containers} />}
          {tab === 'network' && <NetworkTab key={selected.id} server={selected} samples={samples} minutes={minutes} onTab={(next) => go(selected.id, next)} />}
          {tab === 'system' && <SystemTab key={selected.id} server={selected} overview={overview} forecasts={forecasts} isAdmin={isAdmin} onDelete={deleteServer} deleting={deletingId === selected.id} />}
          {tab === 'events' && <EventsTab key={selected.id} server={selected} pending={pending} isAdmin={isAdmin} onReview={openConnect} />}
        </ServerPanel>
        {tab === 'overview' && <ContainersCard key={`containers-${selected.id}`} server={selected} sparklines={containerLines?.containers} calm={calm} />}
        {tab === 'overview' && <NetworkCard key={`network-${selected.id}`} server={selected} />}
      </div>}
    </>
  }

  if (phone && overview && servers.length) {
    const watch = calm ? nextToWatch(servers, forecasts, (server) => fullestMount(serverMounts(server))) : null
    body = selected
      ? <PhoneServer server={selected} overview={overview} tab={tab} onTab={(next) => go(selected.id, next)} isAdmin={isAdmin} onInterval={changeInterval} samples={samples} minutes={minutes} forecasts={forecasts} listening={listeningCount(selected)} error={actionError || error}>
        {tab === 'network' && <NetworkTab key={selected.id} server={selected} samples={samples} minutes={minutes} onTab={(next) => go(selected.id, next)} />}
        {tab === 'system' && <SystemTab key={selected.id} server={selected} overview={overview} forecasts={forecasts} isAdmin={isAdmin} onDelete={deleteServer} deleting={deletingId === selected.id} />}
        {tab === 'events' && <EventsTab key={selected.id} server={selected} pending={pending} isAdmin={isAdmin} onReview={openConnect} />}
      </PhoneServer>
      : <PhoneOverview overview={overview} servers={servers} isAdmin={isAdmin} waiting={waiting} sparklines={fleetLines?.servers} watch={watch} error={actionError || error} onAttention={onAttention} onConnect={openConnect} />
  } else if (phone) {
    body = <><PhoneHeader mark title="Overview" /><div className="px-4 pt-4">{body}</div></>
  }

  return (
    <AppShell user={user} onLogout={onLogout} crumbs={crumbs} bare phoneHeader={phone}>
      {phone ? body : <div className="space-y-8 px-3 pt-5 sm:px-4 lg:px-7 lg:pt-7">{body}</div>}
      {isAdmin && <Drawer open={showConnect && (servers.length > 0 || phone)} onClose={() => setShowConnect(false)} title="Connect a server" description="Run one command on the machine, then add it when it shows up here. The fleet behind this panel updates by itself.">
        {pending.some((item) => item.status === 'pending' && item.details?.dashboard_host) && <ThisMachineCard pending={pending} onChanged={refreshOverview} recommended={false} />}
        <AnotherMachineCard pending={pending} onChanged={refreshOverview} />
      </Drawer>}
    </AppShell>
  )
}
