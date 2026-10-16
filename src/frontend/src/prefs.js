// How this browser shows Infradeck: theme and density apply at once and are remembered here, so the page doesn't
// flash the wrong theme while the account loads. The account itself keeps the same choices on the server.
const KEY = 'infradeck.display'

export function storedDisplay() {
  try { return JSON.parse(window.localStorage.getItem(KEY)) || {} } catch { return {} }
}

export function applyDisplay(display) {
  const choice = { ...storedDisplay(), ...(display || {}) }
  const system = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  document.documentElement.dataset.theme = choice.theme === 'system' ? system : choice.theme === 'light' ? 'light' : 'dark'
  document.documentElement.dataset.density = choice.density === 'compact' ? 'compact' : 'comfortable'
  if (display) { try { window.localStorage.setItem(KEY, JSON.stringify(choice)) } catch { /* not remembered */ } }
  return choice
}

// How often the Overview refreshes for this person, in seconds: their choice, or the default built into the frontend.
export function liveRefreshSeconds(fallback) {
  const chosen = Number(storedDisplay().live_refresh)
  return [2, 3, 5, 10, 30, 60].includes(chosen) ? chosen : fallback
}

// Where "/" should lead for this person: the page they chose to open on.
export function landingPath() {
  return { statistics: '/statistics', agent: '/agent' }[storedDisplay().open_on] || '/'
}
