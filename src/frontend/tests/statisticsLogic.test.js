// Tests for the Statistics page helpers. Run with `node --test` in this folder.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { busiestAt, busiestHour, deltaPoints, eventMarks, findGaps, findings, percentile, projection, summarize, timeAtLevels, timelineCsv } from '../src/statistics/logic.js'

const HOUR = 3600000
const START = new Date(2026, 9, 6, 0, 0).getTime()   // Tue 6 Oct, local time
const half = (index) => START + index * 1800000
const week = (value) => Array.from({ length: 336 }, (_, index) => ({ t: half(index), samples: 120, ...value(index, new Date(half(index))) }))

test('summary figures carry the moment of the peak', () => {
  const series = [{ t: 1, avg: 10, max: 20, samples: 1 }, { t: 2, avg: 30, max: 100, samples: 3 }, { t: 3, avg: null, max: null, samples: 1 }]
  assert.deepEqual(summarize(series), { average: 25, peak: 100, peakAt: 2, last: null })
  assert.equal(deltaPoints(9.7, 8.5), '+1.2 pts')
  assert.equal(deltaPoints(50, 52.1), '−2.1 pts')
  assert.equal(deltaPoints(5, null), null)
})

test('a hole between points is a gap; history starting late is not', () => {
  const times = [half(4), half(5), half(6), half(17), half(18)]
  assert.deepEqual(findGaps(times, 1800, half(18) + 1), [{ from: half(7), to: half(17) }])
  assert.deepEqual(findGaps(times, 1800, half(30)), [{ from: half(7), to: half(17) }, { from: half(19), to: half(30) }])
  assert.deepEqual(findGaps([], 1800, half(30)), [])
})

test('the busiest container is looked up by the period that covers a moment', () => {
  const lookup = { bucketSeconds: 3600, points: [{ t: START, name: 'web', cpu: 5 }, { t: START + 3 * HOUR, name: 'gitlab', cpu: 94 }] }
  assert.equal(busiestAt(lookup, START + 3.5 * HOUR).name, 'gitlab')
  assert.equal(busiestAt(lookup, START + 2 * HOUR), null)   // nothing reported for that hour
  assert.equal(busiestAt(null, START), null)
})

test('findings name swap growth, a nightly spike and a gap, in plain words', () => {
  const cpu = week((index, date) => (date.getHours() === 3 && date.getMinutes() === 0 ? { avg: 31, max: 100 } : { avg: 9, max: 22 })).filter((_, index) => index < 200 || index > 211)
  const ram = week(() => ({ avg: 52, max: 59 }))
  const swap = week((index) => ({ avg: index < 250 ? 0 : (index - 250) * 0.19, max: index < 250 ? 0 : (index - 250) * 0.19 }))
  const load = week(() => ({ avg: 0.4, max: 2.1 }))
  const busiest = { bucketSeconds: 3600, points: cpu.filter((point) => new Date(point.t).getHours() === 3).map((point) => ({ t: point.t - (point.t % HOUR), name: 'gitlab', cpu: 94 })) }
  const result = findings({ cpu, ram, swap, load, cores: 4, swapTotalGb: 16, bucketSeconds: 1800, gaps: findGaps(cpu.map((point) => point.t), 1800, half(335)), events: [], busiest, containers: [{ name: 'gitlab', swap_mb: 1126 }, { name: 'web', swap_mb: 3 }], disks: [] })

  assert.deepEqual(result.map((finding) => finding.key), ['swap', 'spike', 'gap'])
  assert.equal(result[0].title, 'Swap keeps growing while memory has room.')
  assert.match(result[0].text, /^Swap went from 0 GB to 2\.6 GB since Sun 11 Oct, but RAM never passed 59%\. .* gitlab holds the most swap \(1\.10 GB\)\.$/)
  assert.equal(result[0].at, half(255))   // the last moment it was still within a point of its lowest
  assert.equal(result[1].title, 'CPU hits 100% at 03:00 every night.')
  assert.match(result[1].text, /^Seen on 7 of 7 days, and gitlab is the busiest container each time\. Average CPU stays around 9% the rest of the day\.$/)
  assert.equal(result[1].marks.length, 7)
  assert.equal(result[1].marks[0].label, 'nightly gitlab peak')
  // With the check-ins still around, the spikes get a length.
  const spikes = cpu.filter((point) => new Date(point.t).getHours() === 3 && new Date(point.t).getMinutes() === 0).map((point, index) => ({ t: point.t + 60000, seconds: index % 2 ? 14 * 60 : 8 * 60 }))
  const timed = findings({ cpu, ram, swap, load, cores: 4, bucketSeconds: 1800, gaps: [], events: [], busiest, spikes, disks: [] }).find((finding) => finding.key === 'spike')
  assert.match(timed.text, /^Spikes last 8 to 14 minutes and gitlab is the busiest container each time\. /)
  assert.equal(result[2].title, 'No data for 6 h on Sat 10 Oct.')
  assert.match(result[2].text, /^Nothing was reported from 04:00 to 10:00\. No reboot was recorded/)
})

