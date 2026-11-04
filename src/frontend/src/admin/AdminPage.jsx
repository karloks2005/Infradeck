// Settings for this Infradeck instance and everyone who uses it: servers, users, alert rules, the incident
// agent, how long data is kept, and what version this is.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { AnotherMachineCard, ThisMachineCard } from '../dashboard/EmptyFleet.jsx'
import { INTERVALS, intervalLabel } from '../dashboard/ServerPanel.jsx'
import { serverState } from '../dashboard/logic.js'
import { Sparkline } from '../dashboard/charts.jsx'
import { usePolled } from '../dashboard/data.js'
import { Caret, Icon } from '../icons.jsx'
import { refreshOverview, useOverview } from '../overviewStore.js'
import { AppShell, serverLink } from '../shell.jsx'
import { ADMIN_GROUPS } from '../more/groups.js'
import { Field, Notice, Outcome, Row, Section, SettingsLayout, ShownOnce, Soon, Switch, ago, keptFor, useAction, useLoaded, usePhone } from '../ui.jsx'

const REPOSITORY = 'https://github.com/karloks2005/Infradeck'
const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const SEVERITY = { CRITICAL: ['Critical', 'chip-signal'], WARNING: ['Warning', 'chip-warn'], INFO: ['Info', ''] }
const THRESHOLD_LABELS = {
  restart_loop_count: 'Restarts', restart_loop_window_minutes: 'Within (min)', disk_warning_pct: 'Warning from %', disk_critical_pct: 'Critical from %', inodes_warning_pct: 'Warning from %', inodes_critical_pct: 'Critical from %',
  memory_warning_pct: 'Warning from %', memory_critical_pct: 'Critical from %', swap_warning_pct: 'Warning from %', load_warning_per_core: 'Warning × cores', load_critical_per_core: 'Critical × cores', event_window_minutes: 'Stays open (min)',
  sustained_checks: 'Check-ins in a row', docker_slow_ms: 'Slower than (ms)', collect_slow_pct: 'Over % of interval', leak_min_mb_per_hour: 'Rises MB/h', leak_min_growth_pct: 'Grows by %', leak_window_hours: 'Over (h)',
}
const put = (body) => apiRequest('/admin/settings', { method: 'PUT', body: JSON.stringify(body) })
const post = (path, body) => apiRequest(path, { method: 'POST', body: JSON.stringify(body || {}) })
const initials = (name) => { const parts = (name || '?').trim().split(/\s+/); return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase() }

function Servers({ overview }) {
  const [editing, setEditing] = useState(null)
  const [menu, setMenu] = useState(null)
  const [state, run] = useAction()
  const servers = overview.servers
  const save = (server, body) => run(async () => { await apiRequest(`/monitoring/servers/${server.id}`, { method: 'PATCH', body: JSON.stringify(body) }); await refreshOverview() })
  const remove = (server) => {
    setMenu(null)
    if (!window.confirm(`Remove ${server.hostname} and all its stored metrics and incidents? This cannot be undone.`)) return
    run(async () => { await apiRequest(`/monitoring/servers/${server.id}`, { method: 'DELETE' }); await refreshOverview() })
  }
  return (
    <div className="card overflow-hidden">
      {/* Phones: one block per server instead of a table that scrolls sideways. */}
      <ul className="m-0 list-none divide-y divide-line p-0 md:hidden">
        {servers.map((server) => {
          const version = server.system?.collector_version
          const outdated = !version || version !== overview.version
          const renaming = editing?.id === server.id
          return (
            <li key={server.id} className="px-4 py-3.5">
              {renaming
                ? <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); save(server, { hostname: editing.name }).then(() => setEditing(null)) }}>
                  <input className="field h-10 min-w-0 flex-1" autoFocus maxLength={253} aria-label={`New name for ${server.hostname}`} value={editing.name} onChange={(event) => setEditing({ id: server.id, name: event.target.value })} />
                  <button type="submit" className="btn btn-primary h-10" disabled={state.busy}>Save</button>
                  <button type="button" className="btn btn-ghost h-10" onClick={() => setEditing(null)}>Cancel</button>
                </form>
                : <div className="flex items-start gap-2.5">
                  <span className={`dot mt-[7px] ${STATE_DOT[serverState(server)]}`} />
                  <span className="min-w-0 flex-1"><span className="block break-all text-sm font-semibold leading-5 text-paper">{server.hostname}</span>{server.is_dashboard_host && <span className="chip mt-1.5">this machine</span>}</span>
                  <button type="button" className="link shrink-0 text-xs" onClick={() => setEditing({ id: server.id, name: server.hostname })}>Edit</button>
                </div>}
              <dl className="m-0 mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 text-xs">
                <div><dt className="text-muted">Address</dt><dd className="mono m-0 mt-0.5 break-all text-soft">{server.ip_address || '—'}</dd></div>
                <div><dt className="text-muted">Collector</dt><dd className="m-0 mt-0.5"><span className="mono text-paper">{version || 'older'}</span>{outdated && <span className="ml-2 text-warn">update</span>}</dd></div>
                <div><dt className="text-muted">Last check-in</dt><dd className="m-0 mt-0.5 text-soft">{ago(server.last_seen_at)}</dd></div>
                <div><dt className="text-muted">System helper</dt><dd className="m-0 mt-1"><span className={`chip ${server.system?.helper_installed ? 'chip-ok' : ''}`}>{server.system?.helper_installed ? 'installed' : 'not installed'}</span></dd></div>
              </dl>
              <div className="mt-3 flex items-center justify-between gap-3">
                <label className="flex items-center gap-2.5 text-xs text-muted">Check every
                  <select className="select h-10" aria-label={`Check interval of ${server.hostname}`} value={server.check_interval_seconds} onChange={(event) => save(server, { check_interval_seconds: Number(event.target.value) })}>
                    {!INTERVALS.some(([value]) => value === server.check_interval_seconds) && <option value={server.check_interval_seconds}>{intervalLabel(server.check_interval_seconds)}</option>}
                    {INTERVALS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <button type="button" className="btn btn-ghost h-10 text-signal" onClick={() => remove(server)}>Remove</button>
              </div>
            </li>
          )
        })}
        {!servers.length && <li className="px-4 py-6 text-center text-xs text-muted">No servers yet. Connect one below.</li>}
      </ul>
      <div className="custom-scroll hidden overflow-x-auto md:block"><table className="w-full min-w-[820px] border-collapse text-left text-[13px]">
        <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">Server</th><th className="font-medium">Address</th><th className="font-medium">Collector</th><th className="font-medium">Check every</th><th className="font-medium">Last check-in</th><th className="font-medium">System helper</th><th className="pr-[18px]" /></tr></thead>
        <tbody>
          {servers.map((server) => {
            const version = server.system?.collector_version
            const outdated = !version || version !== overview.version
            return (
              <tr key={server.id} className="h-[50px] border-t border-line">
                <td className="pl-[18px]">
                  {editing?.id === server.id
                    ? <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); save(server, { hostname: editing.name }).then(() => setEditing(null)) }}>
                      <input className="field h-8 w-44" autoFocus maxLength={253} aria-label={`New name for ${server.hostname}`} value={editing.name} onChange={(event) => setEditing({ id: server.id, name: event.target.value })} />
                      <button type="submit" className="btn btn-sm btn-primary" disabled={state.busy}>Save</button>
                    </form>
                    : <span className="flex items-center gap-2.5"><span className={`dot ${STATE_DOT[serverState(server)]}`} /><span className="text-sm font-semibold text-paper">{server.hostname}</span>{server.is_dashboard_host && <span className="chip">this machine</span>}</span>}
                </td>
                <td className="mono text-soft">{server.ip_address || '—'}</td>
                <td className="mono text-paper">{version || 'older'}{outdated && <span className="ml-2 font-sans text-xs text-warn">update</span>}</td>
                <td><select className="select h-8" aria-label={`Check interval of ${server.hostname}`} value={server.check_interval_seconds} onChange={(event) => save(server, { check_interval_seconds: Number(event.target.value) })}>
                  {!INTERVALS.some(([value]) => value === server.check_interval_seconds) && <option value={server.check_interval_seconds}>{intervalLabel(server.check_interval_seconds)}</option>}
                  {INTERVALS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select></td>
                <td className="text-soft">{ago(server.last_seen_at)}</td>
                <td><span className={`chip ${server.system?.helper_installed ? 'chip-ok' : ''}`}>{server.system?.helper_installed ? 'installed' : 'not installed'}</span></td>
                <td className="relative whitespace-nowrap pr-[18px] text-right">
                  <button type="button" className="link" onClick={() => setEditing(editing?.id === server.id ? null : { id: server.id, name: server.hostname })}>{editing?.id === server.id ? 'Cancel' : 'Edit'}</button>
                  <button type="button" className="ml-3 rounded border-0 bg-transparent px-1.5 text-base leading-none text-muted hover:text-paper" aria-label={`More for ${server.hostname}`} aria-expanded={menu === server.id} onClick={() => setMenu(menu === server.id ? null : server.id)}>⋯</button>
                  {menu === server.id && <div role="menu" className="absolute right-[18px] top-10 z-10 rounded-md border border-edge bg-bg p-1 text-left"><button type="button" role="menuitem" className="block rounded border-0 bg-transparent px-3 py-1.5 text-xs text-signal hover:bg-raised" onClick={() => remove(server)}>Remove this server…</button></div>}
                </td>
              </tr>
            )
          })}
          {!servers.length && <tr className="border-t border-line"><td colSpan={7} className="px-[18px] py-6 text-center text-xs text-muted">No servers yet. Connect one below.</td></tr>}
        </tbody>
      </table></div>
      <p className="m-0 border-t border-line px-[18px] py-3 text-xs text-muted">The system helper lets the collector read shutdown history, OOM process names and kernel errors. See SECURITY.md before installing. <Outcome state={state} /></p>
    </div>
  )
}

