// What needs you now, as pulled-out drawers; or one calm panel saying that nothing does.
import { Link } from 'react-router-dom'
import { Icon } from '../icons.jsx'
import { attentionAction, forecastMonth, formatAge, formatPercent, formatQuiet, needsAttention } from './logic.js'

export function SectionHeading({ title, detail, children }) {
  return (
    <div className="mb-2.5 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <h2 className="m-0 text-sm font-semibold text-paper">{title}</h2>
      {detail && <span className="text-xs text-muted">{detail}</span>}
      {children && <span className="ml-auto text-xs text-soft">{children}</span>}
    </div>
  )
}

function Drawer({ alert, onAction }) {
  const critical = alert.severity === 'CRITICAL'
  const action = attentionAction(alert)
  // Rule titles name the container or the metric; the server goes in front of the explanation when the title lacks it.
  const where = alert.hostname && !alert.title.includes(alert.hostname) ? `${alert.hostname} · ` : ''
  return (
    <div className={`drawer ${critical ? 'drawer-critical drawer-out' : ''} grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-2 md:grid-cols-[96px_minmax(0,1fr)_minmax(0,auto)_56px_auto]`}>
      <span className={`col-span-2 text-[11px] font-bold tracking-[0.08em] md:col-span-1 ${critical ? 'text-bg' : 'text-warn'}`}>{alert.severity}</span>
      <span className="min-w-0">
        <span className={`block truncate text-[13px] font-semibold ${critical ? 'text-bg' : 'text-paper'}`}>{alert.title}</span>
        <span className={`block truncate text-xs ${critical ? 'text-bg/80' : 'text-muted'}`} title={alert.description}>{where}{alert.description}</span>
      </span>
      <span className={`hidden min-w-0 max-w-[400px] items-center gap-2.5 text-xs md:flex ${critical ? 'text-bg/85' : 'text-soft'}`}>
        {alert.agent_note && <><Icon name="agent" size={14} className="shrink-0" /><span className="truncate" title={alert.agent_note}>Agent: {alert.agent_note}</span></>}
      </span>
      <span className={`hidden text-center text-xs md:block ${critical ? 'text-bg/85' : 'text-muted'}`} title={`Since ${new Date(alert.created_at).toLocaleString()}`}>{formatAge(alert.created_at)}</span>
      <button type="button" onClick={() => onAction(alert, action)} className={`btn btn-sm ${critical ? 'border-[#160800] bg-[#160800] text-paper hover:!border-black hover:!bg-black' : ''}`}>{action.label}</button>
    </div>
  )
}

// A rack with every drawer pushed in.
function RackIllustration({ units = 5 }) {
  return (
    <svg viewBox="0 0 232 164" className="h-[164px] w-[232px]" role="img" aria-label="A rack with every drawer pushed in">
      <path d="M1 0v164M231 0v164" stroke="var(--color-rail)" strokeWidth="2" />
      {Array.from({ length: units }, (_, index) => {
        const y = 18 + index * 27
        return <g key={index}>
          <rect x="18.5" y={y + 0.5} width="194" height="19" rx="2" fill="none" stroke="var(--color-edge)" />
          <circle cx="27" cy={y + 10} r="1.4" fill="var(--color-dim)" /><circle cx="43" cy={y + 10} r="2.4" fill="var(--color-ok)" />
          <path d={`M184 ${y + 6}v8M188 ${y + 6}v8M192 ${y + 6}v8`} stroke="var(--color-dim)" strokeWidth="1.2" /><circle cx="204" cy={y + 10} r="1.4" fill="var(--color-dim)" />
        </g>
      })}
    </svg>
  )
}

function Fact({ label, value, detail }) {
  return (
    <div className="min-w-0 px-5 py-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-2 truncate text-[13px] font-medium text-paper" title={typeof value === 'string' ? value : undefined}>{value}</div>
      <div className="mt-2 text-xs leading-[18px] text-muted">{detail}</div>
    </div>
  )
}

