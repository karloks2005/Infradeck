// One server, one view: a header with what the machine is, tabs, and the live Overview tab.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { LineChart, Meter } from './charts.jsx'
import { forecastKey } from './data.js'
import { forecastPhrase, formatPercent, formatUsedOfTotal, healthChecks, healthVerdict, mountLevel, niceCeiling, serverMounts, serverState, shortKernel } from './logic.js'

const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const TONE_DOT = { ok: 'dot-ok', warn: 'dot-warn', unknown: '' }
export const INTERVALS = [[15, '15 s'], [30, '30 s'], [60, '1 min'], [300, '5 min'], [900, '15 min']]
export const TABS = ['overview', 'containers', 'network', 'system', 'events']

export function intervalLabel(seconds) {
  return INTERVALS.find(([value]) => value === seconds)?.[1] || (seconds < 60 ? `${seconds} s` : `${Math.round(seconds / 60)} min`)
}

function Metric({ label, value, unit, children }) {
  return (
    <div className="min-w-0 px-[18px] pb-5 pt-[26px]">
      <div className="mb-5 flex items-baseline justify-between gap-3">
        <span className="text-xs font-medium text-soft">{label}</span>
        <span className="whitespace-nowrap"><span className="text-2xl font-semibold leading-none tracking-[-0.01em] text-paper">{value}</span>{unit && <span className="ml-2 text-[11px] text-muted">{unit}</span>}</span>
      </div>
      {children}
    </div>
  )
}

