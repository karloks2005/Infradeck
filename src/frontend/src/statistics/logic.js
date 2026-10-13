// Pure helpers for the Statistics page: what stands out in a range, gaps, bands, percentiles and wording.
// Series are [{ t, avg, max, samples }] with t in milliseconds, oldest first. No React in here.
import { formatMegabytes, formatPercent, formatSpan } from '../dashboard/logic.js'

const DAY_MS = 86400000
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (value) => String(value).padStart(2, '0')

export const RANGES = [
  { id: '1h', label: '1 h', words: 'hour', previous: 'previous hour', short: 'previous hour', span: 'hour', days: 1 },
  { id: '6h', label: '6 h', words: '6 hours', previous: 'previous 6 hours', short: 'previous 6 h', span: 'time', days: 1 },
  { id: '24h', label: '24 h', words: '24 hours', previous: 'previous 24 hours', short: 'previous day', span: 'day', days: 2 },
  { id: '7d', label: '7 days', words: '7 days', previous: 'previous 7 days', short: 'previous week', span: 'week', days: 7 },
  { id: '30d', label: '30 days', words: '30 days', previous: 'previous 30 days', short: 'previous 30 days', span: 'time', days: 30 },
  { id: '90d', label: '90 days', words: '90 days', previous: 'previous 90 days', short: 'previous 90 days', span: 'time', days: 90 },
  { id: '1y', label: '1 year', words: 'year', previous: 'previous year', short: 'previous year', span: 'year', days: 366 },
  { id: 'all', label: 'All', words: 'whole history', previous: null, short: null, span: 'time', days: 366 },
]
export const RANGE_BY_ID = Object.fromEntries(RANGES.map((range) => [range.id, range]))

// "Thu 9 Oct 03:12", in the viewer's time zone, or just "Thu 9 Oct" for day-sized points.
export function formatMoment(time, { clock = true, utc = false } = {}) {
  const date = new Date(time)
  const day = utc ? `${WEEKDAYS[date.getUTCDay()]} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}` : `${WEEKDAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]}`
  return clock && !utc ? `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}` : day
}

export const formatClock = (time) => { const date = new Date(time); return `${pad(date.getHours())}:${pad(date.getMinutes())}` }

// "Thu 9 Oct, 03:00–03:30" for one point's period.
export function formatPeriod(time, bucketSeconds, utc = false) {
  if (utc || bucketSeconds >= 86400) return formatMoment(time, { clock: false, utc })
  return `${formatMoment(time, { clock: false })}, ${formatClock(time)}–${formatClock(time + bucketSeconds * 1000)}`
}

// "+1.2 pts" between two percentages, or null when either is unknown.
export function deltaPoints(current, previous) {
  if (current == null || previous == null) return null
  const delta = Number((current - previous).toFixed(1))
  return `${delta > 0 ? '+' : delta < 0 ? '−' : '±'}${Math.abs(delta)} pts`
}

// Weighted average and highest maximum of a series, with the moment of the maximum.
export function summarize(series) {
  let total = 0
  let weight = 0
  let peak = null
  for (const point of series) {
    if (point.avg != null) { total += point.avg * (point.samples || 1); weight += point.samples || 1 }
    const top = point.max ?? point.avg
    if (top != null && (peak === null || top > peak.value)) peak = { value: top, t: point.t }
  }
  return { average: weight ? total / weight : null, peak: peak?.value ?? null, peakAt: peak?.t ?? null, last: series.length ? series[series.length - 1].avg : null }
}

// Stretches with no data, as [{ from, to }] in milliseconds: before the first point is not a gap (history may
// simply start later), but a hole between points or a silence up to ``end`` is.
export function findGaps(times, bucketSeconds, end) {
  const step = bucketSeconds * 1000
  const gaps = []
  for (let index = 1; index < times.length; index += 1) {
    if (times[index] - times[index - 1] > step * 1.5) gaps.push({ from: times[index - 1] + step, to: times[index] })
  }
  if (times.length && end != null && end - times[times.length - 1] > step * 2.5) gaps.push({ from: times[times.length - 1] + step, to: end })
  return gaps
}

