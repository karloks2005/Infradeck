// Who is listening and who can reach it: the summary card under a server's Overview and the full Network tab.
import { useMemo, useState } from 'react'
import { LineChart } from './charts.jsx'
import { StatStrip } from './Containers.jsx'
import { rangeLabel } from './FleetRack.jsx'
import { firewallLine, firewallSummary, formatAge, groupNetworkEvents, listenerRows, niceCeiling, sortListeners, trafficRates } from './logic.js'

const RULE_NAMES = { ufw: 'ufw allow', ufw6: 'ufw allow', nftables: 'nftables allow', iptables: 'iptables allow', ip6tables: 'ip6tables allow' }
const rate = (value) => (value == null ? '—' : value >= 10 ? value.toFixed(0) : value.toFixed(1))

function PortChip({ row }) {
  return <li className={`port ${row.risky ? 'port-risky' : ''}`} title={row.risky ? row.reason : undefined}>{row.proto}/{row.label}{(row.owner || row.what) && <span>{[row.owner, row.risky && row.what !== 'database' ? row.what.replace(' UI', '') : null].filter(Boolean).join(' ') || row.what}</span>}</li>
}

function Changes({ events, limit = 4, compact = false }) {
  const [all, setAll] = useState(false)
  const groups = useMemo(() => groupNetworkEvents(events), [events])
  const [open, setOpen] = useState(null)
  if (!groups.length) return <p className="m-0 text-xs text-muted">No new listeners or firewall changes in the last 7 days.</p>
  const shown = all ? groups : groups.slice(0, limit)
  return <>
    <ul className="m-0 list-none divide-y divide-line p-0">
      {shown.map((group, index) => (
        <li key={`${group.time}-${group.port}-${index}`} className={`grid ${compact ? 'grid-cols-[52px_96px_minmax(0,1fr)]' : 'grid-cols-[86px_124px_minmax(0,1fr)]'} gap-x-2 py-2.5 text-xs`}>
          <span className="text-muted" title={new Date(group.time).toLocaleString()}>{group.age}{compact ? '' : ' ago'}</span>
          <span className={group.tone === 'warn' ? 'text-warn' : 'text-soft'}>{compact ? group.label.replace('Now exposed', 'Exposed') : group.label}</span>
          <span className="min-w-0 text-soft">
            {group.port && <span className="mono text-paper">{compact ? group.port.replace(/^[a-z+]+\//, '') : group.port} </span>}{group.text}
            {group.count > 2 && <> <button type="button" className="link" aria-expanded={open === index} onClick={() => setOpen(open === index ? null : index)}>{open === index ? 'hide' : 'show all'}</button></>}
            {open === index && <ul className="m-0 mt-1.5 list-none space-y-1 p-0 text-[11px] text-muted">{group.events.map((event, position) => <li key={`${event.time}-${position}`}>{formatAge(event.time)} ago · {event.detail}</li>)}</ul>}
          </span>
        </li>
      ))}
    </ul>
    {groups.length > limit && <button type="button" className="link mt-1 text-xs" onClick={() => setAll((value) => !value)}>{all ? 'Show fewer' : `${groups.length - limit} more`}</button>}
  </>
}

function counts(server) {
  const listeners = server.network?.listeners || []
  const ports = new Set(listeners.map((listener) => listener.port))
  const reachable = new Set(listeners.filter((listener) => listener.scope !== 'loopback').map((listener) => listener.port))
  return { listening: ports.size, reachable: reachable.size, local: ports.size - reachable.size }
}

export const listeningCount = (server) => counts(server).listening

// Under a server's Overview: ports as chips naming their owner, and the week's changes in a few lines.
export function NetworkCard({ server }) {
  const network = server.network || { listeners: [], events: [] }
  const rows = useMemo(() => listenerRows(server), [server])
  if (!network.listeners.length && !network.events.length) return null
  const total = counts(server)
  const reachable = rows.filter((row) => row.reachable)
  const local = rows.filter((row) => !row.reachable)
  const risky = reachable.some((row) => row.risky)
  return (
    <section className="card overflow-hidden" aria-label={`Network on ${server.hostname}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-[18px] py-3.5">
        <h2 className="m-0 text-sm font-semibold text-paper">Network</h2>
        <span className="text-xs text-muted">{total.listening} listening · {total.reachable} reachable from other machines · firewall config read from files, not the live ruleset</span>
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] lg:divide-x lg:divide-line">
        <div className="min-w-0 px-[18px] py-4">
          {reachable.length > 0 && <>
            <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-x-3"><h3 className="m-0 text-[13px] font-medium text-paper">Reachable from other machines <span className={risky || firewallSummary(server).enabled === false ? 'text-warn' : 'text-muted'}>{total.reachable}</span></h3><span className="text-[11px] text-muted">{firewallLine(server)}</span></div>
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">{reachable.map((row) => <PortChip key={`${row.proto}-${row.label}`} row={row} />)}</ul>
          </>}
          {local.length > 0 && <>
            <h3 className={`m-0 mb-2.5 text-[13px] font-medium text-paper ${reachable.length ? 'mt-5' : ''}`}>This machine only <span className="text-muted">{total.local}</span></h3>
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">{local.map((row) => <PortChip key={`${row.proto}-${row.label}`} row={row} />)}</ul>
          </>}
          {!rows.length && <p className="m-0 text-xs text-muted">This collector has not reported its listening ports.</p>}
          {risky && <p className="mb-0 mt-3 text-[11px] text-muted">Amber ports are admin panels or databases. Those usually shouldn't face the network.</p>}
        </div>
        <div className="min-w-0 border-t border-line px-[18px] py-4 lg:border-t-0">
          <div className="mb-1 flex items-baseline justify-between"><h3 className="m-0 text-[13px] font-medium text-paper">Changes · 7 days</h3><span className="text-[11px] text-muted">{network.events.length ? `${network.events.length} ${network.events.length === 1 ? 'event' : 'events'}, grouped` : ''}</span></div>
          <Changes events={network.events} />
        </div>
      </div>
    </section>
  )
}

function Stat({ label, children }) {
  return <div className="min-w-0 px-[18px] py-3"><div className="truncate text-[11px] text-muted">{label}</div><div className="mt-1.5 whitespace-nowrap text-[17px] font-semibold text-paper">{children}</div></div>
}

const FILTERS = { reachable: (row) => row.reachable, local: (row) => !row.reachable, all: () => true }

export function NetworkTab({ server, samples, minutes, onTab }) {
  const [filter, setFilter] = useState('reachable')
  const [sort, setSort] = useState('risk')
  const [all, setAll] = useState(false)
  const network = server.network || { listeners: [], events: [] }
  const rows = useMemo(() => listenerRows(server), [server])
  const traffic = useMemo(() => trafficRates(samples), [samples])
  const total = counts(server)
  const firewall = firewallSummary(server)
  const risky = rows.filter((row) => row.risky).length
  const latest = traffic[traffic.length - 1]
  const peak = niceCeiling(Math.max(0.1, ...traffic.flatMap((point) => [point.in ?? 0, point.out ?? 0])))
  const shown = sortListeners(rows.filter(FILTERS[filter]), sort)
  const limit = 8
  const visible = all ? shown : shown.slice(0, limit)
  const hidden = all ? [] : shown.slice(limit)
  const interfaces = server.system?.interfaces || []
  const bound = (row) => (row.scope === 'any' ? '0.0.0.0' : row.scope === 'loopback' ? '127.0.0.1' : 'one address')
  const reach = (row) => (row.scope === 'any' ? 'every network' : row.scope === 'loopback' ? 'this machine' : 'one network')
  if (!network.listeners.length) {
    return <p className="m-0 px-[18px] py-10 text-center text-[13px] text-muted">The collector on {server.hostname} has not reported its listening ports, so there is nothing to show here yet.</p>
  }
  return <>
    <StatStrip columns="md:grid-cols-3 xl:grid-cols-5">
      <Stat label="Listening">{total.listening} <span className="text-[11px] font-normal text-muted">ports</span></Stat>
      <Stat label="Reachable from other machines"><span className={total.reachable ? 'text-warn' : ''}>{total.reachable}</span></Stat>
      <Stat label="Admin panels or databases exposed"><span className={risky ? 'text-warn' : ''}>{risky}</span></Stat>
      <Stat label="Firewall"><span className={`text-sm ${firewall.tone === 'warn' ? 'text-warn' : firewall.tone === 'unknown' ? 'text-muted' : ''}`}>{firewall.text}</span></Stat>
      <Stat label="Traffic now">{rate(latest?.in)} <span className="text-[11px] font-normal text-muted">MB/s in</span> {rate(latest?.out)} <span className="text-[11px] font-normal text-muted">MB/s out</span></Stat>
    </StatStrip>
    <div className="grid xl:grid-cols-[minmax(0,1fr)_331px] xl:divide-x xl:divide-line">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3 px-[18px] py-3">
          <div className="seg" role="group" aria-label="Show ports">
            <button type="button" aria-pressed={filter === 'reachable'} onClick={() => setFilter('reachable')}>Reachable {total.reachable}</button>
            <button type="button" aria-pressed={filter === 'local'} onClick={() => setFilter('local')}>This machine only {total.local}</button>
            <button type="button" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All {total.listening}</button>
          </div>
          <label className="flex items-center gap-3 text-xs text-muted sm:ml-auto">Sort
            <select className="select" value={sort} onChange={(event) => setSort(event.target.value)}><option value="risk">Risk</option><option value="port">Port</option><option value="owner">Owner</option></select>
          </label>
        </div>
        {/* Phones: one card per port instead of a seven-column table. */}
        <ul className="m-0 list-none border-t border-line p-0 md:hidden">
          {visible.map((row) => (
            <li key={`${row.proto}-${row.label}`} className="border-b border-line px-4 py-3 last:border-b-0" style={row.risky ? { boxShadow: 'inset 2px 0 0 var(--color-warn)' } : undefined}>
              <div className="flex items-baseline gap-2.5">
                <span className="mono text-[13px] text-paper">{row.proto}/{row.label}</span>
                <span className={`min-w-0 truncate text-[13px] font-medium ${row.owner ? 'text-paper' : 'text-muted'}`}>{row.owner || 'host process'}</span>
                <span className={`ml-auto shrink-0 text-xs ${row.scope === 'any' ? 'text-warn' : 'text-soft'}`}>{reach(row)}</span>
              </div>
              <div className="mt-1 text-xs text-muted">{[row.ownerKind === 'container' ? `container${row.what ? ` · ${row.what}` : ''}` : null, `bound to ${bound(row)}`, row.rule ? RULE_NAMES[row.rule] || 'allowed' : row.reachable ? 'no firewall rule' : null].filter(Boolean).join(' · ')}</div>
              {row.reason && <div className="mt-1 text-xs text-muted">{row.reason}</div>}
            </li>
          ))}
          {!visible.length && <li className="px-4 py-6 text-center text-xs text-muted">No ports in this view.</li>}
        </ul>
        <div className="custom-scroll relative hidden overflow-x-auto md:block">
          <table className="w-full min-w-[720px] border-collapse text-left text-xs">
            <thead><tr className="grid-head h-10 border-t border-line">
              <th className="w-[80px] pl-[18px] font-medium">Port</th><th className="w-[60px] font-medium">Proto</th><th className="font-medium">Owner</th><th className="w-[100px] font-medium">Bound to</th><th className="w-[120px] font-medium">Reachable from</th><th className="w-[120px] font-medium">Firewall</th><th className="pr-[18px] font-medium"><span className="sr-only">Note</span></th>
            </tr></thead>
            <tbody>
              {visible.map((row) => (
                <tr key={`${row.proto}-${row.label}`} className="h-[54px] border-t border-line" style={row.risky ? { boxShadow: 'inset 2px 0 0 var(--color-warn)' } : undefined}>
                  <td className="mono pl-[18px] text-paper">{row.label}</td>
                  <td className="text-soft">{row.proto}</td>
                  <td>
                    <div className={`text-[13px] font-medium ${row.owner ? 'text-paper' : 'text-muted'}`}>{row.owner || 'host process'}</div>
                    <div className="text-[11px] text-muted">{row.ownerKind === 'container' ? `container${row.what ? ` · ${row.what}` : ''}` : row.owner ? 'host process' : 'not published by a container'}</div>
                  </td>
                  <td className="mono text-soft">{bound(row)}</td>
                  <td className={row.scope === 'any' ? 'text-warn' : 'text-paper'}>{reach(row)}</td>
                  <td className="text-soft">{row.rule ? RULE_NAMES[row.rule] || 'allowed' : row.reachable ? 'no rule' : '—'}</td>
                  <td className="pr-[18px] text-muted">{row.reason}</td>
                </tr>
              ))}
              {!visible.length && <tr className="border-t border-line"><td colSpan={7} className="px-[18px] py-6 text-center text-muted">No ports in this view.</td></tr>}
            </tbody>
          </table>
        </div>
        {hidden.length > 0 && <button type="button" onClick={() => setAll(true)} className="block h-10 w-full truncate border-0 border-t border-solid border-line bg-transparent px-[18px] text-left text-xs text-muted hover:bg-raised hover:text-soft">{hidden.length} more · {hidden.slice(0, 6).map((row) => `${row.owner || row.proto} ${row.label}`).join(', ')}{hidden.length > 6 ? '…' : ''}</button>}
      </div>
      <div className="min-w-0 border-t border-line px-[18px] py-4 xl:border-t-0">
        <div className="mb-4 flex items-baseline gap-2"><h3 className="m-0 text-[13px] font-semibold text-paper">Traffic</h3><span className="text-xs text-muted">MB/s · last {rangeLabel(minutes)}</span></div>
        <LineChart label={`Network traffic on ${server.hostname}`} points={traffic.map((point) => ({ time: point.time, value: point.in }))} second={traffic.map((point) => ({ time: point.time, value: point.out }))} max={peak} ticks={[peak, peak / 2, 0]} format={(tick) => String(Number(tick.toFixed(2)))} area height={92} gutter={28} />
        <div className="mt-2.5 flex gap-5 pl-7 text-[11px] text-muted"><span><span className="mr-2 inline-block h-px w-3.5 bg-paper align-middle" />in</span><span><span className="mr-2 inline-block w-3.5 border-t border-dashed border-muted align-middle" />out</span></div>

        <h3 className="m-0 mb-1 mt-6 text-[13px] font-semibold text-paper">Interfaces</h3>
        {interfaces.length ? <ul className="m-0 list-none divide-y divide-line p-0">{interfaces.map((item) => (
          <li key={item.name} className="grid h-9 grid-cols-[84px_minmax(0,1fr)_auto] items-center gap-2 text-xs">
            <span className="mono truncate text-paper">{item.name}</span>
            <span className="truncate text-soft">{[item.network, item.speed_mbit ? `${item.speed_mbit >= 1000 ? `${item.speed_mbit / 1000} Gbit/s` : `${item.speed_mbit} Mbit/s`}` : null].filter(Boolean).join(' · ') || 'no address'}</span>
            <span className="text-muted">{item.rx_mbps != null ? `${rate(item.rx_mbps + (item.tx_mbps || 0))} MB/s` : ''}</span>
          </li>
        ))}</ul> : <p className="m-0 text-xs text-muted">Reported once this machine's collector is updated.</p>}

        <div className="mb-1 mt-6 flex items-baseline justify-between"><h3 className="m-0 text-[13px] font-semibold text-paper">Changes <span className="ml-1.5 text-xs font-normal text-muted">7 days</span></h3><button type="button" className="link text-xs" onClick={() => onTab('events')}>Events tab</button></div>
        <Changes events={network.events} limit={3} compact />
      </div>
    </div>
    <p className="m-0 border-t border-line px-[18px] py-3 text-xs text-muted">Firewall rules are read from ufw, iptables and /etc/nftables.conf, not the live ruleset, and a rule is only matched to a port when the file names that port.{server.system?.listener_owners ? '' : ' Ports no container publishes belong to host processes; naming them needs the optional port-owners helper, which is off by default (see SECURITY.md).'}</p>
  </>
}