function AllClear({ overview, watch }) {
  const quiet = overview.quiet
  const servers = overview.servers
  const newest = Math.max(...servers.map((server) => (server.last_seen_at ? new Date(server.last_seen_at).getTime() : 0)))
  const slowest = Math.max(...servers.map((server) => server.check_interval_seconds || 60))
  const reporting = servers.filter((server) => server.last_seen_at && Date.now() - new Date(server.last_seen_at).getTime() < (server.check_interval_seconds || 60) * 3000).length
  const last = quiet?.last_incident
  return (
    <div className="card grid overflow-hidden md:grid-cols-[300px_minmax(0,1fr)]">
      <div className="hidden items-center justify-center border-r border-line py-4 md:flex"><RackIllustration /></div>
      <div className="px-6 py-6">
        <h3 className="m-0 text-[17px] font-semibold text-paper">Every drawer is in. Nothing needs you right now.</h3>
        <p className="mb-0 mt-2.5 text-[13px] text-soft">
          {reporting === servers.length ? `${servers.length === 1 ? 'The collector' : `All ${servers.length} collectors`} reported in the last ${formatQuiet(Math.max(slowest, (Date.now() - newest) / 1000 + slowest)).replace(/^1 min$/, 'minute')} and nothing is open.` : `${reporting} of ${servers.length} collectors are reporting and nothing is open.`}
        </p>
        <div className="mt-5 grid divide-y divide-line rounded-md border border-line md:grid-cols-3 md:divide-x md:divide-y-0">
          <Fact label="Last incident" value={last ? last.title : 'None yet'} detail={last ? `Resolved ${formatAge(last.resolved_at)} ago${last.hostname ? ` · ${last.hostname}` : ''}` : 'Nothing has needed attention since this install started.'} />
          <Fact label="Next thing to watch" value={watch ? watch.title : 'Nothing in sight'} detail={watch ? watch.detail : 'No disk is on course to fill up.'} />
          <Fact label="Quiet for" value={quiet ? formatQuiet((Date.now() - new Date(quiet.quiet_since).getTime()) / 1000) : '—'} detail={quiet ? `Longest this month: ${formatQuiet(quiet.longest_quiet_seconds_this_month)}` : ''} />
        </div>
      </div>
    </div>
  )
}

// The filesystem most likely to need attention next: the one that fills soonest, else the fullest.
export function nextToWatch(servers, forecasts, fullest) {
  let best = null
  for (const server of servers) {
    const mount = fullest(server)
    if (!mount) continue
    const days = forecasts[`${server.id}:${mount.mount}`]
    const candidate = { server, mount, days, rank: days != null ? days : 1e9 - (mount.used_pct || 0) }
    if (!best || candidate.rank < best.rank) best = candidate
  }
  if (!best || (best.days == null && (best.mount.used_pct || 0) < 50)) return null
  const name = best.mount.mount === '/' ? 'Root disk' : best.mount.mount
  const when = forecastMonth(best.days)
  return { title: `${name} on ${best.server.hostname}`, detail: `${formatPercent(best.mount.used_pct)} now${when ? ` · ${when}` : ' · no clear growth'}` }
}

export function NeedsAttention({ overview, watch, onAction }) {
  const alerts = (overview.alerts || []).filter(needsAttention)
  const counts = overview.alerts_by_severity || {}
  const critical = counts.CRITICAL || 0
  const warnings = counts.WARNING || 0
  const open = critical + warnings
  const detail = open ? [`${open} open`, critical ? `${critical} critical` : null, warnings ? `${warnings} ${warnings === 1 ? 'warning' : 'warnings'}` : null].filter(Boolean).join(' · ') : '0 open'
  return (
    <section aria-label="Needs attention">
      <SectionHeading title="Needs attention" detail={detail}>
        <Link to="/agent" className="hover:text-paper">{open ? 'All incidents in Agent' : 'Incident history in Agent'} →</Link>
      </SectionHeading>
      {open ? <>
        <div className="rack">{alerts.map((alert) => <Drawer key={alert.id} alert={alert} onAction={onAction} />)}</div>
        {open > alerts.length && <p className="mb-0 mt-2 text-[11px] text-muted">Showing the {alerts.length} most severe of {open}. The rest are in Agent.</p>}
      </> : <AllClear overview={overview} watch={watch} />}
    </section>
  )
}
