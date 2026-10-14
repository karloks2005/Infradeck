// Settings for this Infradeck instance and everyone who uses it: servers, users, alert rules, the incident
// agent, how long data is kept, and what version this is.
import { useEffect, useState } from 'react'
import { apiRequest } from '../api.js'
import { AnotherMachineCard, ThisMachineCard } from '../dashboard/EmptyFleet.jsx'
import { INTERVALS, intervalLabel } from '../dashboard/ServerPanel.jsx'
import { serverState } from '../dashboard/logic.js'
import { Icon } from '../icons.jsx'
import { refreshOverview, useOverview } from '../overviewStore.js'
import { AppShell } from '../shell.jsx'
import { Field, Notice, Outcome, Row, Section, SettingsLayout, ShownOnce, Switch, ago, useAction, useLoaded } from '../ui.jsx'

const REPOSITORY = 'https://github.com/karloks2005/Infradeck'
const STATE_DOT = { critical: 'dot-signal', warn: 'dot-warn', ok: 'dot-ok', offline: '' }
const SEVERITY = { CRITICAL: ['Critical', 'chip-signal'], WARNING: ['Warning', 'chip-warn'], INFO: ['Info', ''] }
const THRESHOLD_LABELS = {
  restart_loop_count: 'Restarts', restart_loop_window_minutes: 'Within (min)', disk_warning_pct: 'Warning from %', disk_critical_pct: 'Critical from %', inodes_warning_pct: 'Warning from %', inodes_critical_pct: 'Critical from %',
  memory_warning_pct: 'Warning from %', memory_critical_pct: 'Critical from %', swap_warning_pct: 'Warning from %', load_warning_per_core: 'Warning × cores', load_critical_per_core: 'Critical × cores', event_window_minutes: 'Stays open (min)',
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
        <thead><tr className="grid-head h-10"><th className="pl-[18px] font-medium">User</th><th className="font-medium">Role</th><th className="font-medium">Two-factor</th><th className="font-medium">Last seen</th><th className="pr-[18px]" /></tr></thead>
        <tbody>
          {data.users.map((user) => {
            const fixed = user.role_name === 'Owner' || user.id === me.id
            return (
              <tr key={user.id} className={`h-[54px] border-t border-line ${user.is_active ? '' : 'opacity-60'}`}>
                <td className="pl-[18px]"><span className="flex items-center gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-[11px] font-semibold" style={{ color: user.avatar_color || undefined }}>{initials(user.name)}</span><span className="min-w-0"><span className="block truncate font-medium text-paper">{user.name}{user.id === me.id && <span className="ml-2 text-xs font-normal text-muted">you</span>}</span><span className="block truncate text-xs text-muted">{user.email}</span></span></span></td>
                <td>{fixed ? <span className="text-paper">{user.role_name}</span> : <select className="select h-8" aria-label={`Role of ${user.name}`} value={user.role} onChange={(event) => change(user, { role: event.target.value })}><option value="admin">Admin</option><option value="user">Viewer</option></select>}</td>
                <td><span className={`chip ${user.two_factor ? 'chip-ok' : ''}`}>{user.two_factor ? 'On' : 'Off'}</span></td>
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
              <td className="text-soft">{invite.role === 'admin' ? 'Admin' : 'Viewer'}</td><td className="text-muted">n/a</td><td className="text-muted">never</td>
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
                {editing === rule.kind && <form onSubmit={save} className="my-2.5 flex flex-wrap items-end gap-2.5">
                  {rule.edit.map((key) => <Field key={key} label={THRESHOLD_LABELS[key] || key}><input type="number" step="any" className="field h-8 w-28" required value={values[key] ?? ''} onChange={(event) => setValues({ ...values, [key]: event.target.value })} /></Field>)}
                  <button type="submit" className="btn btn-sm btn-primary" disabled={state.busy}>Save</button>
                </form>}
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
        <button type="button" className="btn btn-sm" disabled title="Rules of your own are not built yet; the built-in ones can be tuned and switched off."><Icon name="plus" size={13} />New rule</button>
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
  const agent = system.agent
  return <>
    <div className="card divide-y divide-line">
      <Row icon="AI" title="Model provider" detail={agent.key_configured ? `Google Gemini · ${agent.model.replace('models/', '')} · key configured` : 'No API key configured. Set GEMINI_API_KEY in .env to turn the chat on.'} tone={agent.key_configured ? undefined : 'warn'}><button type="button" className="btn btn-sm" disabled title="The provider and key are set in .env (AGENT_MODEL, GEMINI_API_KEY) and need a restart.">Change</button></Row>
      <Row title="Diagnose new incidents automatically" detail="Always on. The diagnosis is built from stored metrics and events by fixed rules, so it costs nothing and sends nothing to the provider."><Switch label="Diagnose automatically" checked disabled onChange={() => {}} /></Row>
      <Row title="Read container logs" detail="Not available: Infradeck's Docker proxy can't read logs. See SECURITY.md."><Switch label="Read container logs" checked={false} disabled onChange={() => {}} /></Row>
      <Row title="Chat" detail="Available to every signed-in user while it is on. Questions and the incident's facts are sent to the provider."><Switch label="Chat with the agent" checked={settings.agent_chat_enabled} disabled={state.busy} onChange={(on) => run(async () => onSaved(await put({ agent_chat_enabled: on })))} /></Row>
      <Row title="Redact IPs and hostnames before sending" detail="Not available yet. Until then, turn Chat off if names must not leave this machine."><Switch label="Redact before sending" checked={false} disabled onChange={() => {}} /></Row>
    </div>
    <div className="card grid divide-y divide-line sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      {[[agent.incidents_this_month, 'incidents diagnosed this month'], [agent.chat_messages_this_month, 'chat messages this month'], ['—', 'provider cost · not tracked']].map(([value, label]) => <div key={label} className="px-[18px] py-4"><div className="text-2xl font-semibold leading-none text-paper">{value}</div><div className="mt-2 text-xs text-muted">{label}</div></div>)}
    </div>
    <Outcome state={state} />
  </>
}

const megabytes = (bytes) => (bytes == null ? '—' : bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`)

function Retention({ settings, system, onSaved }) {
  const [state, run] = useAction()
  const database = system.database
  return (
    <div className="card divide-y divide-line">
      <Row title="Check-ins and their summaries" detail="Every check-in is kept for a week; hourly, daily, weekly and monthly summaries for as long as this says. Statistics reads the summaries for ranges past 7 days.">
        <select className="select" aria-label="Retention" value={settings.metrics_retention_days} disabled={state.busy} onChange={(event) => run(async () => onSaved(await put({ metrics_retention_days: Number(event.target.value) })), 'Saved.')}>
          {[...new Set([7, 30, 90, 365, settings.metrics_retention_days])].sort((a, b) => a - b).map((days) => <option key={days} value={days}>{days === 365 ? '1 year' : `${days} days`}</option>)}
        </select>
      </Row>
      <Row title="Incidents" detail="Kept until their server is removed."><span className="text-[13px] text-soft">Forever</span></Row>
      <Row icon="db" title="Database" detail={`${database.engine}${database.host_rows != null ? ` · ${database.host_rows.toLocaleString()} check-ins stored, ${database.host_rows_week.toLocaleString()} from the last 7 days` : ''}`}>
        <span className="mr-2 text-lg font-semibold text-paper">{megabytes(database.bytes)}</span>
        <button type="button" className="btn btn-sm" disabled title="Not built yet. Back up the postgres volume with pg_dump for now (see SETUP_DATABASES.md).">Export</button>
        <button type="button" className="btn btn-sm" disabled title="Not built yet. Back up the postgres volume with pg_dump for now (see SETUP_DATABASES.md).">Back up now</button>
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
      <Row title={`Infradeck ${system.version}`} detail="Open source, MIT licensed. Infradeck doesn't call home, so it can't tell you about new versions; the changelog can."><a className="btn btn-sm" href={`${REPOSITORY}/releases`} target="_blank" rel="noreferrer">Changelog</a><a className="btn btn-sm" href={`${REPOSITORY}#readme`} target="_blank" rel="noreferrer">How to update</a></Row>
      <Row title="Source" detail="github.com/karloks2005/Infradeck"><a className="btn btn-sm" href={REPOSITORY} target="_blank" rel="noreferrer">Open</a></Row>
      <form className="flex flex-wrap items-end gap-3 px-[18px] py-3.5" onSubmit={(event) => { event.preventDefault(); run(async () => { const saved = await put({ instance_name: name }); setName(saved.instance_name || ''); onSaved(saved); refreshOverview() }, 'Saved.') }}>
        <Field label="Name of this install" hint="Shown in the sidebar and on the sign-in page, where anyone who can reach it can read it. Leave empty for the default." className="min-w-0 flex-1 basis-72"><input className="field h-9" maxLength={60} value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <button type="submit" className="btn btn-primary h-9" disabled={state.busy || name === (settings.instance_name || '')}>Save name</button>
        <Outcome state={state} />
      </form>
    </div>
  )
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
    <AppShell user={user} onLogout={onLogout} crumbs={[{ label: 'Admin' }]} bare>
      <SettingsLayout title="Admin" description="Settings for this Infradeck instance and everyone who uses it." sections={sections}>
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