function HealthChecks({ server }) {
  const [help, setHelp] = useState(false)
  const checks = healthChecks(server)
  return (
    <div className="min-w-0 px-[18px] pb-4 pt-[22px]">
      <div className="mb-2.5 flex items-baseline justify-between"><span className="text-xs font-medium text-soft">Health checks</span><span className="text-[11px] text-muted">{healthVerdict(checks)}</span></div>
      <ul className="m-0 list-none divide-y divide-line p-0">
        {checks.map((check) => (
          <li key={check.key} className="grid h-[30px] grid-cols-[18px_84px_minmax(0,1fr)] items-center text-xs">
            <span className={`dot ${TONE_DOT[check.tone]}`} style={{ width: 6, height: 6 }} />
            <span className="text-muted">{check.label}</span>
            <span className={`truncate ${check.tone === 'warn' ? 'text-warn' : check.tone === 'unknown' ? 'text-muted' : 'text-paper'}`} title={check.text}>
              {check.text}{check.action && <> · <button type="button" className="link" aria-expanded={help} onClick={() => setHelp((open) => !open)}>{check.action}</button></>}
            </span>
          </li>
        ))}
      </ul>
      {help && <p className="mb-0 mt-2 text-[11px] leading-[18px] text-muted">The internet check is off until this machine's collector is given targets: set <span className="mono text-soft">HOMELAB_CONNECTIVITY_TARGETS</span> (for example <span className="mono text-soft">1.1.1.1:443,dns.google:443</span>) in its environment and restart it.</p>}
    </div>
  )
}

export function DiskRows({ server, overview, forecasts }) {
  const mounts = serverMounts(server)
  if (!mounts.length) return null
  return (
    <div className="divide-y divide-line border-t border-line">
      {mounts.map((mount) => {
        const level = mountLevel(mount, overview.disk_thresholds, overview.mount_thresholds)
        const forecast = forecastPhrase(forecasts[forecastKey(server.id, mount.mount)])
        return (
          <div key={mount.mount} className="grid min-h-[38px] grid-cols-[minmax(90px,150px)_minmax(80px,1fr)_auto] items-center gap-x-4 px-[18px] py-2 md:grid-cols-[150px_minmax(120px,1fr)_180px_200px]">
            <span className="mono truncate text-paper" title={mount.fs_type ? `${mount.mount} (${mount.fs_type})` : mount.mount}>{mount.mount}</span>
            <Meter percent={mount.used_pct} level={level} label={`${mount.mount} usage`} />
            <span className={`whitespace-nowrap text-xs ${level === 2 ? 'text-signal' : level === 1 ? 'text-warn' : 'text-paper'}`}>{mount.total_gb != null ? `${formatUsedOfTotal(mount.used_gb, mount.total_gb, ' of ')} · ` : ''}{formatPercent(mount.used_pct)}</span>
            <span className="hidden truncate text-xs text-muted md:block">{forecast || 'No clear growth trend'}</span>
          </div>
        )
      })}
    </div>
  )
}

function OverviewTab({ server, overview, samples, forecasts }) {
  const host = server.host
  if (!host) return <p className="m-0 px-[18px] py-10 text-center text-[13px] text-muted">The collector on {server.hostname} has not sent a measurement yet. It shows up here on its first check-in.</p>
  const series = (key) => samples.map((sample) => ({ time: sample.time, value: sample[key] }))
  const cores = server.cpu_cores || 1
  const loadPeak = Math.max(cores, ...samples.map((sample) => sample.load ?? 0))
  const loadMax = loadPeak <= cores ? cores : niceCeiling(loadPeak)
  const percentTicks = [100, 50, 0]
  return <>
    <div className="grid divide-y divide-line lg:grid-cols-2 lg:divide-x xl:grid-cols-[1fr_1fr_1fr_331px] xl:divide-y-0">
      <Metric label="CPU" value={formatPercent(host.cpu_utilization_pct)}>
        <LineChart label={`CPU use on ${server.hostname}`} points={series('cpu')} max={100} ticks={percentTicks} format={(tick) => `${tick}%`} />
      </Metric>
      <Metric label="Memory" value={formatPercent(host.ram_pct)}>
        <LineChart label={`Memory use on ${server.hostname}`} points={series('ram')} max={100} ticks={percentTicks} format={(tick) => `${tick}%`} area />
      </Metric>
      <Metric label="Load (1 min)" value={host.load_1m == null ? '—' : host.load_1m.toFixed(2)} unit={`of ${cores} ${cores === 1 ? 'core' : 'cores'}`}>
        <LineChart label={`Load on ${server.hostname}`} points={series('load')} max={loadMax} ticks={[loadMax, loadMax / 2, 0]} format={(tick) => String(Number(tick.toFixed(1)))} area height={118} gutter={28} />
      </Metric>
      <HealthChecks server={server} />
    </div>
    <DiskRows server={server} overview={overview} forecasts={forecasts} />
  </>
}

export function ServerHeader({ server, isAdmin, onInterval }) {
  const state = serverState(server)
  const kernel = shortKernel(server.system?.kernel)
  const meta = [server.os_info?.replace(/ \((.*)\)$/, ''), kernel ? `kernel ${kernel}` : null, server.cpu_cores ? `${server.cpu_cores} cores` : null, server.total_ram_gb ? `${Number(server.total_ram_gb.toFixed(1))} GB` : null, server.ip_address].filter(Boolean)
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-[18px] pb-1 pt-4">
      <span className={`dot ${STATE_DOT[state]}`} />
      <h2 className="m-0 text-[17px] font-semibold text-paper">{server.hostname}</h2>
      {server.is_dashboard_host && <span className="chip">this machine</span>}
      <span className="min-w-0 truncate text-xs text-muted">{meta.join(' · ')}</span>
      <span className="ml-auto flex items-center gap-3">
        <label className="flex items-center gap-3 text-xs text-muted">Check every
          {isAdmin ? <select className="select" value={server.check_interval_seconds} onChange={(event) => onInterval(server, Number(event.target.value))}>
            {!INTERVALS.some(([value]) => value === server.check_interval_seconds) && <option value={server.check_interval_seconds}>{intervalLabel(server.check_interval_seconds)}</option>}
            {INTERVALS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select> : <span className="font-medium text-paper">{intervalLabel(server.check_interval_seconds)}</span>}
        </label>
        <Link to={`/statistics?server=${server.id}`} className="btn">Full history</Link>
      </span>
    </div>
  )
}

export function ServerTabs({ server, tab, onTab, listening }) {
  const tabs = [
    ['overview', 'Overview', null],
    ['containers', 'Containers', server.containers?.length ? String(server.containers.length) : null],
    ['network', 'Network', listening ? `${listening} ${listening === 1 ? 'port' : 'ports'}` : null],
    ['system', 'System', null],
    ['events', 'Events', '7 d'],
  ]
  return (
    <div role="tablist" aria-label={`${server.hostname} sections`} className="no-scrollbar flex gap-7 overflow-x-auto border-b border-line px-7">
      {tabs.map(([id, label, count]) => <button key={id} type="button" role="tab" id={`server-tab-${id}`} aria-selected={tab === id} aria-controls="server-tabpanel" className="tab" onClick={() => onTab(id)}>{label}{count && <small>{count}</small>}</button>)}
    </div>
  )
}

export function ServerPanel({ server, overview, tab, onTab, isAdmin, onInterval, samples, forecasts, listening, children }) {
  return (
    <section className="card overflow-hidden" aria-label={`Server ${server.hostname}`}>
      <ServerHeader server={server} isAdmin={isAdmin} onInterval={onInterval} />
      <ServerTabs server={server} tab={tab} onTab={onTab} listening={listening} />
      <div role="tabpanel" id="server-tabpanel" aria-labelledby={`server-tab-${tab}`}>
        {tab === 'overview' ? <OverviewTab server={server} overview={overview} samples={samples} forecasts={forecasts} /> : children}
      </div>
    </section>
  )
}
