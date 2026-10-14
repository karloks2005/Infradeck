// Tests for the Overview page helpers. Run with `node --test` in this folder.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  attentionAction, bootHistory, classifyPort, collapsePaths, collapseRanges, describeEvent, describeGroup, eventStrip, eventsToCsv, firewallLine, forecastMonth, forecastPhrase,
  formatMegabytes, formatPercent, formatQuiet, formatSpan, formatUsedOfTotal, groupContainers, groupNetworkEvents, groupRepeats, healthChecks, healthVerdict, installCommands,
  listenerRows, niceCeiling, parsePorts, serverState, serverStateLabel, shortKernel, sortListeners, sortServers, systemLines, trafficRates, CONTAINER_FILTERS, reachablePortSet,
} from '../src/dashboard/logic.js'

const NOW = new Date('2026-10-13T15:50:00Z').getTime()
const seen = new Date(NOW - 4000).toISOString()

test('numbers read the way the design writes them', () => {
  assert.deepEqual([64, 11.7, 0.03, 0.449, 87.04, 0, null].map(formatPercent), ['64%', '11.7%', '0.03%', '0.45%', '87%', '0%', '—'])
  assert.deepEqual([26, 393.4, 3000.3, 20000].map(formatMegabytes), ['26 MB', '393 MB', '2.93 GB', '19.5 GB'])
  assert.equal(formatUsedOfTotal(27.2, 31.3), '27.2 / 31.3 GB')
  assert.equal(formatUsedOfTotal(5427, 7475), '5.3 / 7.3 TB')
  assert.equal(formatUsedOfTotal(79, 457, ' of '), '79 of 457 GB')
  assert.deepEqual([4, 720, 7200, 41 * 86400].map(formatSpan), ['4 s', '12 min', '2 h', '41 d'])
  assert.deepEqual([8040, 6 * 86400, 86400].map(formatQuiet), ['2 h 14 min', '6 days', '1 day'])
})

test('forecasts are short, and silent when there is no trend', () => {
  assert.equal(forecastPhrase(180), 'Full in about 6 months')
  assert.equal(forecastPhrase(180, { short: true }), 'full in ~6 mo')
  assert.equal(forecastPhrase(6.8 * 365), 'Full in about 6.8 years')
  assert.equal(forecastPhrase(null), null)
  assert.equal(forecastPhrase(99999), null)
  assert.equal(forecastMonth(180, NOW), 'full around April 2027')
})

test('a server is as bad as the worst thing open on it', () => {
  const server = (name, open, lastSeen = seen) => ({ hostname: name, alerts_by_severity: open, last_seen_at: lastSeen, check_interval_seconds: 15 })
  const fleet = [server('nas', {}), server('server', { WARNING: 2 }), server('homelab-01', { CRITICAL: 1, WARNING: 3 }), server('gone', {}, new Date(NOW - 3600e3).toISOString())]
  assert.deepEqual(fleet.map((item) => serverState(item, NOW)), ['ok', 'warn', 'critical', 'offline'])
  assert.deepEqual(fleet.map((item) => serverStateLabel(item, NOW)), ['healthy', '2 warnings', '1 critical', 'offline'])
  assert.deepEqual(sortServers(fleet, NOW).map((item) => item.hostname), ['homelab-01', 'gone', 'server', 'nas'])
  assert.deepEqual(systemLines({ os_info: 'Debian GNU/Linux 12 (bookworm)', cpu_cores: 8, total_ram_gb: 31.3, ip_address: '10.0.0.21', host: { system_uptime_seconds: 41 * 86400 } }), ['Debian GNU/Linux 12 · 8 cores · 31 GB', '10.0.0.21 · up 41 d'])
  assert.equal(shortKernel('6.8.0-84-generic'), '6.8.0-84')
})

test('each alert gets the one action that fits its rule', () => {
  assert.equal(attentionAction({ severity: 'WARNING', rule_key: 'network:exposed' }).label, 'Review ports')
  assert.equal(attentionAction({ severity: 'CRITICAL', rule_key: 'container:gitlab:restart_loop' }).label, 'Open diagnosis')
  assert.equal(attentionAction({ severity: 'WARNING', rule_key: 'host:swap' }).label, 'See trend')
})