test('a quiet range has no findings, and a full disk outranks everything', () => {
  const flat = week(() => ({ avg: 5, max: 9 }))
  const quiet = { cpu: flat, ram: flat, swap: week(() => ({ avg: 0, max: 0 })), load: week(() => ({ avg: 0.2, max: 0.5 })), cores: 4, bucketSeconds: 1800, gaps: [], events: [], disks: [] }
  assert.deepEqual(findings(quiet), [])
  const busy = findings({ ...quiet, ram: week(() => ({ avg: 80, max: 96 })), load: week(() => ({ avg: 5, max: 7 })), disks: [{ mount: '/', days: 12, usedPct: 91 }] })
  assert.deepEqual(busy.map((finding) => finding.key), ['disk', 'load', 'memory'])
  assert.equal(busy[0].title, '/ fills up in about 12 days.')
})

test('time at each level adds up and gets one sentence', () => {
  const series = [...Array.from({ length: 63 }, () => ({ avg: 5, samples: 1 })), ...Array.from({ length: 35 }, () => ({ avg: 15, samples: 1 })), { avg: 25, samples: 1 }, { avg: 100, samples: 1 }]
  const { bands, sentence } = timeAtLevels(series)
  assert.deepEqual(bands.map((band) => [band.label, band.share]), [['0–10%', 63], ['10–20%', 35], ['20–30%', 1], ['30–50%', 0], ['50–80%', 0], ['80–100%', 1]])
  assert.equal(sentence, "98% of the time the CPU was under 20%. There's headroom for more services on this machine.")
  assert.match(timeAtLevels([{ avg: 95, samples: 1 }], 'memory').sentence, /above 80%/)
  assert.equal(timeAtLevels([]).sentence, '')
})

test('percentiles, the busiest hour and the disk projection', () => {
  assert.equal(percentile([12, 10, 31, 14, 11, null], 50), 12)
  assert.equal(percentile([12, 10, 31, 14, 11], 95), 31)
  assert.equal(percentile([], 50), null)
  const grid = Array.from({ length: 7 }, () => Array.from({ length: 24 }, (_, hour) => (hour === 3 ? 31 : 6)))
  assert.deepEqual(busiestHour(grid), { hour: 3, value: 31 })
  assert.equal(busiestHour(Array.from({ length: 7 }, () => Array(24).fill(6))), null)
  assert.deepEqual(projection(1000, 20, 80), { from: { t: 1000, value: 20 }, to: { t: 1000 + 80 * 86400000, value: 100 }, full: true })
  assert.equal(projection(1000, 20, 8000).full, false)
  assert.equal(projection(1000, 20, null), null)
})

test('event marks fold repeats and skip what is only noise on a chart', () => {
  const at = (hours) => new Date(START + hours * HOUR).toISOString()
  const events = [
    { time: at(3), kind: 'image_changed', detail: 'Container a now runs a different image build' }, { time: at(3.1), kind: 'image_changed', detail: 'Container b now runs a different image build' },
    { time: at(30), kind: 'collector_stopped', severity: 'CRITICAL', title: 'Server server is offline' }, { time: at(35), kind: 'collector_resumed', title: 'Server server is offline' },
    { time: at(60), kind: 'port_opened', detail: 'New listener: tcp/8081 (a specific address)' }, { time: at(900), kind: 'reboot', detail: 'The host rebooted' },
    { time: at(70), kind: 'packages_updated', detail: '41 packages updated by apt, kernel 6.8.0-85-generic waiting for reboot' },
    { time: at(80), kind: 'port_opened', detail: 'New listener: udp/3479 (all interfaces)' }, { time: at(81), kind: 'port_closed', detail: 'Listener closed: udp/3479 (all interfaces)' }, { time: at(82), kind: 'port_opened', detail: 'New listener: udp/3479 (all interfaces)' },
  ]
  const marks = eventMarks(events, START, START + 7 * 24 * HOUR)
  assert.deepEqual(marks.map((mark) => [mark.label, mark.count, mark.tone]), [['Images updated', 2, 'plain'], ['Collector offline', 1, 'plain'], ['8081 exposed', 1, 'warn'], ['apt upgrade · 41 packages', 1, 'warn'], ['3479 re-bound', 3, 'plain']])
  assert.equal(eventMarks([], START, START + HOUR, [{ t: START + 60000, label: 'nightly peak' }, { t: START - 1, label: 'outside' }]).length, 1)
})

test('the timeline exports as CSV', () => {
  const csv = timelineCsv([{ time: '2026-10-13T03:00:00+00:00', samples: 120, cpu: { avg: 31, max: 100 }, ram: null }], ['cpu', 'ram'])
  assert.equal(csv, 'time,samples,cpu_avg,cpu_max,ram_avg,ram_max\n2026-10-13T03:00:00+00:00,120,31,100,,')
})
