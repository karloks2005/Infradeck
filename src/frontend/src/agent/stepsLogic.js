// The agent's look-ups as the page shows them (#158). The backend sends each step's window as UTC ISO times; they are
// shown here in this browser's clock, 24 h, like the rest of the app. See docs/agent-investigation.md.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (value) => String(value).padStart(2, '0')
const NOW_SLACK_MS = 2 * 60 * 1000   // a window ending this close to now ends "now"

// The tools read a time without a zone as UTC (the model may send "2026-08-01T00:00:00"); so does this.
const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i

function parse(time) {
  const value = time ? Date.parse(HAS_ZONE.test(time) ? time : `${time}Z`) : NaN
  return Number.isFinite(value) ? new Date(value) : null
}

const clock = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`
const day = (date) => `${date.getDate()} ${MONTHS[date.getMonth()]}`
const sameDay = (one, two) => one.getFullYear() === two.getFullYear() && one.getMonth() === two.getMonth() && one.getDate() === two.getDate()

// "3 Nov 10:00–12:00", "1 Nov 00:00 – now", "at 7 Nov 12:00", or '' when the step has no window.
export function stepWindow(step, now = Date.now()) {
  const from = parse(step?.from)
  const to = parse(step?.to)
  const at = parse(step?.at)
  if (from && to) {
    if (Math.abs(now - to.getTime()) <= NOW_SLACK_MS) return `${day(from)} ${clock(from)} – now`
    return sameDay(from, to) ? `${day(from)} ${clock(from)}–${clock(to)}` : `${day(from)} ${clock(from)} – ${day(to)} ${clock(to)}`
  }
  if (at) return Math.abs(now - at.getTime()) <= NOW_SLACK_MS ? 'now' : `at ${day(at)} ${clock(at)}`
  return ''
}

// "1 look-up", "3 look-ups"; only calls that actually ran count, so a refused one doesn't inflate the number.
export function lookupCount(steps) {
  const ran = (steps || []).filter((step) => step.status !== 'refused').length
  return `${ran} ${ran === 1 ? 'look-up' : 'look-ups'}`
}
