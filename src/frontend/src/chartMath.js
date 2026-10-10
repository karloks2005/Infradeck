// Pure helpers for the charts: ticks, labels, gaps and summary figures. No React in here.

const pad = (value) => String(value).padStart(2, '0')

export function niceTicks(low, high, wanted = 5) {
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1]
  if (high <= low) high = low + 1
  const rough = (high - low) / Math.max(wanted, 1)
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= rough) || 10 * magnitude
  const ticks = []
  // The last tick is at or above the top of the data, so nothing is clipped against the edge.
  const last = Math.ceil(high / step - 1e-9) * step
  for (let value = Math.ceil(low / step) * step; value <= last + step * 1e-9; value += step) ticks.push(Number(value.toFixed(10)))
  return ticks
}

// Day, week and month points are UTC-aligned, so they are labelled in UTC; shorter ranges use the viewer's clock.
export function formatTime(milliseconds, spanSeconds, utc) {
  const date = new Date(milliseconds)
  const get = utc
    ? { year: date.getUTCFullYear(), month: date.getUTCMonth(), day: date.getUTCDate(), hour: date.getUTCHours(), minute: date.getUTCMinutes() }
    : { year: date.getFullYear(), month: date.getMonth(), day: date.getDate(), hour: date.getHours(), minute: date.getMinutes() }
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][get.month]
  if (spanSeconds <= 18 * 3600) return `${pad(get.hour)}:${pad(get.minute)}`
  if (spanSeconds <= 14 * 86400) return `${get.day} ${month} ${pad(get.hour)}:${pad(get.minute)}`
  if (spanSeconds <= 400 * 86400) return `${get.day} ${month}`
  return `${month} ${get.year}`
}