test('published ports are parsed once per host port and runs are collapsed', () => {
  assert.deepEqual(parsePorts('8080->80/tcp, 8080->80/tcp, 3478->3478/udp, junk'), [{ host: 3478, container: 3478, proto: 'udp' }, { host: 8080, container: 80, proto: 'tcp' }])
  assert.deepEqual(collapseRanges([9103, 9101, 9102, 9200]), ['9101-9103', '9200'])
})

const server = {
  containers: [
    { name: 'mariadb', image: 'mariadb:11.4', ports: '3306->3306/tcp', status: 'running' },
    { name: 'nginx-proxy-manager', image: 'jc21/nginx-proxy-manager:2', ports: '80->80/tcp, 443->443/tcp, 81->81/tcp', status: 'running' },
    { name: 'coturn', image: 'coturn/coturn:4.6', ports: '3478->3478/tcp, 3478->3478/udp', status: 'running' },
    { name: 'adguard', image: 'adguard/adguardhome', ports: '53->53/tcp', status: 'running' },
  ],
  system: { firewall_allowed: [{ proto: 'tcp', port: 22, file: 'nftables' }] },
  network: {
    listeners: [
      { proto: 'tcp', port: 22, scope: 'any', first_seen: '2026-10-01T00:00:00Z' }, { proto: 'tcp', port: 80, scope: 'any', first_seen: '2026-10-01T00:00:00Z' }, { proto: 'tcp', port: 443, scope: 'any', first_seen: '2026-10-01T00:00:00Z' },
      { proto: 'tcp', port: 81, scope: 'any', first_seen: '2026-10-01T00:00:00Z' }, { proto: 'tcp', port: 3306, scope: 'any', first_seen: '2026-10-01T00:00:00Z' },
      { proto: 'tcp', port: 3478, scope: 'any', first_seen: '2026-10-01T00:00:00Z' }, { proto: 'udp', port: 3478, scope: 'any', first_seen: '2026-10-01T00:00:00Z' },
      { proto: 'tcp', port: 53, scope: 'loopback', first_seen: '2026-10-01T00:00:00Z' }, { proto: 'tcp', port: 5173, scope: 'loopback', first_seen: '2026-10-01T00:00:00Z' },
    ],
    firewall: [{ name: 'ufw-conf', enabled: false, rules: 0 }, { name: 'nftables', rules: 12 }],
  },
}

test('each port names its owner, and databases and admin panels are marked', () => {
  const rows = listenerRows(server)
  const byLabel = Object.fromEntries(rows.map((row) => [row.label, row]))
  assert.deepEqual(Object.keys(byLabel).sort(), ['22', '3306', '3478', '53', '5173', '80, 443', '81'].sort())
  assert.deepEqual([byLabel['3306'].owner, byLabel['3306'].what, byLabel['3306'].risky, byLabel['3306'].reason], ['mariadb', 'database', true, 'Databases rarely need this'])
  assert.deepEqual([byLabel['81'].owner, byLabel['81'].what, byLabel['81'].risky], ['nginx-proxy-manager', 'admin UI', true])
  assert.deepEqual([byLabel['80, 443'].owner, byLabel['80, 443'].risky, byLabel['80, 443'].reason], ['nginx-proxy-manager', false, 'Expected, public entry'])
  assert.equal(byLabel['3478'].proto, 'tcp+udp')
  assert.deepEqual([byLabel['22'].owner, byLabel['22'].rule, byLabel['3306'].rule], ['sshd', 'nftables', null])
  assert.deepEqual([byLabel['53'].reachable, byLabel['53'].risky, byLabel['5173'].owner], [false, false, null])
  assert.deepEqual(sortListeners(rows).slice(0, 2).map((row) => row.label), ['81', '3306'])
  assert.equal(firewallLine(server), 'ufw disabled · nftables.conf 12 rules')
  assert.equal(classifyPort('tcp', 5432, null).risky, true)
  // With the optional port-owners helper, host processes get their names; a container still wins over docker-proxy.
  const named = Object.fromEntries(listenerRows({ ...server, system: { ...server.system, listener_owners: [{ proto: 'tcp', port: 5173, process: 'node' }, { proto: 'tcp', port: 3306, process: 'docker-proxy' }, { proto: 'tcp', port: 22, process: 'sshd' }] } }).map((row) => [row.label, row]))
  assert.deepEqual([named['5173'].owner, named['5173'].ownerKind, named['3306'].owner, named['3306'].ownerKind], ['node', 'host', 'mariadb', 'container'])
})

