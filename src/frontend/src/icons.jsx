// Line icons, 16px, drawn in the current text colour.
const base = { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }

const paths = {
  overview: <><rect x="2.5" y="2.5" width="11" height="4" rx="0.8" /><rect x="2.5" y="9.5" width="11" height="4" rx="0.8" /><path d="M5 4.5h.01M5 11.5h.01" /></>,
  statistics: <><path d="M2.5 13.5h11" /><path d="M3 10l3-3.5 2.5 2L13 4" /></>,
  availability: <path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4" />,
  agent: <><path d="M2.5 3.5h11v7.5H7l-3 2.5V11H2.5z" /><path d="M5.5 6.5h5M5.5 8.5h3" /></>,
  account: <><circle cx="8" cy="5.5" r="2.5" /><path d="M3 13.5c.6-2.3 2.5-3.5 5-3.5s4.4 1.2 5 3.5" /></>,
  admin: <><path d="M2.5 5h7M12.5 5h1M2.5 11h1M6.5 11h7" /><circle cx="11" cy="5" r="1.5" /><circle cx="5" cy="11" r="1.5" /></>,
  signout: <><path d="M9.5 2.5h3.5v11H9.5" /><path d="M9 8H3M5.5 5.5L3 8l2.5 2.5" /></>,
  search: <><circle cx="7" cy="7" r="4" /><path d="M10 10l3.5 3.5" /></>,
  plus: <path d="M8 3v10M3 8h10" />,
  check: <path d="M3 8.5l3.2 3L13 4.5" />,
  logs: <path d="M3 4h10M3 8h7M3 12h9" />,
  restart: <><path d="M13 8a5 5 0 1 1-1.6-3.7" /><path d="M13 2.5v3h-3" /></>,
  download: <><path d="M8 2.5v8M5 7.5l3 3 3-3" /><path d="M3 13.5h10" /></>,
  copy: <><rect x="5.5" y="5.5" width="8" height="8" rx="1" /><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" /></>,
  arrow: <path d="M3 8h10M9.5 4.5L13 8l-3.5 3.5" />,
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  more: <path d="M3.5 8h.01M8 8h.01M12.5 8h.01" strokeWidth="2.4" />,
  bell: <><path d="M4 11V7.5a4 4 0 0 1 8 0V11l1 1.5H3z" /><path d="M6.5 14h3" /></>,
  lock: <><rect x="3.5" y="7" width="9" height="6.5" rx="1" /><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" /></>,
  key: <><circle cx="5.5" cy="10.5" r="2.5" /><path d="M7.3 8.7L13 3M11 5l1.5 1.5" /></>,
  eye: <><path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8s-2.4 4.5-6.5 4.5S1.5 8 1.5 8z" /><circle cx="8" cy="8" r="2" /></>,
  share: <><path d="M9.5 2.5h4v4M13.5 2.5L8 8" /><path d="M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3" /></>,
  chevron: <path d="M6 3.5L10.5 8 6 12.5" />,
}

export function Icon({ name, size = 16, className = '' }) {
  return <svg {...base} width={size} height={size} className={className}>{paths[name]}</svg>
}

// The disclosure triangle in front of a group or an expandable row.
export function Caret({ open, className = '' }) {
  return <svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true" className={`shrink-0 fill-current transition-transform ${open ? 'rotate-90' : ''} ${className}`}><path d="M2 0.8l5 3.7-5 3.7z" /></svg>
}