const span = (milliseconds) => {
  const minutes = Math.round(milliseconds / 60000)
  if (minutes < 60) return `${minutes} min`
  if (minutes < 48 * 60) return minutes % 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${Math.floor(minutes / 60)} h`
  return formatSpan(milliseconds / 1000)
}

// The value of ``lookup`` ({ bucketSeconds, points: [{ t, name, cpu }] }) that covers a moment, or null.
export function busiestAt(lookup, time) {
  const points = lookup?.points
  if (!points?.length) return null
  let low = 0
  let high = points.length - 1
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (points[middle].t <= time) low = middle
    else high = middle - 1
  }
  const found = points[low]
  return found.t <= time && time < found.t + lookup.bucketSeconds * 1000 ? found : null
}

function swapFinding({ swap, ram, swapTotalGb, containers }) {
  const known = swap.filter((point) => point.avg != null)
  if (known.length < 6) return null
  const last = known[known.length - 1].avg
  const lowest = Math.min(...known.map((point) => point.avg))
  if (last < 10 || last - lowest < 5) return null
  // Where the climb began: the last moment it was still within a point of its lowest.
  let began = known[0]
  for (const point of known) if (point.avg <= lowest + 1) began = point
  if (began === known[known.length - 1]) return null
  const ramPeak = Math.max(...ram.map((point) => point.max ?? point.avg ?? 0))
  const amount = (percent) => (swapTotalGb ? `${Number(((percent / 100) * swapTotalGb).toFixed(1))} GB` : formatPercent(percent))
  const holder = [...(containers || [])].filter((container) => container.swap_mb > 1).sort((a, b) => b.swap_mb - a.swap_mb)[0]
  const roomy = ramPeak < 80
  return {
    key: 'swap', weight: 3 + (last - lowest) / 20, at: began.t, metric: 'swap', note: 'started climbing',
    title: roomy ? 'Swap keeps growing while memory has room.' : 'Swap keeps growing.',
    text: `Swap went from ${amount(lowest)} to ${amount(last)} since ${formatMoment(began.t, { clock: false })}${roomy ? `, but RAM never passed ${formatPercent(ramPeak)}. Something is being paged out and not coming back.` : `, with RAM as high as ${formatPercent(ramPeak)}. The machine is short of memory.`}${holder ? ` ${holder.name} holds the most swap (${formatMegabytes(holder.swap_mb)}).` : ''}`,
  }
}

function spikeFinding({ cpu, busiest, bucketSeconds, spikes }) {
  if (bucketSeconds > 3600 || cpu.length < 12) return null
  const days = new Set(cpu.map((point) => Math.floor((point.t - new Date(point.t).getTimezoneOffset() * 60000) / DAY_MS)))
  if (days.size < 3) return null
  const byHour = new Map()
  for (const point of cpu) {
    if ((point.max ?? 0) < 90) continue
    const date = new Date(point.t)
    const entry = byHour.get(date.getHours()) || { days: new Set(), points: [] }
    entry.days.add(Math.floor((point.t - date.getTimezoneOffset() * 60000) / DAY_MS))
    entry.points.push(point)
    byHour.set(date.getHours(), entry)
  }
  const [hour, entry] = [...byHour.entries()].sort((a, b) => b[1].days.size - a[1].days.size)[0] || []
  if (entry === undefined || entry.days.size < 3 || entry.days.size < days.size * 0.6) return null
  const quiet = cpu.filter((point) => new Date(point.t).getHours() !== hour && point.avg != null)
  const rest = quiet.length ? quiet.reduce((sum, point) => sum + point.avg, 0) / quiet.length : null
  const top = Math.max(...entry.points.map((point) => point.max))
  const names = new Map()
  for (const point of entry.points) { const who = busiestAt(busiest, point.t); if (who) names.set(who.name, (names.get(who.name) || 0) + 1) }
  const culprit = [...names.entries()].sort((a, b) => b[1] - a[1])[0]
  const when = hour < 6 || hour >= 22 ? 'night' : 'day'
  const named = culprit && culprit[1] >= entry.points.length / 2 ? culprit[0] : null
  // How long each one lasted, from the check-ins themselves when the range still has them.
  const minutes = (spikes || []).filter((spike) => new Date(spike.t).getHours() === hour).map((spike) => Math.max(1, Math.round(spike.seconds / 60)))
  const shortest = Math.min(...minutes)
  const longest = Math.max(...minutes)
  const lasting = minutes.length ? `Spikes last ${shortest === longest ? `about ${longest}` : `${shortest} to ${longest}`} ${longest === 1 ? 'minute' : 'minutes'}${named ? ` and ${named} is the busiest container each time` : ''}.` : `Seen on ${entry.days.size} of ${days.size} days${named ? `, and ${named} is the busiest container each time` : ''}.`
  return {
    key: 'spike', weight: 2 + entry.days.size / 10, at: entry.points[entry.points.length - 1].t, metric: 'cpu',
    title: `CPU hits ${formatPercent(top)} at ${pad(hour)}:00 every ${when}.`,
    text: `${lasting}${rest != null ? ` Average CPU stays around ${formatPercent(rest)} the rest of the day.` : ''}`,
    // One mark per occurrence for the timeline's event row; what the job is, Infradeck can't know.
    marks: entry.points.map((point) => ({ t: point.t, kind: 'cpu_spike', group: 'cpu_spike', label: `${when === 'night' ? 'nightly' : 'daily'} ${named ? `${named} ` : ''}peak`, tone: 'plain', count: 1, detail: `CPU reached ${formatPercent(point.max)}` })),
  }
}

function gapFinding({ gaps, events, bucketSeconds }) {
  const longest = [...gaps].sort((a, b) => (b.to - b.from) - (a.to - a.from))[0]
  if (!longest || longest.to - longest.from < Math.max(10 * 60000, bucketSeconds * 2000)) return null
  const rebooted = (events || []).some((event) => event.kind === 'reboot' && new Date(event.time).getTime() >= longest.from && new Date(event.time).getTime() <= longest.to + 10 * 60000)
  const sameDay = new Date(longest.from).toDateString() === new Date(longest.to).toDateString()
  const others = gaps.length - 1
  return {
    key: 'gap', weight: 1 + Math.min(2, (longest.to - longest.from) / (6 * 3600000)), at: longest.from, metric: 'cpu',
    title: `No data for ${span(longest.to - longest.from)} on ${formatMoment(longest.from, { clock: false })}.`,
    text: `Nothing was reported from ${sameDay ? formatClock(longest.from) : formatMoment(longest.from)} to ${sameDay ? formatClock(longest.to) : formatMoment(longest.to)}. ${rebooted ? 'The machine rebooted in that time.' : 'No reboot was recorded, so the machine stayed up and the collector itself was down or could not reach Infradeck.'}${others > 0 ? ` There ${others === 1 ? 'is one more shorter gap' : `are ${others} more shorter gaps`} in this range.` : ''}`,
  }
}

function loadFinding({ load, cores }) {
  if (!cores || load.length < 6) return null
  const over = load.filter((point) => (point.avg ?? 0) > cores)
  const share = over.length / load.length
  if (share < 0.05) return null
  return {
    key: 'load', weight: 2 + share * 2, at: over[over.length - 1].t, metric: 'load',
    title: `Load is above the ${cores} cores ${formatPercent(share * 100)} of the time.`,
    text: `Work is queuing for the CPU in ${over.length} of ${load.length} periods, as high as ${Math.max(...over.map((point) => point.max ?? point.avg)).toFixed(2)}.`,
  }
}

function memoryFinding({ ram }) {
  const summary = summarize(ram)
  if (summary.peak == null || summary.peak < 90) return null
  return {
    key: 'memory', weight: 2.5 + (summary.peak - 90) / 10, at: summary.peakAt, metric: 'ram',
    title: `Memory reached ${formatPercent(summary.peak)}.`,
    text: `The peak was on ${formatMoment(summary.peakAt)}; the average over the range is ${formatPercent(summary.average)}. Above 90% the kernel starts reclaiming and swapping.`,
  }
}

function diskFinding({ disks }) {
  const soon = (disks || []).filter((disk) => disk.days != null && disk.days < 60).sort((a, b) => a.days - b.days)[0]
  if (!soon) return null
  return {
    key: 'disk', weight: 6 - soon.days / 60, at: null, metric: 'disk',
    title: `${soon.mount} fills up in about ${Math.max(1, Math.round(soon.days))} days.`,
    text: `It is ${formatPercent(soon.usedPct)} full and growing steadily over the last 30 days. The forecast is drawn in the disk charts below.`,
  }
}

// The two or three things that stand out in the selected range, most important first. Every sentence is worked out
// from the series passed in; nothing is guessed.
export function findings(input, limit = 3) {
  const found = [diskFinding(input), swapFinding(input), memoryFinding(input), spikeFinding(input), loadFinding(input), gapFinding(input)].filter(Boolean)
  return found.sort((a, b) => b.weight - a.weight).slice(0, limit)
}

export const LEVELS = [[0, 10], [10, 20], [20, 30], [30, 50], [50, 80], [80, 100]]

// How much of the time a percentage spent in each band, weighted by the check-ins behind every point, and one
// sentence that says what it means.
export function timeAtLevels(series, what = 'CPU') {
  const totals = LEVELS.map(() => 0)
  let all = 0
  for (const point of series) {
    if (point.avg == null) continue
    const weight = point.samples || 1
    const index = LEVELS.findIndex(([low, high], position) => point.avg >= low && (point.avg < high || position === LEVELS.length - 1))
    if (index >= 0) { totals[index] += weight; all += weight }
  }
  const bands = LEVELS.map(([low, high], index) => ({ label: `${low}–${high}%`, share: all ? (totals[index] / all) * 100 : 0, hot: low >= 80 }))
  if (!all) return { bands, sentence: '' }
  const whole = (value) => (value >= 10 ? `${Math.round(value)}%` : formatPercent(value))
  const under20 = bands[0].share + bands[1].share
  const over80 = bands[5].share
  const over50 = bands[4].share + over80
  let sentence
  if (over80 >= 10) sentence = `${whole(over80)} of the time the ${what} was above 80%. This machine is close to its limit.`
  else if (under20 >= 90) sentence = `${whole(under20)} of the time the ${what} was under 20%. There's headroom for more services on this machine.`
  else if (over50 >= 25) sentence = `${whole(over50)} of the time the ${what} was above 50%. It is well used, with some room left.`
  else sentence = `${whole(100 - over50)} of the time the ${what} was under 50%.`
  return { bands, sentence }
}