test('a container counts as exposed only when another machine can reach one of its ports', () => {
  const reachable = reachablePortSet(server)
  assert.deepEqual(server.containers.filter((container) => CONTAINER_FILTERS.exposed(container, reachable)).map((container) => container.name), ['mariadb', 'nginx-proxy-manager', 'coturn'])
  assert.equal(reachablePortSet({ network: { listeners: [] } }), null)
})

test('noisy port events collapse into lines that say what actually changed', () => {
  const at = (hours) => new Date(NOW - hours * 3600e3).toISOString()
  const events = [
    { time: at(20), kind: 'port_opened', detail: 'New listener: tcp/8081 (a specific address)' },
    { time: at(20), kind: 'port_closed', detail: 'Listener closed: tcp/8081 (loopback only)' },
    { time: at(50), kind: 'port_opened', detail: 'New listener: udp/3479 (all interfaces)' },
    { time: at(50), kind: 'port_closed', detail: 'Listener closed: udp/3479 (all interfaces)' },
    { time: at(70), kind: 'port_opened', detail: 'New listener: udp/3479 (all interfaces)' },
    { time: at(72), kind: 'port_opened', detail: 'New listener: tcp/3478 (all interfaces)' },
    { time: at(80), kind: 'firewall_weakened', detail: 'ufw was enabled and is now disabled' },
  ]
  const lines = groupNetworkEvents(events, NOW)
  assert.deepEqual(lines.map((line) => [line.age, line.label, line.port, line.tone]), [
    ['20 h', 'Now exposed', 'tcp/8081', 'warn'], ['2–3 d', 'Re-bound ×3', 'udp/3479', 'plain'], ['3 d', 'Now exposed', 'tcp/3478', 'warn'], ['3 d', 'Firewall weaker', null, 'warn'],
  ])
  assert.equal(lines[0].text, 'moved from loopback to a specific address')
})

test('containers group by compose project, busiest first, standalone last', () => {
  const containers = [
    { name: 'collabora', project: null, cpu_pct: 0.4, memory_used_mb: 393, status: 'running' },
    { name: 'gitlab-runner', project: 'gitlab', cpu_pct: 0.03, memory_used_mb: 26, status: 'running' },
    { name: 'gitlab', project: 'gitlab', cpu_pct: 58.3, memory_used_mb: 3000, status: 'running' },
    { name: 'redis', project: 'infradeck', cpu_pct: 1, memory_used_mb: 20, status: 'exited' },
    { name: 'backend', project: 'infradeck', cpu_pct: 5, memory_used_mb: 146, status: 'running' },
  ]
  const groups = groupContainers(containers)
  assert.deepEqual(groups.map((group) => [group.name, group.count, group.problems]), [['gitlab', 2, 0], ['infradeck', 2, 1], ['Standalone', 1, 0]])
  assert.deepEqual(groups[0].containers.map((container) => container.name), ['gitlab', 'gitlab-runner'])
  assert.deepEqual(groups[1].containers.map((container) => container.name), ['redis', 'backend'])   // the stopped one first
  assert.equal(Math.round(groups[0].memoryMb), 3026)
  assert.deepEqual(groupContainers(containers, { grouped: false })[0].count, 5)
  assert.deepEqual(collapsePaths('/srv/gitlab/config, /srv/gitlab/logs, /srv/gitlab/data, web_cache, /etc/x'), ['/srv/gitlab/{config,logs,data}', 'web_cache', '/etc/x'])
})

test('health checks give plain verdicts and count what needs a look', () => {
  const checks = healthChecks({
    ...server,
    host: { reboot_required: true, system_uptime_seconds: 10 * 86400, last_shutdown: 'clean', ntp_synced: true, oom_kill_total: 0, kernel_tainted: 0, swap_pct: 0 },
    system: { reboot_kernel: '6.8.0-85-generic', time_service: 'chrony' }, system_events: [],
  }, NOW)
  const text = Object.fromEntries(checks.map((check) => [check.label, [check.text, check.tone]]))
  assert.deepEqual(text.Firewall, ['ufw off · 6 ports exposed', 'warn'])
  assert.deepEqual(text.Updates, ['Reboot needed for 6.8.0-85', 'warn'])
  assert.deepEqual(text.Booted, ['10 d ago · clean shutdown', 'ok'])
  assert.deepEqual(text.Clock, ['Synced (chrony)', 'ok'])
  assert.deepEqual(text['OOM kills'], ['None in 24 h', 'ok'])
  assert.deepEqual(text['Kernel log'], ['Clean', 'ok'])
  assert.deepEqual(text.Internet, ['Not checked', 'unknown'])
  assert.equal(healthVerdict(checks), '2 to look at')
  assert.equal(healthVerdict(checks.filter((check) => check.tone !== 'warn')), 'all passing')
})

