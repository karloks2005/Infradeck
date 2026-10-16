// The More tab on a phone: your profile, the fleet, and every Account and Admin page as a plain row that shows
// its state on the right (2FA on, 1 failing, 2 waiting), so problems show without opening anything.
import { useMemo } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { serverState, serverStateLabel, sortServers } from '../dashboard/logic.js'
import { Icon } from '../icons.jsx'
import { useOverview } from '../overviewStore.js'
import { AppShell, PhoneHeader, serverLink } from '../shell.jsx'
import { useLoaded, usePhone } from '../ui.jsx'
import { ACCOUNT_GROUPS } from './groups.js'

const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const initials = (name) => { const parts = (name || '?').trim().split(/\s+/); return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase() }

function Group({ title, children }) {
  return (
    <section aria-label={title}>
      <h2 className="m-0 mb-2 px-1 text-xs font-medium text-muted">{title}</h2>
      <div className="divide-y divide-line overflow-hidden rounded-md border border-line bg-ink">{children}</div>
    </section>
  )
}

function Item({ to, icon, dot, title, detail, value, tone }) {
  return (
    <Link to={to} className="flex min-h-14 items-center gap-3.5 px-4 py-2.5">
      {icon && <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-bg text-soft"><Icon name={icon} size={15} /></span>}
      {dot !== undefined && <span className={`dot shrink-0 ${dot}`} />}
      <span className="min-w-0 flex-1"><span className="block truncate text-[15px] text-paper">{title}</span>{detail && <span className="mt-0.5 block truncate text-xs text-muted">{detail}</span>}</span>
      {value && <span className={`shrink-0 text-[13px] ${tone === 'warn' ? 'text-warn' : 'text-muted'}`}>{value}</span>}
      <Icon name="chevron" size={14} className="shrink-0 text-muted" />
    </Link>
  )
}

export default function MorePage({ user, onLogout }) {
  const phone = usePhone()
  const { data: overview } = useOverview(Boolean(user), 30, onLogout)
  const { data: account } = useLoaded(phone ? '/account' : null)
  const { data: sessions } = useLoaded(phone ? '/auth/sessions' : null)
  const servers = useMemo(() => sortServers(overview?.servers || []), [overview])
  // There is no More page on a larger screen: the sidebar holds all of it.
  if (!phone) return <Navigate to="/account" replace />

  const isAdmin = user?.role === 'admin'
  const failing = (account?.channels || []).filter((channel) => channel.last_ok === false).length
  const display = account?.preferences?.display
  const waiting = (overview?.pending_collectors || []).filter((item) => item.status === 'pending').length
  const values = {
    security: account ? [account.two_factor.enabled ? '2FA on' : '2FA off', account.two_factor.enabled ? '' : 'warn'] : null,
    notifications: account ? (failing ? [`${failing} failing`, 'warn'] : [account.channels.length ? `${account.channels.length} ${account.channels.length === 1 ? 'channel' : 'channels'}` : 'none', '']) : null,
    sessions: account && sessions ? [`${sessions.sessions.length} · ${account.tokens.length}`, ''] : null,
    display: display ? [`${display.theme.charAt(0).toUpperCase()}${display.theme.slice(1)} · ${display.time_format === '12h' ? '12 h' : '24 h'}`, ''] : null,
    servers: overview ? (waiting ? [`${waiting} waiting`, 'warn'] : [`${servers.length}`, '']) : null,
  }
  const detail = (server) => {
    const state = serverState(server)
    const label = serverStateLabel(server)
    return server.is_dashboard_host && state !== 'critical' && state !== 'offline' ? `this machine${label === 'healthy' ? '' : ` · ${label}`}` : label
  }

  return (
    <AppShell user={user} onLogout={onLogout} crumbs={[{ label: 'More' }]} bare phoneHeader>
      <PhoneHeader title="More" live={false} action={<span />} />
      <div className="space-y-6 px-4 pt-3">
        {user && <Link to="/account?section=profile" className="flex items-center gap-3.5 rounded-md border border-line bg-ink px-4 py-3.5">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-sm font-semibold text-paper" style={account?.avatar_color ? { color: account.avatar_color, borderColor: account.avatar_color } : undefined}>{initials(account?.name || user.name)}</span>
          <span className="min-w-0 flex-1"><span className="block truncate text-[15px] font-semibold text-paper">{account?.name || user.name}</span><span className="mt-0.5 block truncate text-xs text-muted">{isAdmin ? 'Owner' : 'Member'} · {user.email}</span></span>
          <Icon name="chevron" size={14} className="shrink-0 text-muted" />
        </Link>}

        <Group title="Monitoring">
          <Item to="/availability" icon="availability" title="Availability" value={overview?.checks_down ? `${overview.checks_down} down` : null} tone="warn" />
        </Group>

        {servers.length > 0 && <Group title="Fleet">
          {servers.map((server) => <Item key={server.id} to={serverLink(server.id)} dot={STATE_DOT[serverState(server)]} title={server.hostname} detail={detail(server)} />)}
        </Group>}

        <Group title="Account">
          {ACCOUNT_GROUPS.map((group) => <Item key={group.id} to={`/account?section=${group.id}`} icon={group.icon} title={group.label} value={values[group.id]?.[0]} tone={values[group.id]?.[1]} />)}
        </Group>

        {isAdmin && <Group title="Admin">
          <Item to="/admin" icon="admin" title="Servers and collectors" value={values.servers?.[0]} tone={values.servers?.[1]} />
          <Item to="/admin#instance" icon="admin" title="Users, rules, agent, data" />
        </Group>}

        <button type="button" onClick={onLogout} className="flex h-12 w-full items-center justify-center rounded-md border border-signal/50 bg-transparent text-[15px] font-medium text-signal">Sign out</button>
        <p className="m-0 pb-2 text-center text-xs text-dim">Infradeck {overview?.version || ''}{overview?.instance_name ? ` · ${overview.instance_name}` : ''}</p>
      </div>
    </AppShell>
  )
}