export function formatDateTime(milliseconds, utc) {
  const date = new Date(milliseconds)
  const pick = (local, universal) => (utc ? universal : local)
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][pick(date.getMonth(), date.getUTCMonth())]
  const day = `${pick(date.getDate(), date.getUTCDate())} ${month} ${pick(date.getFullYear(), date.getUTCFullYear())}`
  return utc ? day : `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function describeBucket(seconds) {
  if (seconds >= 28 * 86400) return 'month'
  if (seconds >= 7 * 86400) return 'week'
  if (seconds >= 86400) return seconds === 86400 ? 'day' : `${Math.round(seconds / 86400)} days`
  if (seconds >= 3600) return seconds === 3600 ? 'hour' : `${Math.round(seconds / 3600)} hours`
  if (seconds >= 60) return seconds === 60 ? 'minute' : `${Math.round(seconds / 60)} minutes`
  return `${seconds} seconds`
}

// Points become runs of neighbours; a hole bigger than a bucket and a half starts a new run, so a gap stays a gap.
export function splitRuns(points, bucketSeconds) {
  const runs = []
  let current = []
  let previous = null
  for (const point of points) {
    if (previous !== null && point.t - previous > bucketSeconds * 1500) {
      runs.push(current)
      current = []
    }
    current.push(point)
    previous = point.t
  }
  if (current.length) runs.push(current)
  return runs
}

// Turns the API's points into {t, avg, max, samples} for one metric, skipping points where it is unknown.
export function seriesFor(points, metric) {
  return (points || [])
    .map((point) => ({ t: new Date(point.time).getTime(), avg: point[metric]?.avg ?? null, max: point[metric]?.max ?? null, samples: point.samples }))
    .filter((point) => Number.isFinite(point.t) && (point.avg !== null || point.max !== null))
}

// Weighted average and highest maximum of a series, for the summary figures.
export function summarise(series) {
  let total = 0
  let weight = 0
  let maximum = null
  for (const point of series) {
    if (point.avg !== null) { total += point.avg * point.samples; weight += point.samples }
    if (point.max !== null && (maximum === null || point.max > maximum)) maximum = point.max
  }
  return { average: weight ? total / weight : null, maximum }
}


// Counts of values per bin, between ``low`` and ``high``; each value may carry a weight (for example check-ins).
// A value equal to ``high`` goes in the last bin, and values outside the range are left out.
export function histogramBins(values, { low = 0, high = 100, bins = 10 } = {}) {
  const width = (high - low) / bins
  const counts = Array.from({ length: bins }, (_, index) => ({ from: low + index * width, to: low + (index + 1) * width, count: 0 }))
  for (const entry of values) {
    const value = typeof entry === 'number' ? entry : entry.value
    const weight = typeof entry === 'number' ? 1 : entry.weight ?? 1
    if (!Number.isFinite(value) || value < low || value > high) continue
    counts[Math.min(Math.floor((value - low) / width), bins - 1)].count += weight
  }
  return counts
}

// The average of a metric for each hour of the day (0-23) and weekday (0 = Monday ... 6 = Sunday), in the viewer's
// time zone or in UTC. Cells nobody reported stay null. Points are {t, avg, samples}.
export function hourWeekdayGrid(points, utc = false) {
  const sums = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => ({ total: 0, weight: 0 })))
  for (const point of points) {
    if (point.avg === null || point.avg === undefined) continue
    const date = new Date(point.t)
    const weekday = ((utc ? date.getUTCDay() : date.getDay()) + 6) % 7
    const cell = sums[weekday][utc ? date.getUTCHours() : date.getHours()]
    cell.total += point.avg * point.samples
    cell.weight += point.samples
  }
  return sums.map((row) => row.map((cell) => (cell.weight ? cell.total / cell.weight : null)))
}

// Slices as fractions of their total, for a donut; zero and invalid values are dropped.
export function donutSlices(slices) {
  const valid = slices.filter((slice) => Number.isFinite(slice.value) && slice.value > 0)
  const total = valid.reduce((sum, slice) => sum + slice.value, 0)
  let start = 0
  return valid.map((slice) => {
    const fraction = slice.value / total
    const result = { ...slice, fraction, start }
    start += fraction
    return result
  })
}

// Days until a straight-line trend through (time, value) points reaches ``limit``, or null when the trend is flat,
// falling, too short or too noisy to say. Times are milliseconds.
export function daysUntilFull(points, limit = 100, { minPoints = 6, minSpanDays = 1, minFit = 0.5 } = {}) {
  const usable = points.filter((point) => Number.isFinite(point.t) && Number.isFinite(point.value))
  if (usable.length < minPoints) return null
  const spanDays = (usable[usable.length - 1].t - usable[0].t) / 86400000
  if (spanDays < minSpanDays) return null
  const n = usable.length
  const meanT = usable.reduce((sum, point) => sum + point.t, 0) / n
  const meanV = usable.reduce((sum, point) => sum + point.value, 0) / n
  let covariance = 0
  let varianceT = 0
  let varianceV = 0
  for (const point of usable) {
    covariance += (point.t - meanT) * (point.value - meanV)
    varianceT += (point.t - meanT) ** 2
    varianceV += (point.value - meanV) ** 2
  }
  if (varianceT === 0 || varianceV === 0) return null
  const slope = covariance / varianceT                       // per millisecond
  const fit = (covariance * covariance) / (varianceT * varianceV)   // r squared
  if (slope <= 0 || fit < minFit) return null
  const current = usable[usable.length - 1].value
  if (current >= limit) return 0
  return (limit - current) / slope / 86400000
}

// "Full in about 3 weeks" for a forecast in days, or an honest "no clear trend" when there is none.
export function formatForecast(days) {
  if (days === null || days === undefined || !Number.isFinite(days)) return 'No clear growth trend in this range, so no estimate.'
  if (days <= 0) return 'Full now.'
  if (days < 1) return 'Full in less than a day at this rate.'
  if (days < 60) return `Full in about ${Math.round(days)} ${Math.round(days) === 1 ? 'day' : 'days'} at this rate.`
  if (days < 730) return `Full in about ${Math.round(days / 30)} months at this rate.`
  if (days < 3650) return `Full in about ${(days / 365).toFixed(1)} years at this rate.`
  return 'Not full within ten years at this rate.'
}