test('traffic is the change between check-ins, with a gap across a reboot', () => {
  const samples = [{ time: '2026-10-13T15:00:00Z', net_recv_mb: 100, net_sent_mb: 50 }, { time: '2026-10-13T15:00:10Z', net_recv_mb: 121, net_sent_mb: 56 }, { time: '2026-10-13T15:00:20Z', net_recv_mb: 3, net_sent_mb: 1 }]
  assert.deepEqual(trafficRates(samples).map((point) => [point.in, point.out]), [[2.1, 0.6], [null, null]])
  assert.deepEqual([0.4, 2.1, 37, 5.2, 0].map(niceCeiling), [0.4, 4, 40, 6, 1])
  assert.deepEqual(bootHistory([{ kind: 'reboot', time: 'b', detail: 'The host rebooted after a clean shutdown' }, { kind: 'oom_kill', time: 'x', detail: '' }, { kind: 'reboot', time: 'a', detail: 'The host restarted after an unclean shutdown (crash)' }]), [{ time: 'a', unclean: true }, { time: 'b', unclean: false }])
})

test('the timeline folds repeats, keeps alerts apart and draws the week', () => {
  const at = (hours) => new Date(NOW - hours * 3600e3).toISOString()
  const events = [
    { time: at(1), source: 'alert', kind: 'alert_resolved', severity: 'WARNING', title: 'Memory usage on server is 93%', duration_seconds: 1080 },
    { time: at(2), source: 'alert', kind: 'alert_opened', severity: 'WARNING', title: 'Memory usage on server is 93%', resolved: true },
    { time: '2026-10-13T03:00:40Z', source: 'container', kind: 'image_changed', detail: 'Container jellyfin now runs a different image build (a to b)' },
    { time: '2026-10-13T03:00:20Z', source: 'container', kind: 'image_changed', detail: 'Container collabora now runs a different image build (a to b)' },
    { time: '2026-10-10T06:32:00Z', source: 'collector', kind: 'collector_stopped', severity: 'CRITICAL', title: 'Server server is offline' },
  ]
  const groups = groupRepeats(events)
  assert.deepEqual(groups.map((group) => group.events.length), [1, 1, 2, 1])
  const folded = describeGroup(groups[2])
  assert.deepEqual([folded.source, folded.lead, folded.text], ['Containers ×2', 'jellyfin, collabora', 'now run a different image build'])
  assert.deepEqual([describeEvent(events[0]).source, describeEvent(events[0]).text], ['Alert resolved', 'cleared after 18 min'])
  assert.equal(describeEvent(events[4]).tag.text, 'critical')
  const strip = eventStrip(events, 7, NOW)
  assert.equal(strip.length, 7)
  assert.equal(strip.at(-1).label, 'Today')
  assert.equal(strip.reduce((sum, day) => sum + day.count, 0), 5)
  assert.ok(strip.some((day) => day.bars.some((bar) => bar.tone === 'signal')))
  assert.equal(eventsToCsv(events).split('\n').length, 6)
})

test('the install commands carry the token and the chosen name three ways', () => {
  const commands = installCommands({ url: 'https://deck.home.lan/api', token: 'jt_abc', name: 'homelab-01', collectorImage: 'ghcr.io/x/collector@sha256:1', proxyImage: 'ghcr.io/x/proxy@sha256:2' })
  assert.deepEqual(Object.keys(commands), ['docker run', 'compose', 'systemd'])
  for (const command of Object.values(commands)) {
    assert.ok(command.includes('jt_abc') && command.includes('homelab-01') && command.includes('ghcr.io/x/proxy@sha256:2'))
  }
  assert.ok(!installCommands({ url: 'u', token: 't', name: '', collectorImage: 'c', proxyImage: 'p' }).compose.includes('INFRADECK_SERVER_NAME'))
})
