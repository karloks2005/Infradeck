// Facts about the machine, grouped by the question they answer, each with one verdict line.
import { useState } from 'react'
import { Icon } from '../icons.jsx'
import { Meter } from './charts.jsx'
import { forecastKey, usePolled } from './data.js'
import { bootHistory, forecastPhrase, formatPercent, formatSpan, formatUsedOfTotal, mountLevel, serverMounts, shortKernel } from './logic.js'

const DAY = 24 * 3600 * 1000
const HELPER_COMMAND = 'docker compose --profile system-events up -d'
const PRUNE_COMMAND = 'docker image prune -a && docker builder prune'
const when = (time) => new Date(time).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })

function Card({ title, aside, children, className = '' }) {
  return (
    <div className={`min-w-0 px-[18px] pb-5 pt-[18px] ${className}`}>
      <div className="mb-3 flex items-baseline justify-between gap-3"><h3 className="m-0 text-[13px] font-semibold text-paper">{title}</h3>{aside && <span className="text-[11px] text-muted">{aside}</span>}</div>
      {children}
    </div>
  )
}

function Rows({ items }) {
  return <dl className="kv m-0">{items.filter(Boolean).map(([label, value, options = {}]) => <div key={label} className="contents"><dt>{label}</dt><dd className={`truncate ${options.mono ? 'mono' : ''} ${options.tone === 'warn' ? 'text-warn' : options.tone === 'muted' ? 'text-muted' : 'text-paper'}`} title={typeof value === 'string' ? value : undefined}>{value}</dd></div>)}</dl>
}

function Verdict({ tone, children }) {
  return <p className={`mb-0 mt-3.5 flex items-center gap-2.5 text-xs ${tone === 'warn' ? 'text-warn' : 'text-paper'}`}><span className={`dot ${tone === 'warn' ? 'dot-warn' : 'dot-ok'}`} style={{ width: 6, height: 6 }} />{children}</p>
}

function Boots({ serverId }) {
  const { data } = usePolled(`/monitoring/servers/${serverId}/events?days=30`, null)
  const boots = bootHistory((data?.events || []).filter((event) => event.kind === 'reboot'))
  const unclean = boots.filter((boot) => boot.unclean)
  const slots = [...Array.from({ length: Math.max(0, 10 - boots.length) }, () => null), ...boots]
  return <>
    <div className="mt-3.5 flex gap-1" role="img" aria-label={`${boots.length} boots recorded in the last 30 days, ${unclean.length} unclean`}>
      {slots.map((boot, index) => <span key={index} title={boot ? `${when(boot.time)} · ${boot.unclean ? 'after an unclean shutdown' : 'clean'}` : 'no boot recorded'} className={`h-4 flex-1 rounded-[2px] ${!boot ? 'border border-line' : boot.unclean ? 'bg-warn' : 'bg-rail'}`} />)}
    </div>
    <p className="mb-0 mt-2.5 text-xs text-muted">
      {!data ? 'Loading boot history…' : !boots.length ? 'No reboot recorded in the last 30 days.' : unclean.length ? `${unclean.length === 1 ? 'One unclean boot' : `${unclean.length} unclean boots`}, the last on ${when(unclean[unclean.length - 1].time)}.` : `${boots.length} ${boots.length === 1 ? 'boot' : 'boots'} in the last 30 days, all after a clean shutdown.`}
    </p>
  </>
}

// What Docker's images and build cache take, how much of it nothing uses, and the command that frees it.
function DockerDiskRow({ disk }) {
  const [shown, setShown] = useState(false)
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(PRUNE_COMMAND); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { setCopied(false) }
  }
  const used = (disk.images_gb ?? 0) + (disk.build_cache_gb ?? 0)
  const reclaimable = (disk.images_unused_gb ?? 0) + (disk.build_cache_unused_gb ?? 0)
  const gb = (value) => `${Number(value.toFixed(value >= 10 ? 0 : 1))} GB`
  return <>
    <tr className="h-[42px] border-t border-line">
      <td className="mono pl-[18px] text-paper">/var/lib/docker</td>
      <td className="mono text-muted">—</td>
      <td className="whitespace-nowrap text-soft">images + cache</td>
      <td className="pr-6"><Meter percent={used ? (reclaimable / used) * 100 : 0} label="Share of Docker's disk use that nothing uses" /></td>
      <td className="whitespace-nowrap pr-6 text-paper">{gb(used)} · {gb(reclaimable)} reclaimable</td>
      <td className="text-soft">{disk.images_unused != null ? `${disk.images_unused} of ${disk.images} images not used by a container` : ''}</td>
      <td className="pr-[18px]">{reclaimable >= 0.1 && <button type="button" className="btn btn-sm" aria-expanded={shown} onClick={() => setShown((open) => !open)}>{shown ? 'Hide command' : 'Show prune command'}</button>}</td>
    </tr>
    {shown && <tr className="border-t border-line bg-sunken"><td colSpan={7} className="px-[18px] py-3">
      <div className="flex max-w-[780px] items-center gap-3 rounded-md border border-line bg-bg py-2 pl-3.5 pr-2"><code className="mono min-w-0 flex-1 truncate text-paper">{PRUNE_COMMAND}</code><button type="button" className="btn btn-sm" onClick={copy}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}</button></div>
      <p className="mb-0 mt-2 text-[11px] text-muted">Run it on this machine yourself; Infradeck never deletes anything. It removes every image no container uses and the unused build cache, so images you keep for later will have to be pulled again.</p>
    </td></tr>}
  </>
}