function Users({ me }) {
  const { data, error, setData, reload } = useLoaded('/admin/users')
  const [state, run] = useAction()
  const [inviting, setInviting] = useState(false)
  const [form, setForm] = useState({ email: '', role: 'user' })
  const [link, setLink] = useState(null)
  if (!data) return <Notice tone="plain">{error || 'Loading users…'}</Notice>
  const change = (user, body) => run(async () => setData(await apiRequest(`/admin/users/${user.id}`, { method: 'PATCH', body: JSON.stringify(body) })))
  const invite = (event) => { event.preventDefault(); run(async () => { const created = await post('/admin/invites', form); setLink({ email: created.email, url: `${window.location.origin}/register?invite=${created.token}` }); setInviting(false); setForm({ email: '', role: 'user' }); reload() }) }
  const setting = (body) => run(async () => { await put(body); reload() })
  return (
    <div className="card overflow-hidden">
      <div className="custom-scroll overflow-x-auto"><table className="w-full min-w-[680px] border-collapse text-left text-[13px]">
        <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">User</th><th className="font-medium">Role</th><th className="font-medium">Two-factor</th>{data.agent_chat_access === 'per_user' && <th className="font-medium">Agent chat</th>}<th className="font-medium">Last seen</th><th className="pr-[18px]" /></tr></thead>
        <tbody>
          {data.users.map((user) => {
            const fixed = user.role_name === 'Owner' || user.id === me.id
            return (
              <tr key={user.id} className={`h-[54px] border-t border-line ${user.is_active ? '' : 'opacity-60'}`}>
                <td className="pl-[18px]"><span className="flex items-center gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-[11px] font-semibold" style={{ color: user.avatar_color || undefined }}>{initials(user.name)}</span><span className="min-w-0"><span className="block truncate font-medium text-paper">{user.name}{user.id === me.id && <span className="ml-2 text-xs font-normal text-muted">you</span>}</span><span className="block truncate text-xs text-muted">{user.email}</span></span></span></td>
                <td>{fixed ? <span className="text-paper">{user.role_name}</span> : <select className="select h-8" aria-label={`Role of ${user.name}`} value={user.role} onChange={(event) => change(user, { role: event.target.value })}><option value="admin">Admin</option><option value="user">Viewer</option></select>}</td>
                <td><span className={`chip ${user.two_factor ? 'chip-ok' : ''}`}>{user.two_factor ? 'On' : 'Off'}</span></td>
                {data.agent_chat_access === 'per_user' && <td>{user.role === 'admin' ? <span className="text-xs text-muted">always</span> : <Switch label={`Agent chat for ${user.name}`} checked={user.agent_chat_enabled} disabled={state.busy} onChange={(on) => change(user, { agent_chat_enabled: on })} />}</td>}
                <td className="text-soft">{!user.is_active ? 'disabled' : user.sessions > 0 && user.id === me.id ? 'now' : ago(user.last_seen_at)}</td>
                <td className="whitespace-nowrap pr-[18px] text-right">{!fixed && <>
                  {user.sessions > 0 && <button type="button" className="link" onClick={() => run(async () => { await post(`/admin/users/${user.id}/sign-out`); reload() })}>Sign out</button>}
                  <button type="button" className="link ml-3" onClick={() => change(user, { is_active: !user.is_active })}>{user.is_active ? 'Disable' : 'Enable'}</button>
                </>}</td>
              </tr>
            )
          })}
          {data.invites.map((invite) => (
            <tr key={`invite-${invite.id}`} className="h-[54px] border-t border-line">
              <td className="pl-[18px]"><span className="flex items-center gap-3"><span className="flex h-7 w-7 items-center justify-center rounded-md border border-dashed border-edge text-[11px] text-muted">?</span><span><span className="block font-medium text-paper">Invite pending</span><span className="block text-xs text-muted">{invite.email} · made {ago(invite.created_at)}</span></span></span></td>
              <td className="text-soft">{invite.role === 'admin' ? 'Admin' : 'Viewer'}</td><td className="text-muted">n/a</td>{data.agent_chat_access === 'per_user' && <td className="text-muted">off</td>}<td className="text-muted">never</td>
              <td className="pr-[18px] text-right"><button type="button" className="link" onClick={() => run(async () => setData(await apiRequest(`/admin/invites/${invite.id}`, { method: 'DELETE' })))}>Cancel</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {link && <div className="border-t border-line px-[18px] py-3.5"><ShownOnce value={link.url}>Send this link to {link.email}. Infradeck sends no email itself. It works once, only for that address, and expires in 7 days.</ShownOnce></div>}
      {inviting && (
        <form onSubmit={invite} className="flex flex-wrap items-end gap-3 border-t border-line px-[18px] py-3.5">
          <Field label="Email"><input type="email" className="field h-9 w-64" autoFocus required value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></Field>
          <Field label="Role"><select className="select h-9" value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}><option value="user">Viewer</option><option value="admin">Admin</option></select></Field>
          <button type="submit" className="btn btn-primary h-9" disabled={state.busy}>Create invitation link</button>
          <button type="button" className="btn btn-ghost h-9" onClick={() => setInviting(false)}>Cancel</button>
        </form>
      )}
      <div className="divide-y divide-line border-t border-line">
        <Row title="Require two-factor for everyone" detail={data.secrets_configured ? 'New and existing users set it up at their next sign-in' : 'Not possible yet: this server has no SECRETS_KEY (see docs/accounts.md)'}>
          <Switch label="Require two-factor for everyone" checked={data.require_2fa_for_everyone} disabled={!data.secrets_configured || state.busy} onChange={(on) => setting({ require_2fa_for_everyone: on })} />
        </Row>
        <Row title="Require two-factor for owners and admins" detail="On unless you turn it off. Applies even when the switch above is off.">
          <Switch label="Require two-factor for admins" checked={data.require_2fa_for_admins} disabled={!data.secrets_configured || state.busy || data.require_2fa_for_everyone} onChange={(on) => setting({ require_2fa_for_admins: on })} />
        </Row>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-[18px] py-3 text-xs text-muted">
        <span>Owners and admins change settings. Viewers see everything but can't acknowledge, resolve or change anything. <Outcome state={state} /></span>
        {!inviting && <button type="button" className="btn btn-sm" onClick={() => { setLink(null); setInviting(true) }}><Icon name="plus" size={13} />Invite user</button>}
      </div>
    </div>
  )
}

function Rules({ onCount }) {
  const phone = usePhone()
  const { data, error, reload } = useLoaded('/admin/rules')
  const [state, run] = useAction()
  const [editing, setEditing] = useState(null)
  const [values, setValues] = useState({})
  const count = data?.rules.length
  useEffect(() => { if (count != null) onCount(count) }, [count, onCount])
  if (!data) return <Notice tone="plain">{error || 'Loading rules…'}</Notice>
  const toggle = (rule, on) => run(async () => { await put({ disabled_rules: data.rules.filter((item) => (item.kind === rule.kind ? !on : !item.enabled)).map((item) => item.kind) }); reload() })
  const open = (rule) => { setEditing(editing === rule.kind ? null : rule.kind); setValues(Object.fromEntries(rule.edit.map((key) => [key, data.thresholds[key]]))) }
  const save = (event) => { event.preventDefault(); run(async () => { await put({ alert_thresholds: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])) }); setEditing(null); reload() }) }
  const off = data.rules.filter((rule) => !rule.enabled)
  const editor = (rule) => editing === rule.kind && <form onSubmit={save} className="my-2.5 flex flex-wrap items-end gap-2.5">
    {rule.edit.map((key) => <Field key={key} label={THRESHOLD_LABELS[key] || key}><input type="number" step="any" className="field h-9 w-28" required value={values[key] ?? ''} onChange={(event) => setValues({ ...values, [key]: event.target.value })} /></Field>)}
    <button type="submit" className="btn btn-sm h-9 btn-primary" disabled={state.busy}>Save</button>
  </form>
  // Phones: each rule as one sentence with its severity, how often it fired, and a large switch.
  if (phone) {
    return (
      <div className="space-y-3">
        <div className="divide-y divide-line overflow-hidden rounded-md border border-line bg-ink">
          {data.rules.map((rule) => (
            <div key={rule.kind} className="px-4 py-3">
              <div className="flex items-center gap-3">
                <span className="min-w-0 flex-1">
                  <span className={`block text-[15px] leading-5 ${rule.enabled ? 'text-paper' : 'text-soft'}`}>{rule.when}{rule.window && rule.window !== 'n/a' ? ` · ${rule.window}` : ''}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-xs text-muted"><span className={`dot ${rule.enabled ? { CRITICAL: 'dot-signal', WARNING: 'dot-warn', INFO: '' }[rule.severity] : ''}`} style={{ width: 6, height: 6 }} />{rule.enabled ? SEVERITY[rule.severity][0] : 'Off'} · {rule.enabled ? `fired ${rule.fired}× in 30 d` : `would have fired ${rule.fired}×`}{rule.noisy && <span className="text-warn"> · noisy</span>}</span>
                  {rule.edit.length > 0 && <button type="button" className="mt-1.5 h-7 border-0 bg-transparent p-0 text-xs text-soft underline" aria-expanded={editing === rule.kind} onClick={() => open(rule)}>{editing === rule.kind ? 'Close' : 'Change thresholds'}</button>}
                </span>
                <span className="origin-right scale-125"><Switch label={rule.when} checked={rule.enabled} disabled={state.busy} onChange={(on) => toggle(rule, on)} /></span>
              </div>
              {editor(rule)}
            </div>
          ))}
        </div>
        <p className="m-0 text-xs leading-5 text-muted">{off.length ? `${off.length} ${off.length === 1 ? 'rule is' : 'rules are'} switched off and open no incidents.` : 'Every rule is on.'} A rule that is switched off also closes the incidents it had open. Rules of your own are not built yet. <Outcome state={state} /></p>
      </div>
    )
  }
  return (
    <div className="card overflow-hidden">
      <div className="custom-scroll overflow-x-auto"><table className="w-full min-w-[760px] border-collapse text-left text-[13px]">
        <thead><tr className="grid-head h-10"><th className="w-[70px] pl-[18px] font-medium">On</th><th className="font-medium">When</th><th className="font-medium">For</th><th className="font-medium">Severity</th><th className="font-medium">Servers</th><th className="pr-[18px] text-right font-medium">Fired · 30 d</th></tr></thead>
        <tbody>
          {data.rules.map((rule) => (
            <tr key={rule.kind} className={`border-t border-line ${rule.enabled ? '' : 'text-dim'}`}>
              <td className="h-[46px] pl-[18px]"><Switch label={rule.when} checked={rule.enabled} disabled={state.busy} onChange={(on) => toggle(rule, on)} /></td>
              <td className={rule.enabled ? 'text-paper' : ''}>
                {rule.when}{rule.edit.length > 0 && <button type="button" className="link ml-3 text-xs" aria-expanded={editing === rule.kind} onClick={() => open(rule)}>{editing === rule.kind ? 'close' : 'change'}</button>}
                {editor(rule)}
              </td>
              <td className={rule.enabled ? 'text-soft' : ''}>{rule.window || 'n/a'}</td>
              <td><span className={`chip ${rule.enabled ? SEVERITY[rule.severity][1] : ''}`}>{SEVERITY[rule.severity][0]}</span></td>
              <td className={rule.enabled ? 'text-soft' : ''}>All</td>
              <td className="pr-[18px] text-right">{rule.fired}{rule.noisy && <span className="ml-2 text-xs text-warn">noisy</span>}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-[18px] py-3 text-xs text-muted">
        <span>{off.length ? `${off.length} ${off.length === 1 ? 'rule is' : 'rules are'} switched off and open no incidents.` : 'Every rule is on.'} A rule that is switched off also closes the incidents it had open. <Outcome state={state} /></span>
        <button type="button" className="btn btn-sm" disabled title="Rules of your own are not built yet; the built-in ones can be tuned and switched off."><Icon name="plus" size={13} />New rule<Soon /></button>
      </div>
    </div>
  )
}

function MountOverrides({ settings, onSaved }) {
  const [rows, setRows] = useState(() => Object.entries(settings.mount_thresholds || {}).map(([mount, limits]) => ({ mount, warning: limits.disk_warning_pct, critical: limits.disk_critical_pct })))
  const [state, run] = useAction()
  const set = (index, key, value) => setRows(rows.map((row, position) => (position === index ? { ...row, [key]: value } : row)))
  const save = () => run(async () => onSaved(await put({ mount_thresholds: Object.fromEntries(rows.filter((row) => row.mount.trim()).map((row) => [row.mount.trim(), { disk_warning_pct: Number(row.warning), disk_critical_pct: Number(row.critical) }])) })), 'Saved.')
  return (
    <div className="card px-[18px] py-4">
      <p className="m-0 text-[13px] font-medium text-paper">Disk limits for single filesystems</p>
      <p className="mb-0 mt-1 text-xs text-muted">A backup disk that is meant to be nearly full can get its own limits instead of the ones in the Disk rule above.</p>
      <div className="mt-3 space-y-2">
        {rows.map((row, index) => (
          <div key={index} className="flex flex-wrap items-center gap-2.5">
            <input className="field mono h-8 w-56" placeholder="/mnt/backup" aria-label="Mount" value={row.mount} onChange={(event) => set(index, 'mount', event.target.value)} />
            <input type="number" className="field h-8 w-24" aria-label="Warning from %" placeholder="warn %" value={row.warning} onChange={(event) => set(index, 'warning', event.target.value)} />
            <input type="number" className="field h-8 w-24" aria-label="Critical from %" placeholder="crit %" value={row.critical} onChange={(event) => set(index, 'critical', event.target.value)} />
            <button type="button" className="link text-xs" onClick={() => setRows(rows.filter((_, position) => position !== index))}>Remove</button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3"><button type="button" className="btn btn-sm" onClick={() => setRows([...rows, { mount: '', warning: 95, critical: 99 }])}><Icon name="plus" size={13} />Add a filesystem</button><button type="button" className="btn btn-sm btn-primary" disabled={state.busy} onClick={save}>Save limits</button><Outcome state={state} /></div>
    </div>
  )
}

function Agent({ settings, system, onSaved }) {
  const [state, run] = useAction()
  const [test, runTestAction] = useAction()
  const [tested, setTested] = useState(null)
  const runTest = (work) => { setTested(null); return runTestAction(async () => setTested(await work())) }
  const agent = system.agent
  return <>
    <div className="card divide-y divide-line">
      <Row icon="AI" title="Model provider" detail={agent.key_configured ? `${agent.provider || 'OpenAI'} · ${agent.model} · key configured` : 'No API key configured. Set OPENAI_API_KEY in .env and restart the backend to turn the chat on.'} tone={agent.key_configured ? undefined : 'warn'}>
        <button type="button" className="btn btn-sm" disabled={!agent.key_configured || test.busy} title={agent.key_configured ? 'Checks the key and the model without spending tokens.' : 'Set OPENAI_API_KEY first.'} onClick={() => runTest(() => apiRequest('/admin/agent/test', { method: 'POST' }))}>{test.busy ? 'Testing…' : 'Test connection'}</button>
      </Row>
      {(test.error || tested) && <div className="px-[18px] py-2.5">{test.error ? <p role="alert" className="m-0 text-xs text-warn">{test.error}</p> : <p role="status" className="m-0 text-xs text-ok">Connected. {tested.provider} accepts the key and offers {tested.model}.</p>}</div>}
      <Row title="Diagnose new incidents automatically" detail="When a warning or critical incident opens or gets worse, the agent worker sends its evidence (masked) to the model and writes a diagnosis. Off: incidents keep the rule-based diagnosis, and anyone who may act on incidents can still ask for one."><Switch label="Diagnose automatically" checked={settings.agent_auto_diagnose !== false} disabled={state.busy} onChange={(on) => run(async () => onSaved(await put({ agent_auto_diagnose: on })))} /></Row>
      <Row title="Read container logs" detail="Not available: Infradeck's Docker proxy can't read logs. See SECURITY.md."><Switch label="Read container logs" checked={false} disabled onChange={() => {}} /></Row>
      <Row title="Chat" detail="The main switch for the chat; who may use it is set under Chat access. Questions and the incident's facts are sent to the provider."><Switch label="Chat with the agent" checked={settings.agent_chat_enabled} disabled={state.busy} onChange={(on) => run(async () => onSaved(await put({ agent_chat_enabled: on })))} /></Row>
      <Row title="Chat access" detail={settings.agent_chat_access === 'per_user' ? 'Admins always; everyone else only with the Agent chat switch next to their name in Users.' : 'Only owners and admins can use the chat.'}>
        <select className="select" aria-label="Chat access" value={settings.agent_chat_access || 'admins_only'} disabled={state.busy} onChange={(event) => run(async () => onSaved(await put({ agent_chat_access: event.target.value })))}><option value="admins_only">Admins only</option><option value="per_user">Per user</option></select>
      </Row>
      <Row title="Show diagnoses to users" detail="Off: only owners and admins see the agent's diagnoses and their summary in notifications; everyone else sees the rule-based diagnosis.">
        <Switch label="Show diagnoses to users" checked={settings.agent_show_diagnoses_to_users !== false} disabled={state.busy} onChange={(on) => run(async () => onSaved(await put({ agent_show_diagnoses_to_users: on })))} />
      </Row>
      <Row title="Redact IPs and hostnames before sending" detail="Not available yet. Until then, turn Chat off if names must not leave this machine."><Switch label="Redact before sending" checked={false} disabled onChange={() => {}} /></Row>
    </div>
    <Outcome state={state} />
    <AgentWorker />
    <AgentSpending settings={settings} onSaved={onSaved} />
  </>
}

const JOB_TONE = { failed: 'chip-warn', noted: '', skipped: '', diagnosed: 'chip-ok', reused: 'chip-ok' }

// The worker that runs the agent's jobs, in one line: is it alive, does anything wait, how did the last jobs go.
// The jobs themselves fold away; Redis keeps only the latest 50 and this shows at most 10.
function AgentWorker() {
  const { data, error } = usePolled('/admin/agent/worker', 15)
  if (!data) return <Notice tone="plain">{error || 'Checking the agent worker…'}</Notice>
  const waiting = [data.queued ? `${data.queued} queued` : null, data.processing ? `${data.processing} running` : null, data.retrying ? `${data.retrying} waiting to retry` : null].filter(Boolean).join(' · ') || 'nothing waiting'
  const counts = {}
  for (const job of data.recent) counts[job.status] = (counts[job.status] || 0) + 1
  const failed = counts.failed || 0
  const last = data.recent[0]
  const lastLine = last ? `last job ${ago(last.finished_at)}: ${last.status} INC-${String(last.incident_id).padStart(4, '0')}` : 'no jobs yet'
  const detail = !data.online ? `Not running, so new incidents wait for their diagnosis · ${waiting}. Start it with docker compose up -d agent-worker.`
    : `Running since ${ago(data.worker.started_at).replace(' ago', '')} · ${waiting} · ${lastLine}${failed ? ` · ${failed} of the last ${data.recent.length} failed` : ''}`
  return (
    <div className="card overflow-hidden">
      <Row icon="Q" title="Worker" tone={!data.online || failed ? 'warn' : undefined} detail={detail}>
        <span className={`chip ${data.online ? 'chip-ok' : 'chip-warn'}`}>{data.online ? 'Online' : 'Offline'}</span>
      </Row>
      {data.recent.length > 0 && <details className="group border-t border-line">
        <summary className="flex cursor-pointer list-none items-center gap-2.5 px-[18px] py-2.5 text-xs text-muted hover:text-soft">
          <Caret open={false} className="transition-transform group-open:rotate-90" />
          Last {data.recent.length} {data.recent.length === 1 ? 'job' : 'jobs'}: {Object.entries(counts).map(([status, count]) => `${count} ${status}`).join(', ')}
        </summary>
        <ul className="m-0 list-none divide-y divide-line border-t border-line p-0">
          {data.recent.map((job) => (
            <li key={job.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-[18px] py-2.5 text-xs">
              <span className="w-16 shrink-0 text-muted">{ago(job.finished_at)}</span>
              <Link to={`/agent?incident=${job.incident_id}`} className="mono shrink-0 text-soft hover:text-paper">INC-{String(job.incident_id).padStart(4, '0')}</Link>
              <span className="shrink-0 text-muted">{job.type === 'incident.worsened' ? 'got worse' : job.type === 'incident.requested' ? 'asked for' : 'opened'}</span>
              <span className={`chip ${JOB_TONE[job.status] ?? ''}`}>{job.status}</span>
              <span className="min-w-0 flex-1 basis-60 text-muted">{job.note}</span>
            </li>
          ))}
        </ul>
      </details>}
    </div>
  )
}

// "$0.0031" for small amounts, "$1.84" from a cent up.
const dollars = (value) => (value == null ? '—' : `$${value > 0 && value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`)
const thousands = (value) => (value >= 1e6 ? `${(value / 1e6).toFixed(1)} M` : value >= 1e4 ? `${Math.round(value / 1e3)} k` : value.toLocaleString())

// What the model calls cost this month, against the budget, and the settings that limit it.
function AgentSpending({ settings, onSaved }) {
  const { data, error, reload } = useLoaded('/admin/agent/usage')
  const [state, run] = useAction()
  const [cap, setCap] = useState(settings.agent_monthly_cap_usd ?? '')
  const [limit, setLimit] = useState(String(settings.agent_daily_chat_limit ?? 100))
  const [prices, setPrices] = useState(() => Object.entries(settings.agent_prices || {}).map(([model, price]) => ({ model, input: String(price.input), output: String(price.output) })))
  if (!data) return <Notice tone="plain">{error || 'Loading the agent\'s usage…'}</Notice>
  const save = (body, done) => run(async () => { onSaved(await put(body)); reload() }, done)
  const savePrices = (event) => {
    event.preventDefault()
    save({ agent_prices: Object.fromEntries(prices.filter((row) => row.model.trim()).map((row) => [row.model.trim(), { input: Number(row.input), output: Number(row.output) }])) }, 'Prices saved.')
  }
  const setRow = (index, key, value) => setPrices((rows) => rows.map((row, position) => (position === index ? { ...row, [key]: value } : row)))
  const share = data.share == null ? null : Math.min(1, data.share)
  const daily = data.daily.map((day) => day.cost)
  const configuredPriced = prices.some((row) => row.model.trim() && (data.model === row.model.trim() || data.model.startsWith(`${row.model.trim()}-`)))
  return <>
    {data.state === 'paused' && <Notice>The agent is paused: this month's budget is used up ({dollars(data.cost)} of {dollars(data.cap)}). Chat stays off until next month or until you raise the budget. Incidents and notifications keep working.</Notice>}
    {data.state === 'warn' && <Notice>The agent has used {Math.round(data.share * 100)}% of this month's budget ({dollars(data.cost)} of {dollars(data.cap)}). At 100% it pauses until next month.</Notice>}
    <div className="card grid divide-y divide-line sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      <div className="px-[18px] py-4">
        <div className="text-2xl font-semibold leading-none text-paper">{dollars(data.cost)}{data.cap && <span className="ml-1.5 text-sm font-normal text-muted">of {dollars(data.cap)}</span>}</div>
        <div className="mt-2 text-xs text-muted">estimated cost this month{data.unpriced_runs ? ` · ${data.unpriced_runs} ${data.unpriced_runs === 1 ? 'call' : 'calls'} without a price` : ''}</div>
        {share != null && <span className={`meter mt-3 ${data.state === 'paused' ? 'meter-signal' : data.state === 'warn' ? 'meter-warn' : ''}`} role="meter" aria-label="Share of the monthly budget" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}><i style={{ width: `${share * 100}%`, minWidth: share > 0 ? 3 : 0 }} /></span>}
      </div>
      <div className="px-[18px] py-4">
        <div className="text-2xl font-semibold leading-none text-paper">{data.calls}</div>
        <div className="mt-2 text-xs text-muted">calls to the model this month{data.errors ? ` · ${data.errors} failed` : ''}{data.diagnoses ? ` · ${data.diagnoses} diagnoses` : ''}</div>
        {daily.some((value) => value > 0) && <Sparkline className="mt-3" values={daily} width={140} height={18} max={Math.max(...daily)} />}
      </div>
      <div className="px-[18px] py-4">
        <div className="text-2xl font-semibold leading-none text-paper">{thousands(data.input_tokens + data.output_tokens)}</div>
        <div className="mt-2 text-xs text-muted">tokens · {thousands(data.input_tokens)} in, {thousands(data.output_tokens)} out</div>
      </div>
    </div>

    {data.by_model.length > 0 && <div className="card overflow-hidden"><div className="custom-scroll overflow-x-auto"><table className="w-full min-w-[460px] border-collapse text-left text-[13px]">
      <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">Model</th><th className="text-right font-medium">Calls</th><th className="text-right font-medium">Tokens in / out</th><th className="pr-[18px] text-right font-medium">Cost</th></tr></thead>
      <tbody>{data.by_model.map((row) => (
        <tr key={row.model} className="h-10 border-t border-line">
          <td className="mono pl-[18px] text-paper">{row.model}</td>
          <td className="text-right text-soft">{row.calls}</td>
          <td className="text-right text-soft">{thousands(row.input_tokens)} / {thousands(row.output_tokens)}</td>
          <td className={`pr-[18px] text-right ${row.priced ? 'text-paper' : 'text-warn'}`}>{row.priced ? dollars(row.cost) : 'no price'}</td>
        </tr>
      ))}</tbody>
    </table></div></div>}

    <div className="card divide-y divide-line">
      <Row title="Monthly budget" detail="At 80% admins get a notification; at 100% the chat pauses until the 1st of next month. Empty: no budget.">
        <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); save({ agent_monthly_cap_usd: cap === '' ? 0 : Number(cap) }, 'Budget saved.') }}>
          <span className="text-[13px] text-muted">$</span>
          <input type="number" min="0" step="0.01" inputMode="decimal" className="field h-9 w-24" aria-label="Monthly budget in US dollars" value={cap} placeholder="none" onChange={(event) => setCap(event.target.value)} />
          <button type="submit" className="btn btn-sm h-9" disabled={state.busy || String(cap) === String(settings.agent_monthly_cap_usd ?? '')}>Save</button>
        </form>
      </Row>
      <Row title="Chat messages per person" detail="In any 24 hours, on top of 30 an hour. Admins included.">
        <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); save({ agent_daily_chat_limit: Number(limit) }, 'Limit saved.') }}>
          <input type="number" min="1" step="1" inputMode="numeric" className="field h-9 w-24" aria-label="Chat messages per person per day" value={limit} onChange={(event) => setLimit(event.target.value)} />
          <button type="submit" className="btn btn-sm h-9" disabled={state.busy || limit === String(settings.agent_daily_chat_limit)}>Save</button>
        </form>
      </Row>
      <form onSubmit={savePrices} className="px-[18px] py-3.5">
        <div className="text-[13px] font-medium text-paper">Prices per million tokens</div>
        <p className="mb-0 mt-0.5 text-xs leading-5 text-muted">Copy them from your provider's pricing page; they aren't fetched automatically. A price for <span className="mono">gpt-5-mini</span> also covers dated names such as <span className="mono">gpt-5-mini-2026-01-01</span>.{!configuredPriced && <span className="text-warn"> The model in use, <span className="mono">{data.model}</span>, has no price, so its calls aren't counted in the budget.</span>}</p>
        <div className="mt-3 space-y-2">
          {prices.map((row, index) => (
            <div key={index} className="flex flex-wrap items-end gap-2">
              <Field label="Model" className="min-w-0 flex-1 basis-40"><input className="field mono h-9" required maxLength={100} value={row.model} onChange={(event) => setRow(index, 'model', event.target.value)} /></Field>
              <Field label="Input $"><input type="number" min="0" step="any" required inputMode="decimal" className="field h-9 w-24" value={row.input} onChange={(event) => setRow(index, 'input', event.target.value)} /></Field>
              <Field label="Output $"><input type="number" min="0" step="any" required inputMode="decimal" className="field h-9 w-24" value={row.output} onChange={(event) => setRow(index, 'output', event.target.value)} /></Field>
              <button type="button" className="btn btn-ghost h-9" aria-label={`Remove the price of ${row.model || 'this model'}`} onClick={() => setPrices((rows) => rows.filter((_, position) => position !== index))}><Icon name="close" size={13} /></button>
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2.5">
          <button type="button" className="btn btn-sm" onClick={() => setPrices((rows) => [...rows, { model: rows.some((item) => item.model === data.model) ? '' : data.model, input: '', output: '' }])}><Icon name="plus" size={13} />Add a model</button>
          <button type="submit" className="btn btn-sm btn-primary" disabled={state.busy}>Save prices</button>
          <Outcome state={state} />
        </div>
      </form>
    </div>
  </>
}

const megabytes = (bytes) => (bytes == null ? '—' : bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`)

function Retention({ settings, system, onSaved }) {
  const [state, run] = useAction()
  const database = system.database
  return (
    <div className="card divide-y divide-line">
      <Row title="Every check-in" detail="Each server's report, about one a minute, and every availability result. Charts for ranges past 7 days read the summaries below."><span className="text-[13px] text-soft">7 days</span></Row>
      <Row title="Hourly summaries and events" detail="The average and peak of every metric per hour, each check's uptime per hour, and events such as reboots, OOM kills and port changes. Older hours are deleted once their day is summarised.">
        <select className="select" aria-label="Keep hourly summaries and events" value={settings.history_retention_days} disabled={state.busy} onChange={(event) => run(async () => onSaved(await put({ history_retention_days: Number(event.target.value) })), 'Saved.')}>
          {[365, 730, 0].map((days) => <option key={days} value={days}>{keptFor(days)[0].toUpperCase() + keptFor(days).slice(1)}</option>)}
        </select>
      </Row>
      <Row title="Daily, weekly and monthly summaries" detail="A few kilobytes per server a year, so they are never deleted."><span className="text-[13px] text-soft">Forever</span></Row>
      <Row title="Incidents" detail="Kept until their server is removed."><span className="text-[13px] text-soft">Forever</span></Row>
      <Row icon="db" title="Database" detail={`${database.engine}${database.host_rows != null ? ` · ${database.host_rows.toLocaleString()} check-ins stored, ${database.host_rows_week.toLocaleString()} from the last 7 days` : ''}`}>
        <span className="mr-2 text-lg font-semibold text-paper">{megabytes(database.bytes)}</span>
        <button type="button" className="btn btn-sm" disabled title="Not built yet. Back up the postgres volume with pg_dump for now (see SETUP_DATABASES.md).">Export<Soon /></button>
        <button type="button" className="btn btn-sm" disabled title="Not built yet. Back up the postgres volume with pg_dump for now (see SETUP_DATABASES.md).">Back up now<Soon /></button>
      </Row>
      {(state.error || state.ok) && <div className="px-[18px] py-2.5"><Outcome state={state} /></div>}
    </div>
  )
}

function About({ settings, system, onSaved }) {
  const [name, setName] = useState(settings.instance_name || '')
  const [state, run] = useAction()
  return (
    <div className="card divide-y divide-line">
      <Row title={`Infradeck ${system.version}`} detail="Open source, AGPL-3.0 licensed. Infradeck doesn't call home, so it can't tell you about new versions; the changelog can."><a className="btn btn-sm" href={`${REPOSITORY}/releases`} target="_blank" rel="noreferrer">Changelog</a><a className="btn btn-sm" href={`${REPOSITORY}#readme`} target="_blank" rel="noreferrer">How to update</a></Row>
      <Row title="Source" detail="github.com/karloks2005/Infradeck"><a className="btn btn-sm" href={REPOSITORY} target="_blank" rel="noreferrer">Open</a></Row>
      <form className="flex flex-wrap items-end gap-3 px-[18px] py-3.5" onSubmit={(event) => { event.preventDefault(); run(async () => { const saved = await put({ instance_name: name }); setName(saved.instance_name || ''); onSaved(saved); refreshOverview() }, 'Saved.') }}>
        <Field label="Name of this install" hint="Shown in the sidebar and on the sign-in page, where anyone who can reach it can read it. Leave empty for the default." className="min-w-0 flex-1 basis-72"><input className="field h-9" maxLength={60} value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <button type="submit" className="btn btn-primary h-9" disabled={state.busy || name === (settings.instance_name || '')}>Save name</button>
        <Outcome state={state} />
      </form>
    </div>
  )
}

// The phone's Admin screen: servers first with their status in each subtitle, then the instance settings as rows.
function PhoneHub({ overview, settings, system }) {
  const { data: users } = useLoaded('/admin/users')
  const { data: rules } = useLoaded('/admin/rules')
  useEffect(() => { if (window.location.hash === '#instance') requestAnimationFrame(() => document.getElementById('instance')?.scrollIntoView({ block: 'start' })) }, [])
  if (!overview || !settings || !system) return <p className="m-0 py-16 text-center text-[13px] text-muted">Loading…</p>
  const row = (to, title, detail, value, dot, tone) => (
    <Link key={to + title} to={to} className="flex min-h-14 items-center gap-3 px-4 py-2.5">
      {dot !== undefined && <span className={`dot shrink-0 ${dot}`} />}
      <span className="min-w-0 flex-1"><span className="block truncate text-[15px] text-paper">{title}</span>{detail && <span className="mt-0.5 block truncate text-xs text-muted">{detail}</span>}</span>
      {value != null && <span className={`shrink-0 text-[13px] ${tone === 'warn' ? 'text-warn' : 'text-muted'}`}>{value}</span>}
      <Icon name="chevron" size={14} className="shrink-0 text-muted" />
    </Link>
  )
  const waiting = (overview.pending_collectors || []).filter((item) => item.status === 'pending').length
  const invited = users ? (users.invites || []).length : 0
  const on = rules ? rules.rules.filter((rule) => rule.enabled).length : null
  return <>
    {waiting > 0 && <Link to="/admin?section=connect" className="flex items-center gap-3 rounded-md border border-warn/40 px-4 py-3"><span className="dot dot-warn shrink-0" /><span className="min-w-0 flex-1 text-[13px] text-paper">{waiting} {waiting === 1 ? 'machine is' : 'machines are'} asking to join</span><Icon name="chevron" size={14} className="text-muted" /></Link>}
    <section aria-label="Servers">
      <div className="mb-2.5 flex items-baseline gap-2"><h2 className="m-0 text-[15px] font-semibold text-paper">Servers</h2><span className="text-xs text-muted">{overview.servers.length}</span><Link to="/admin?section=connect" className="ml-auto inline-flex h-7 items-center text-[13px] text-soft">Add</Link></div>
      <div className="divide-y divide-line overflow-hidden rounded-md border border-line bg-ink">
        {overview.servers.map((server) => {
          const version = server.system?.collector_version
          const detail = [server.ip_address, version ? `collector ${version}` : 'older collector', `every ${intervalLabel(server.check_interval_seconds)}`, server.system?.helper_installed ? null : 'no helper'].filter(Boolean).join(' · ')
          return row(serverLink(server.id, 'system'), server.hostname, detail, null, STATE_DOT[serverState(server)])
        })}
        {!overview.servers.length && <p className="m-0 px-4 py-4 text-[13px] text-muted">No servers yet.</p>}
      </div>
      <Link to="/admin?section=servers" className="mt-2 inline-flex h-9 items-center text-[13px] text-soft">Rename, interval or remove →</Link>
    </section>
    <section id="instance" aria-label="Instance" className="scroll-mt-4">
      <h2 className="m-0 mb-2.5 text-[15px] font-semibold text-paper">Instance</h2>
      <div className="divide-y divide-line overflow-hidden rounded-md border border-line bg-ink">
        {row('/admin?section=users', 'Users', null, users ? `${users.users.length}${invited ? ` · ${invited} invited` : ''}` : null)}
        {row('/admin?section=rules', 'Alert rules', null, rules ? `${on} on · ${rules.rules.length - on} off` : null)}
        {row('/admin?section=agent', 'Incident agent', null, !system.agent.key_configured ? 'no key' : system.agent.budget.cap ? `${dollars(system.agent.budget.cost)} of ${dollars(system.agent.budget.cap)}` : `${dollars(system.agent.budget.cost)} this month`, undefined, !system.agent.key_configured || system.agent.budget.state !== 'ok' ? 'warn' : undefined)}
        {row('/admin?section=retention', 'Data and retention', null, megabytes(system.database.bytes))}
        {row('/admin?section=about', 'About', null, system.version)}
      </div>
    </section>
  </>
}

export default function AdminPage({ user, onLogout }) {
  const { data: overview } = useOverview(Boolean(user), 15, onLogout)
  const settings = useLoaded('/admin/settings')
  const system = useLoaded('/admin/system')
  const [ruleCount, setRuleCount] = useState(null)
  const pending = overview?.pending_collectors || []
  const sections = [{ id: 'servers', label: 'Servers', count: overview?.servers.length ?? null }, { id: 'connect', label: 'Connect a server' }, { id: 'users', label: 'Users' }, { id: 'rules', label: 'Alert rules', count: ruleCount }, { id: 'agent', label: 'Incident agent' }, { id: 'retention', label: 'Data & retention' }, { id: 'about', label: 'About' }]
  const ready = overview && settings.data && system.data
  return (
    <AppShell user={user} onLogout={onLogout} crumbs={[{ label: 'Admin' }]} bare phoneHeader>
      <SettingsLayout title="Admin" description="Settings for this Infradeck instance and everyone who uses it." sections={sections} groups={ADMIN_GROUPS}
        phoneHub={<PhoneHub overview={overview} settings={settings.data} system={system.data} />} phoneBack={{ label: 'Admin', to: '/admin' }}>
        {(settings.error || system.error) && !ready && <Notice>{settings.error || system.error}</Notice>}
        {!ready && !settings.error && !system.error && <p className="m-0 py-16 text-center text-[13px] text-muted">Loading…</p>}
        {ready && <>
          <Section id="servers" title="Servers" description="Each server runs a small collector container that reports every few seconds. Removing a server deletes its stored metrics and incidents."><Servers overview={overview} /></Section>
          <Section id="connect" title="Connect a server" description="Name it, run one command on that machine, then add it when it shows up. The token works once and expires.">
            <div className="grid items-start gap-4 2xl:grid-cols-2 [&>*]:min-w-0">
              {pending.some((item) => item.status === 'pending' && item.details?.dashboard_host) && <ThisMachineCard pending={pending} onChanged={refreshOverview} />}
              <AnotherMachineCard pending={pending} onChanged={refreshOverview} />
            </div>
          </Section>
          <Section id="users" title="Users" description="Who can sign in, what they may do, and whether they must use two-factor."><Users me={user} /></Section>
          <Section id="rules" title="Alert rules" description="What opens an incident. Rules apply to every server. The “Fired” column shows how noisy each rule has been."><Rules onCount={setRuleCount} /><MountOverrides settings={settings.data} onSaved={settings.setData} /></Section>
          <Section id="agent" title="Incident agent" description="Diagnoses incidents and answers questions using the data Infradeck already has. It is read-only: it can't run commands, restart containers or change files on your servers."><Agent settings={settings.data} system={system.data} onSaved={settings.setData} /></Section>
          <Section id="retention" title="Data & retention" description="How long history is kept and how big it is."><Retention settings={settings.data} system={system.data} onSaved={settings.setData} /></Section>
          <Section id="about" title="About"><About settings={settings.data} system={system.data} onSaved={settings.setData} /></Section>
        </>}
      </SettingsLayout>
    </AppShell>
  )
}
