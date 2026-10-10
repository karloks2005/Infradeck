// Tests for the pure chart helpers. Run with `node --test` in this folder (Node 20 or newer, no packages needed).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { daysUntilFull, donutSlices, formatTime, histogramBins, hourWeekdayGrid, niceTicks, seriesFor, splitRuns, summarise } from '../src/chartMath.js'

const DAY = 86400000

test('histogram: values land in the right bin, weights count, and out-of-range values are left out', () => {
  const bins = histogramBins([0, 5, 10, 99, 100, 100.1, -1, NaN, { value: 55, weight: 3 }])
  assert.equal(bins.length, 10)
  assert.deepEqual([bins[0].count, bins[1].count, bins[5].count, bins[9].count], [2, 1, 3, 2])   // 100 belongs in the last bin
  assert.equal(histogramBins([1], { low: 0, high: 1, bins: 4 })[3].count, 1)
  assert.equal(histogramBins([]).every((bin) => bin.count === 0), true)
})

test('hour-by-weekday grid: weighted average per cell, Monday first, empty cells stay null', () => {
  const monday = Date.UTC(2026, 2, 2, 10, 0)
  const grid = hourWeekdayGrid([
    { t: monday, avg: 40, samples: 1 },
    { t: monday + 60000, avg: 80, samples: 3 },
    { t: Date.UTC(2026, 2, 8, 23, 30), avg: 5, samples: 1 },       // Sunday 23:30
    { t: monday, avg: null, samples: 9 },                           // unknown is not zero
  ], true)
  assert.equal(grid.length, 7)
  assert.equal(grid[0].length, 24)
  assert.equal(grid[0][10], 70)
  assert.equal(grid[6][23], 5)
  assert.equal(grid[3][3], null)
})

test('donut: invalid and zero slices are dropped and fractions add up', () => {
  const slices = donutSlices([{ label: 'a', value: 3 }, { label: 'b', value: 0 }, { label: 'c', value: 1 }, { label: 'x', value: NaN }, { label: 'n', value: -2 }])
  assert.deepEqual(slices.map((slice) => [slice.label, slice.fraction, slice.start]), [['a', 0.75, 0], ['c', 0.25, 0.75]])
  assert.deepEqual(donutSlices([]), [])
})

test('days until full: a clear rising trend gives an estimate, anything unclear gives none', () => {
  const rising = Array.from({ length: 10 }, (_, index) => ({ t: index * DAY, value: 50 + index * 2 }))   // 68 now, +2 a day
  assert.ok(Math.abs(daysUntilFull(rising) - 16) < 1e-6)
  assert.equal(daysUntilFull(rising.slice(0, 3)), null)                                                 // too few points
  assert.equal(daysUntilFull(rising.map((point) => ({ ...point, value: 50 }))), null)                    // flat
  assert.equal(daysUntilFull(rising.map((point) => ({ ...point, value: 90 - point.value / 10 }))), null) // falling
  assert.equal(daysUntilFull(rising.map((point) => ({ t: point.t / 100, value: point.value }))), null)   // under a day of data
  assert.equal(daysUntilFull(rising.map((point, index) => ({ ...point, value: 50 + (index % 2) * 40 }))), null)   // too noisy
  assert.equal(daysUntilFull(rising.map((point) => ({ ...point, value: 95 + (point.t / DAY) * 0.6 }))), 0)        // already full
  assert.equal(daysUntilFull([]), null)
})

test('axis ticks reach the top of the data, and bad input does not break them', () => {
  assert.deepEqual(niceTicks(0, 100, 4), [0, 25, 50, 75, 100])
  assert.deepEqual(niceTicks(0, 1024, 4), [0, 500, 1000, 1500])
  assert.deepEqual(niceTicks(NaN, 1), [0, 1])
  assert.ok(niceTicks(5, 5).length >= 2)
})

test('a hole of more than a bucket and a half splits a series, so a gap stays a gap', () => {
  assert.equal(splitRuns([{ t: 0 }, { t: 60000 }, { t: 400000 }], 60).length, 2)
  assert.equal(splitRuns([{ t: 0 }, { t: 60000 }, { t: 120000 }], 60).length, 1)
  assert.deepEqual(splitRuns([], 60), [])
})

test('series and summary: unknown values are skipped, the average is weighted and the maximum is kept', () => {
  const points = [
    { time: '2026-03-02T10:00:00+00:00', samples: 1, cpu: { avg: 100, max: 100 } },
    { time: '2026-03-02T11:00:00+00:00', samples: 3, cpu: { avg: 0, max: 40 } },
    { time: '2026-03-02T12:00:00+00:00', samples: 5, cpu: null },
  ]
  const series = seriesFor(points, 'cpu')
  assert.equal(series.length, 2)
  assert.deepEqual(summarise(series), { average: 25, maximum: 100 })
  assert.deepEqual(summarise([]), { average: null, maximum: null })
})

test('time labels follow the span of the chart', () => {
  const moment = Date.UTC(2026, 2, 5, 9, 7)
  assert.equal(formatTime(moment, 3600, true), '09:07')
  assert.equal(formatTime(moment, 3 * 86400, true), '5 Mar 09:07')
  assert.equal(formatTime(moment, 90 * 86400, true), '5 Mar')
  assert.equal(formatTime(moment, 800 * 86400, true), 'Mar 2026')
})

test('forecast wording: honest about missing trends and sensible at every scale', async () => {
  const { formatForecast } = await import('../src/chartMath.js')
  assert.match(formatForecast(null), /No clear growth trend/)
  assert.match(formatForecast(NaN), /No clear growth trend/)
  assert.equal(formatForecast(0), 'Full now.')
  assert.match(formatForecast(0.4), /less than a day/)
  assert.equal(formatForecast(1), 'Full in about 1 day at this rate.')
  assert.equal(formatForecast(16.2), 'Full in about 16 days at this rate.')
  assert.equal(formatForecast(120), 'Full in about 4 months at this rate.')
  assert.equal(formatForecast(1000), 'Full in about 2.7 years at this rate.')
  assert.match(formatForecast(9999), /Not full within ten years/)
})
