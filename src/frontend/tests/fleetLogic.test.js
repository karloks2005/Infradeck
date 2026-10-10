// Tests for the all-servers helpers. Run with `node --test` in this folder.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { availabilityRows, bucketLabel, containerState, containerStateSlices, formatDuration, incidentColumns, isOnline, serversByDisk, topContainers } from '../src/fleetLogic.js'

const servers = [
  { hostname: 'nas', host: { disk_usage_pct: 91 }, containers: [
    { name: 'web', status: 'running', healthcheck_status: 'healthy', memory_used_mb: 200, cpu_pct: 5 },
    { name: 'db', status: 'running', healthcheck_status: 'unhealthy', memory_used_mb: 900, cpu_pct: null },
    { name: 'job', status: 'exited', memory_used_mb: null, cpu_pct: null },
  ] },
  { hostname: 'web01', host: { disk_usage_pct: 97 }, containers: [
    { name: 'proxy', status: 'restarting', memory_used_mb: 50, cpu_pct: 40 },
    { name: 'cache', status: 'running', memory_used_mb: 500, cpu_pct: 12 },
  ] },
  { hostname: 'empty', host: null, containers: undefined },
]

test('every container falls into exactly one state', () => {
  assert.equal(containerState({ status: 'running' }), 'running')
  assert.equal(containerState({ status: 'running', healthcheck_status: 'unhealthy' }), 'unhealthy')
  assert.equal(containerState({ status: 'restarting' }), 'restarting')
  for (const status of ['exited', 'dead', 'created', 'paused', 'weird']) assert.equal(containerState({ status }), 'stopped')
  const slices = containerStateSlices(servers)
  assert.deepEqual(slices.map((slice) => [slice.label, slice.value]), [['Running', 2], ['Unhealthy', 1], ['Restarting', 1], ['Stopped', 1]])
  assert.deepEqual(containerStateSlices([]).map((slice) => slice.value), [0, 0, 0, 0])
})

test('top containers rank across servers, skip unknown values and honour the count', () => {
  assert.deepEqual(topContainers(servers, 'memory_used_mb', 3), [{ label: 'nas / db', value: 900 }, { label: 'web01 / cache', value: 500 }, { label: 'nas / web', value: 200 }])
  assert.deepEqual(topContainers(servers, 'cpu_pct').map((row) => row.label), ['web01 / proxy', 'web01 / cache', 'nas / web'])
  assert.deepEqual(topContainers([], 'cpu_pct'), [])
})

test('servers are ranked by disk usage and coloured by the thresholds; servers without data are left out', () => {
  assert.deepEqual(serversByDisk(servers).map((row) => [row.label, row.value, row.color]), [['web01', 97, '#f87171'], ['nas', 91, '#fbbf24']])
  assert.equal(serversByDisk([{ hostname: 'x', host: { disk_usage_pct: 40 } }])[0].color, '#7dd3fc')
})

test('a server is online while it keeps reporting', () => {
  const now = Date.UTC(2026, 2, 5, 12, 0)
  assert.equal(isOnline({ last_seen_at: new Date(now - 100000).toISOString(), check_interval_seconds: 60 }, now), true)
  assert.equal(isOnline({ last_seen_at: new Date(now - 200000).toISOString(), check_interval_seconds: 60 }, now), false)
  assert.equal(isOnline({ last_seen_at: null }, now), false)
})

test('availability rows skip unknown servers, put the worst first and colour by how bad it is', () => {
  const rows = availabilityRows([{ hostname: 'a', percent: 100, checkins: 10, expected: 10 }, { hostname: 'b', percent: 96.5, checkins: 9, expected: 10 }, { hostname: 'c', percent: null, checkins: 0, expected: 5 }, { hostname: 'd', percent: 80, checkins: 8, expected: 10 }])
  assert.deepEqual(rows.map((row) => [row.label, row.color]), [['d', '#f87171'], ['b', '#fbbf24'], ['a', '#34d399']])
})

test('durations read naturally', () => {
  assert.equal(formatDuration(null), '–')
  assert.equal(formatDuration(0.2), 'under a minute')
  assert.equal(formatDuration(45), '45 min')
  assert.equal(formatDuration(300), '5.0 h')
  assert.equal(formatDuration(60 * 24 * 5), '5.0 days')
  assert.equal(formatDuration(NaN), '–')
})

test('incident columns fill quiet buckets with zeros, in order, and keep only the newest ones', () => {
  const days = incidentColumns([{ time: '2026-03-10T00:00:00+00:00', critical: 1, warning: 2, info: 0 }], 'day', '2026-03-08T10:00:00+00:00', '2026-03-11T12:00:00+00:00')
  assert.deepEqual(days.map((column) => [bucketLabel(column.time, 'day'), column.values.critical + column.values.warning]), [['8 Mar', 0], ['9 Mar', 0], ['10 Mar', 3], ['11 Mar', 0]])
  const weeks = incidentColumns([], 'week', '2026-03-04T00:00:00+00:00', '2026-03-20T00:00:00+00:00')
  assert.deepEqual(weeks.map((column) => bucketLabel(column.time, 'week')), ['2 Mar', '9 Mar', '16 Mar'])          // Mondays
  const months = incidentColumns([], 'month', '2026-11-15T00:00:00+00:00', '2027-02-02T00:00:00+00:00')
  assert.deepEqual(months.map((column) => bucketLabel(column.time, 'month')), ['Nov 2026', 'Dec 2026', 'Jan 2027', 'Feb 2027'])
  assert.equal(incidentColumns([], 'hour', '2026-03-01T00:00:00+00:00', '2026-03-20T00:00:00+00:00', 5).length, 5)
})