function HelperCard({ installed }) {
  const [shown, setShown] = useState(false)
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(HELPER_COMMAND); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { setCopied(false) }
  }
  return (
    <Card title={<>System helper <span className={`chip ml-2 align-middle font-normal ${installed ? 'chip-ok' : ''}`}>{installed ? 'installed' : 'not installed'}</span></>} className="lg:col-span-2">
      <p className="m-0 max-w-[780px] text-xs leading-[21px] text-soft">
        {installed ? 'The helper is running, so shutdown history, the names of processes the OOM killer stopped and kernel errors come from the kernel log rather than from counters.'
          : "Without it, Infradeck can't read shutdown history, OOM process names or kernel errors, so those rows above come from counters and files and may be incomplete. It's a small read-only container whose only privilege is reading the kernel log."}
      </p>
      {!installed && <div className="mt-3.5 flex flex-wrap items-center gap-3">
        <button type="button" className="btn" aria-expanded={shown} onClick={() => setShown((open) => !open)}><Icon name="download" size={14} />Install system helper</button>
        <span className="text-xs text-muted">Read SECURITY.md first. Nothing here can change the machine.</span>
      </div>}
      {shown && !installed && <div className="mt-3 flex max-w-[780px] items-center gap-3 rounded-md border border-line bg-bg py-2 pl-3.5 pr-2">
        <code className="mono min-w-0 flex-1 truncate text-paper">{HELPER_COMMAND}</code>
        <button type="button" className="btn btn-sm" onClick={copy}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}</button>
      </div>}
      {shown && !installed && <p className="mb-0 mt-2 text-[11px] text-muted">Run it on this machine, in the folder with the collector's compose file.</p>}
    </Card>
  )
}