// The p-th percentile (0–100) of some numbers, by nearest rank; null for none.
export function percentile(values, p) {
  const sorted = values.filter((value) => value != null && Number.isFinite(value)).sort((a, b) => a - b)
  if (!sorted.length) return null
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))]
}

// The hour of the day that is busiest on average across the week, when it clearly stands out; else null.
export function busiestHour(grid) {
  const hours = Array.from({ length: 24 }, (_, hour) => { const values = grid.map((row) => row[hour]).filter((value) => value != null); return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null })
  const known = hours.filter((value) => value != null)
  if (known.length < 12) return null
  const top = Math.max(...known)
  const mean = known.reduce((sum, value) => sum + value, 0) / known.length
  return top >= mean * 1.8 && top - mean >= 3 ? { hour: hours.indexOf(top), value: top } : null
}

// Short marks for the timeline's event row, oldest first: repeats of the same kind within a few hours become one.
export function eventMarks(events, start, end, extra = []) {
  const marks = []
  const sorted = [...(events || [])].map((event) => ({ ...event, t: new Date(event.time).getTime() })).filter((event) => event.t >= start && event.t <= end).sort((a, b) => a.t - b.t)
  for (const event of sorted) {
    let label
    let tone = 'plain'
    const port = event.detail?.match(/(?:tcp|udp)\/(\d+)/)?.[1]
    if (event.kind === 'collector_stopped') { label = 'Collector offline'; tone = 'plain' } else if (event.kind === 'collector_resumed') continue
    else if (event.kind === 'alert_opened') { label = event.title; tone = event.severity === 'CRITICAL' ? 'signal' : event.severity === 'WARNING' ? 'warn' : 'plain' } else if (event.kind === 'alert_resolved') continue
    else if (event.kind === 'reboot') { label = /unclean/i.test(event.detail) ? 'Unclean reboot' : 'Reboot'; tone = /unclean/i.test(event.detail) ? 'warn' : 'plain' } else if (event.kind === 'oom_kill') { label = 'OOM kill'; tone = 'warn' }
    else if (event.kind === 'port_opened') { label = /loopback/.test(event.detail) ? `${port} opened` : `${port} exposed`; tone = /loopback/.test(event.detail) ? 'plain' : 'warn' } else if (event.kind === 'port_closed') label = `${port} closed`
    else if (event.kind === 'image_changed') label = 'Images updated'
    else if (event.kind === 'container_added') label = 'Container added'
    else if (event.kind === 'container_removed') label = 'Container removed'
    else if (event.kind === 'firewall_weakened') { label = 'Firewall weaker'; tone = 'warn' } else if (event.kind === 'firewall_changed') label = 'Firewall changed'
    else if (event.kind === 'kernel_error' || event.kind === 'kernel_fault') { label = 'Kernel error'; tone = 'warn' } else if (event.kind === 'packages_updated') { const count = event.detail?.match(/^(\d+) packages?/)?.[1]; const manager = event.detail?.match(/by (\w+)/)?.[1] || 'package'; label = `${manager} upgrade${count ? ` · ${count} packages` : ''}`; tone = /waiting for reboot/.test(event.detail || '') ? 'warn' : 'plain' } else continue
    const last = marks[marks.length - 1]
    // A port that closes and opens again within a few hours was re-bound, which is one thing to show, not many.
    const group = port && (event.kind === 'port_opened' || event.kind === 'port_closed') ? `port:${port}` : event.kind
    const same = last && last.group === group && (event.kind !== 'alert_opened' || last.label === label) && event.t - last.t < 6 * 3600000
    if (same) {
      last.count += 1
      if (group.startsWith('port:') && last.kind !== event.kind) { last.label = `${port} re-bound`; last.tone = 'plain' }
    } else marks.push({ t: event.t, kind: event.kind, group, label, tone, count: 1, detail: event.detail || event.title || '' })
  }
  return [...marks, ...extra.filter((mark) => mark.t >= start && mark.t <= end)].sort((a, b) => a.t - b.t)
}

// The timeline as CSV: one line per point, average and maximum of every metric.
export function timelineCsv(points, metrics) {
  const head = ['time', 'samples', ...metrics.flatMap((metric) => [`${metric}_avg`, `${metric}_max`])]
  const lines = [head.join(',')]
  for (const point of points) lines.push([point.time, point.samples, ...metrics.flatMap((metric) => [point[metric]?.avg ?? '', point[metric]?.max ?? ''])].join(','))
  return lines.join('\n')
}

// A dashed straight line from the latest disk reading to the day it is full: [{ t, value }], capped at ``horizon`` days.
export function projection(lastTime, lastValue, daysUntilFull, horizonDays = 3650) {
  if (lastValue == null || daysUntilFull == null || !Number.isFinite(daysUntilFull) || daysUntilFull <= 0) return null
  const days = Math.min(daysUntilFull, horizonDays)
  const value = lastValue + ((100 - lastValue) * days) / daysUntilFull
  return { from: { t: lastTime, value: lastValue }, to: { t: lastTime + days * DAY_MS, value }, full: days === daysUntilFull }
}