export function SystemTab({ server, overview, forecasts, isAdmin, onDelete, deleting }) {
  const host = server.host || {}
  const system = server.system || {}
  const events = server.system_events || []
  const recent = (kind) => events.filter((event) => event.kind === kind && Date.now() - new Date(event.time).getTime() < DAY)
  const ooms = recent('oom_kill')
  const kernelErrors = [...recent('kernel_error'), ...recent('kernel_fault')]
  const rebootKernel = shortKernel(system.reboot_kernel)
  const bootedAt = host.system_uptime_seconds != null ? Date.now() - host.system_uptime_seconds * 1000 : null
  const temperatures = system.temperatures || []
  const collectorCurrent = system.collector_version && overview.version && system.collector_version === overview.version
  const mounts = serverMounts(server)
  const outdated = !system.collector_version

  return <>
    <div className="grid divide-y divide-line lg:grid-cols-3 lg:divide-x lg:divide-y-0">
      <Card title="Operating system">
        <Rows items={[
          ['Distribution', server.os_info || 'Unknown'],
          ['Kernel', system.kernel || 'Not reported', { mono: Boolean(system.kernel), tone: system.kernel ? undefined : 'muted' }],
          ['Architecture', system.arch || 'Not reported', { mono: Boolean(system.arch), tone: system.arch ? undefined : 'muted' }],
          ['Hostname', server.hostname, { mono: true }],
        ]} />
        {host.reboot_required ? <Verdict tone="warn">Reboot needed{rebootKernel ? ` for kernel ${rebootKernel}` : ''}</Verdict> : host.reboot_required === false && <Verdict>No reboot pending</Verdict>}
      </Card>
      <Card title="Updates" aside={system.updates_manager ? `${system.updates_manager}${system.updates_checked_at ? ` · checked ${formatSpan(Date.now() / 1000 - system.updates_checked_at)} ago` : ''}` : null}>
        {system.updates_manager ? <Rows items={[
          ['Pending', system.updates_pending == null ? 'Not reported' : `${system.updates_pending} ${system.updates_pending === 1 ? 'package' : 'packages'}`, { tone: system.updates_pending == null ? 'muted' : undefined }],
          ['Security', system.updates_security == null ? 'Not reported' : system.updates_security ? `${system.updates_security} waiting` : 'None waiting', { tone: system.updates_security ? 'warn' : system.updates_security == null ? 'muted' : undefined }],
          ['Automatic', system.auto_updates == null ? 'Not configured' : `unattended-upgrades ${system.auto_updates ? 'on' : 'off'}`, { tone: system.auto_updates === false ? 'warn' : system.auto_updates == null ? 'muted' : undefined }],
          ['Last applied', system.updates_applied_at ? when(system.updates_applied_at * 1000) : 'Unknown', { tone: system.updates_applied_at ? undefined : 'muted' }],
        ]} /> : <p className="m-0 text-xs leading-[21px] text-muted">{outdated ? 'Reported once this machine\'s collector is updated.' : 'Update state is read from apt, so it is only shown for Debian and Ubuntu.'}</p>}
      </Card>
      <Card title="Hardware">
        <Rows items={[
          ['CPU', [system.cpu_model, server.cpu_cores ? `${server.cpu_cores} cores` : null].filter(Boolean).join(' · ') || 'Unknown'],
          ['Memory', server.total_ram_gb ? `${Number(server.total_ram_gb.toFixed(1))} GB · ${formatPercent(host.ram_pct)} used` : 'Unknown'],
          ['Swap', system.swap_total_gb === 0 ? 'None' : `${system.swap_total_gb != null ? `${Number(system.swap_total_gb.toFixed(1))} GB · ` : ''}${formatPercent(host.swap_pct)} used`, { tone: host.swap_pct >= (overview.alert_thresholds?.swap_warning_pct ?? 50) ? 'warn' : undefined }],
          ['Temperature', temperatures.length ? temperatures.slice(0, 3).map((sensor) => `${Math.round(sensor.celsius)} °C ${sensor.label}`).join(' · ') : 'No sensors visible', { tone: temperatures.length ? undefined : 'muted' }],
        ]} />
      </Card>
    </div>
    <div className="grid divide-y divide-line border-t border-line lg:grid-cols-3 lg:divide-x lg:divide-y-0">
      <Card title="Uptime and boots" aside="last 10 boots">
        <Rows items={[
          ['Up since', bootedAt ? `${when(bootedAt)} · ${formatSpan(host.system_uptime_seconds)}` : 'Unknown'],
          ['Last shutdown', host.last_shutdown === 'clean' ? 'Clean' : host.last_shutdown === 'unclean' ? 'Unclean: crash, power loss or forced reset' : 'Unknown without the system helper', { tone: host.last_shutdown === 'unclean' ? 'warn' : host.last_shutdown === 'clean' ? undefined : 'muted' }],
        ]} />
        <Boots serverId={server.id} />
      </Card>
      <Card title="Time">
        <Rows items={[
          ['Sync', host.ntp_synced == null ? 'Unknown' : `${system.time_service ? `${system.time_service} · ` : ''}${host.ntp_synced ? 'synced' : 'not synced'}`, { tone: host.ntp_synced === false ? 'warn' : host.ntp_synced == null ? 'muted' : undefined }],
          ['Offset', system.clock_offset_ms == null ? 'Not reported' : `${Number(Math.abs(system.clock_offset_ms).toFixed(Math.abs(system.clock_offset_ms) < 10 ? 1 : 0))} ms`, { tone: system.clock_offset_ms == null ? 'muted' : undefined }],
          ['Time zone', system.timezone || 'Not reported', { tone: system.timezone ? undefined : 'muted' }],
        ]} />
        {host.ntp_synced != null && <Verdict tone={host.ntp_synced ? 'ok' : 'warn'}>{host.ntp_synced ? 'Clock is fine' : 'Clock is not synchronized'}</Verdict>}
      </Card>
      <Card title="Kernel and memory pressure" aside="24 h">
        <Rows items={[
          ['OOM kills', host.oom_kill_total == null ? 'Unknown' : ooms.length ? `${ooms.length} · ${ooms[0].detail}` : 'None', { tone: ooms.length ? 'warn' : host.oom_kill_total == null ? 'muted' : undefined }],
          ['Kernel errors', kernelErrors.length ? `${kernelErrors.length} · ${kernelErrors[0].detail}` : host.kernel_tainted == null ? 'Unknown' : 'None', { tone: kernelErrors.length ? 'warn' : host.kernel_tainted == null ? 'muted' : undefined }],
          ['Disk errors', system.disk_io_errors == null ? 'No counter on these disks' : system.disk_io_errors ? `${system.disk_io_errors} I/O ${system.disk_io_errors === 1 ? 'error' : 'errors'} since boot` : 'None', { tone: system.disk_io_errors ? 'warn' : system.disk_io_errors == null ? 'muted' : undefined }],
        ]} />
      </Card>
    </div>
    <div className="grid divide-y divide-line border-t border-line lg:grid-cols-3 lg:divide-x lg:divide-y-0">
      <Card title="Software">
        <Rows items={[
          ['Docker', host.docker_status === 'not_configured' ? 'Not found on this machine' : system.docker_version || (host.docker_status === 'unreachable' ? 'Unreachable' : 'Version not reported'), { tone: host.docker_status === 'unreachable' ? 'warn' : system.docker_version ? undefined : 'muted' }],
          ['Collector', system.collector_version ? `${system.collector_version} · ${collectorCurrent ? 'up to date' : `dashboard is ${overview.version}`}` : 'Older than 0.9.2', { tone: system.collector_version && collectorCurrent ? undefined : 'warn' }],
          ['Proxy', host.docker_status === 'not_configured' ? 'Not used' : 'Docker socket proxy, read-only'],
          ['Port names', system.listener_owners ? 'port-owners helper running' : 'Off (optional helper)', { tone: system.listener_owners ? undefined : 'muted' }],
          ['Checks every', formatSpan(server.check_interval_seconds)],
        ]} />
        {isAdmin && <button type="button" className="btn btn-sm mt-4 border-signal/50 text-signal hover:!border-signal hover:!bg-signal/10" onClick={() => onDelete(server)} disabled={deleting}>{deleting ? 'Removing…' : 'Remove this server'}</button>}
      </Card>
      <HelperCard installed={Boolean(system.helper_installed)} />
    </div>
    {mounts.length > 0 && <div className="border-t border-line">
      <div className="flex items-baseline gap-2.5 px-[18px] pb-2 pt-[18px]"><h3 className="m-0 text-[13px] font-semibold text-paper">Disks and mounts</h3><span className="text-xs text-muted">forecast from the last 30 days</span></div>
      <div className="custom-scroll overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-left text-xs">
          <thead><tr className="grid-head h-9"><th className="pl-[18px] font-medium">Mount</th><th className="font-medium">Device</th><th className="font-medium">Type</th><th className="w-[28%] font-medium">Used</th><th className="font-medium" /><th className="font-medium">Forecast</th><th className="pr-[18px] font-medium">Inodes</th></tr></thead>
          <tbody>{mounts.map((mount) => {
            const level = mountLevel(mount, overview.disk_thresholds, overview.mount_thresholds)
            const inodeLevel = mount.inodes_pct >= (overview.disk_thresholds?.inodes_critical_pct ?? 95) ? 2 : mount.inodes_pct >= (overview.disk_thresholds?.inodes_warning_pct ?? 85) ? 1 : 0
            return (
              <tr key={mount.mount} className="h-[42px] border-t border-line">
                <td className="mono pl-[18px] text-paper">{mount.mount}</td>
                <td className="mono text-muted">{system.mount_devices?.[mount.mount] || '—'}</td>
                <td className="text-soft">{mount.fs_type || '—'}</td>
                <td className="pr-6"><Meter percent={mount.used_pct} level={level} label={`${mount.mount} usage`} /></td>
                <td className={`whitespace-nowrap pr-6 ${level === 2 ? 'text-signal' : level === 1 ? 'text-warn' : 'text-paper'}`}>{mount.total_gb != null ? `${formatUsedOfTotal(mount.used_gb, mount.total_gb, ' of ')} · ` : ''}{formatPercent(mount.used_pct)}</td>
                <td className="text-soft">{forecastPhrase(forecasts[forecastKey(server.id, mount.mount)]) || 'No clear growth trend'}</td>
                <td className={`pr-[18px] ${inodeLevel === 2 ? 'text-signal' : inodeLevel === 1 ? 'text-warn' : 'text-soft'}`}>{mount.inodes_pct == null ? '—' : `${formatPercent(mount.inodes_pct)} used`}</td>
              </tr>
            )
          })}{system.docker_disk && <DockerDiskRow disk={system.docker_disk} />}</tbody>
        </table>
      </div>
    </div>}
  </>
}
