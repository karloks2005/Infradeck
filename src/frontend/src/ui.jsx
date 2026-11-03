// Pieces the settings-style pages (Account, Admin) share: the two-column layout with a section list, cards,
// switches, notices and small helpers.
import { Children, Fragment, createContext, isValidElement, useContext, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PhoneHeader } from './shell.jsx'
import { apiRequest } from './api.js'
import { Icon } from './icons.jsx'

// The three layouts of the redesign: phone under 768 px (tab bar, one column), tablet up to 1199 px (icon rail),
// desktop from 1200 px (full sidebar).
export const PHONE_QUERY = '(max-width: 767px)'
export function useMedia(query) {
  const read = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
  const [matches, setMatches] = useState(read)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}
export const usePhone = () => useMedia(PHONE_QUERY)

export function useCopy() {
  const [copied, setCopied] = useState(false)
  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { setCopied(false) }
  }
  return [copied, copy]
}

// GET ``path`` once (and again on reload()): { data, error, reload, setData }.
export function useLoaded(path) {
  const [state, setState] = useState({ data: null, error: '' })
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!path) return undefined
    let active = true
    apiRequest(path, { method: 'GET' }).then((data) => { if (active) setState({ data, error: '' }) }).catch((error) => { if (active) setState((current) => ({ data: current.data, error: error.message || 'Could not load this.' })) })
    return () => { active = false }
  }, [path, version])
  return { ...state, reload: () => setVersion((value) => value + 1), setData: (data) => setState({ data, error: '' }) }
}

// Runs an async action and keeps its outcome: [{ busy, error, ok }, run].
export function useAction() {
  const [state, setState] = useState({ busy: false, error: '', ok: '' })
  const run = async (work, done = '') => {
    setState({ busy: true, error: '', ok: '' })
    try { const result = await work(); setState({ busy: false, error: '', ok: done }); return result } catch (error) { setState({ busy: false, error: error.message || 'Something went wrong.', ok: '' }); return undefined }
  }
  return [state, run]
}

export function Notice({ tone = 'warn', children }) {
  if (!children) return null
  return <div role={tone === 'warn' ? 'alert' : 'status'} className={`rounded-md border px-3.5 py-2.5 text-xs leading-5 ${tone === 'warn' ? 'border-warn/40 text-warn' : tone === 'ok' ? 'border-ok/40 text-ok' : 'border-line text-soft'}`}>{children}</div>
}

// The label on a button for a feature that isn't built yet; the button itself stays disabled.
export function Soon() {
  return <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-muted">Coming soon</span>
}

export function Outcome({ state }) {
  if (state.error) return <p role="alert" className="m-0 text-xs text-warn">{state.error}</p>
  if (state.ok) return <p role="status" className="m-0 text-xs text-ok">{state.ok}</p>
  return null
}

export function Switch({ checked, onChange, disabled = false, label }) {
  return (
    <label className={`relative inline-flex shrink-0 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <input type="checkbox" role="switch" className="peer sr-only" checked={Boolean(checked)} disabled={disabled} aria-label={label} onChange={(event) => onChange(event.target.checked)} />
      <span aria-hidden="true" className="relative h-[18px] w-8 rounded-full border border-edge bg-raised transition-colors after:absolute after:left-[2px] after:top-[2px] after:h-3 after:w-3 after:rounded-full after:bg-muted after:transition-all peer-checked:border-paper peer-checked:bg-paper peer-checked:after:left-[16px] peer-checked:after:bg-bg peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-paper" />
    </label>
  )
}

export function Check({ checked, onChange, disabled = false, label }) {
  return <input type="checkbox" className="auth-check disabled:opacity-40" checked={Boolean(checked)} disabled={disabled} aria-label={label} onChange={(event) => onChange(event.target.checked)} />
}

// A setting on one line: an icon box, a title with a line under it, and something to do on the right.
export function Row({ icon, title, detail, children, tone }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-[18px] py-3.5">
      {icon !== undefined && <span aria-hidden="true" className="mono flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-md border border-line bg-bg text-[11px] text-soft">{icon}</span>}
      <span className="min-w-0 flex-1 basis-56">
        <span className="block text-[13px] font-medium text-paper">{title}</span>
        {detail && <span className={`mt-0.5 block text-xs ${tone === 'warn' ? 'text-warn' : 'text-muted'}`}>{detail}</span>}
      </span>
      <span className="flex flex-wrap items-center gap-2.5">{children}</span>
    </div>
  )
}

// On a phone, a screen showing one section already has its name in the header.
const LoneSection = createContext(null)

export function Section({ id, title, description, children, aside }) {
  const lone = useContext(LoneSection) === id
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-20 border-b border-line pb-9 last:border-b-0 max-md:pb-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id={`${id}-title`} className={lone ? 'sr-only' : 'm-0 text-base font-semibold text-paper'}>{title}</h2>{description && <p className={`mb-0 max-w-[720px] text-[13px] leading-5 text-muted ${lone ? 'mt-0' : 'mt-1.5'}`}>{description}</p>}</div>
        {aside}
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  )
}

// The page frame: a title, the list of sections on the left (it follows you down the page), the sections on the right.
// On a phone the page is reached from the More tab: ``?section=<group>`` shows just that group's sections
// (``groups``: [{ id, label, sections }]), under a header that leads back to More.
// ``phoneHub``: what a phone shows with no group picked (Admin's list); ``phoneBack``: where a group's screen leads back to.
export function SettingsLayout({ title, description, aside, sections, footer, children, groups = [], phoneHub, phoneBack }) {
  const phone = usePhone()
  const [params] = useSearchParams()
  const group = groups.find((item) => item.id === params.get('section'))
  if (phone) return <PhoneSettings title={group?.label || title} description={group || phoneHub ? null : description} group={group} hub={group ? null : phoneHub} back={group && phoneBack ? phoneBack : { label: 'More', to: '/more' }}>{children}</PhoneSettings>
  return <DesktopSettings title={title} description={description} aside={aside} sections={sections} footer={footer} focus={group?.sections[0]}>{children}</DesktopSettings>
}

function PhoneSettings({ title, description, group, hub, back, children }) {
  // Only <Section>s outside the group are dropped; notices and loading text stay.
  const shown = Children.toArray(children).flatMap((child) => (isValidElement(child) && child.type === Fragment ? Children.toArray(child.props.children) : [child]))
    .filter((child) => !group || !isValidElement(child) || child.type !== Section || group.sections.includes(child.props.id))
  return (
    <LoneSection.Provider value={group?.sections.length === 1 ? group.sections[0] : null}>
      <PhoneHeader title={title} back={back} action={<span />} />
      <div className="space-y-7 px-4 pt-2">
        {description && <p className="m-0 text-[13px] text-soft">{description}</p>}
        {hub || shown}
      </div>
    </LoneSection.Provider>
  )
}

function DesktopSettings({ title, description, aside, sections, footer, focus, children }) {
  const [active, setActive] = useState(sections[0]?.id)
  // A link to one part of the page (from a phone's More tab, say) scrolls to it.
  useEffect(() => { if (focus) requestAnimationFrame(() => document.getElementById(focus)?.scrollIntoView({ block: 'start' })) }, [focus])
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
      if (visible) setActive(visible.target.id)
    }, { rootMargin: '-80px 0px -60% 0px' })
    for (const section of sections) { const element = document.getElementById(section.id); if (element) observer.observe(element) }
    return () => observer.disconnect()
    // The ids are fixed for a page; the counts next to them are not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sections.map((section) => section.id).join()])
  const go = (id) => { setActive(id); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
  return (
    <div className="px-3 pt-5 sm:px-4 lg:px-7 lg:pt-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><h1 className="m-0 text-[21px] font-semibold leading-7 text-paper">{title}</h1><p className="mb-0 mt-1.5 text-[13px] text-soft">{description}</p></div>
        {aside}
      </header>
      <div className="mt-7 grid items-start gap-x-10 gap-y-6 lg:grid-cols-[190px_minmax(0,1fr)]">
        <nav aria-label={`${title} sections`} className="no-scrollbar -mx-3 flex gap-1 overflow-x-auto px-3 lg:sticky lg:top-20 lg:mx-0 lg:block lg:space-y-0.5 lg:px-0">
          {sections.map((section) => (
            <button key={section.id} type="button" aria-current={active === section.id ? 'true' : undefined} onClick={() => go(section.id)} className={`flex h-[31px] shrink-0 items-center justify-between gap-3 rounded-md border-0 px-2.5 text-left text-[13px] font-medium lg:w-full ${active === section.id ? 'bg-raised text-paper' : 'bg-transparent text-soft hover:bg-ink hover:text-paper'}`}>
              {section.label}{section.count != null && <span className="text-xs font-normal text-muted">{section.count}</span>}
            </button>
          ))}
          {footer && <div className="hidden lg:mt-3 lg:block lg:border-t lg:border-line lg:pt-3">{footer}</div>}
        </nav>
        <div className="min-w-0 space-y-9">{children}</div>
      </div>
    </div>
  )
}

export function Field({ label, hint, children, className = '' }) {
  return (
    <label className={`block text-xs text-soft ${className}`}>
      <span className="mb-1.5 block">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-[11px] leading-4 text-muted">{hint}</span>}
    </label>
  )
}

// A secret that is shown once: the value, a copy button and what to do with it.
export function ShownOnce({ value, children }) {
  const [copied, copy] = useCopy()
  return (
    <div className="rounded-md border border-warn/40 px-3.5 py-3">
      <p className="m-0 text-xs text-warn">{children}</p>
      <div className="mt-2 flex items-center gap-3 rounded-md border border-line bg-bg py-1.5 pl-3 pr-1.5"><code className="mono min-w-0 flex-1 break-all text-paper">{value}</code><button type="button" className="btn btn-sm" onClick={() => copy(value)}><Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}</button></div>
    </div>
  )
}

// How long the history setting keeps something: 365 → "1 year", 0 → "forever".
export const keptFor = (days) => (days === 0 ? 'forever' : days === 365 ? '1 year' : `${Math.round(days / 365)} years`)

export const formatDate = (time) => new Date(time).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })

// "4 s ago", "2 h ago", "6 d ago", or the date once it is more than a month back.
export function ago(time) {
  if (!time) return 'never'
  const seconds = Math.max(0, (Date.now() - new Date(time).getTime()) / 1000)
  if (seconds < 45) return seconds < 5 ? 'now' : `${Math.round(seconds)} s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  if (seconds < 30 * 86400) return `${Math.round(seconds / 86400)} d ago`
  return formatDate(time)
}

// "Firefox 131 on Linux" from a User-Agent header, or the header itself when it isn't a browser we know.
export function describeBrowser(userAgent) {
  if (!userAgent) return 'Unknown browser'
  const browser = userAgent.match(/(Firefox|Edg|OPR|Chrome|Version)\/(\d+)/)
  const names = { Edg: 'Edge', OPR: 'Opera', Version: 'Safari' }
  const system = /iPhone/.test(userAgent) ? 'iPhone' : /iPad/.test(userAgent) ? 'iPad' : /Android/.test(userAgent) ? 'Android' : /Windows/.test(userAgent) ? 'Windows' : /Mac OS X/.test(userAgent) ? 'macOS' : /Linux/.test(userAgent) ? 'Linux' : null
  if (!browser) return userAgent.slice(0, 60)
  const name = browser[1] === 'Version' && !/Safari/.test(userAgent) ? 'Browser' : names[browser[1]] || browser[1]
  return `${name}${browser[1] === 'Version' && system === 'iPhone' ? '' : ` ${browser[2]}`}${system ? ` on ${system}` : ''}`
}

// Opening an overlay: focus goes in, Escape closes, the page behind stops scrolling, and focus comes back after.
function useOverlay(open, onClose) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!open) { setShown(false); return undefined }
    const previous = document.activeElement
    const frame = requestAnimationFrame(() => setShown(true))
    const onKeyDown = (event) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    document.body.style.overflow = 'hidden'
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = ''
      if (previous instanceof HTMLElement) previous.focus()
    }
    // onClose is a new function each render; the overlay only reacts to opening and closing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  return shown
}

// On a phone, details open as a sheet from the bottom over the list, so you keep your place. ``title`` may be
// a node (a name with a chip); ``label`` names the dialog for screen readers.
export function Sheet({ open, onClose, title, label, children, footer }) {
  const shown = useOverlay(open, onClose)
  if (!open) return null
  return (
    <div className={`fixed inset-0 z-50 flex items-end transition-colors duration-200 ${shown ? 'bg-black/55' : 'bg-black/0'}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label={label || (typeof title === 'string' ? title : undefined)} className={`flex max-h-[88vh] w-full flex-col rounded-t-2xl border-t border-edge bg-ink pb-[env(safe-area-inset-bottom)] transition-transform duration-200 ease-out motion-reduce:transition-none ${shown ? 'translate-y-0' : 'translate-y-full'}`}>
        <span aria-hidden="true" className="mx-auto mt-2 h-1 w-9 shrink-0 rounded-full bg-edge" />
        <header className="flex items-start justify-between gap-3 px-4 pb-2 pt-3">
          <div className="min-w-0 flex-1">{typeof title === 'string' ? <h2 className="m-0 text-[17px] font-semibold text-paper">{title}</h2> : title}</div>
          <button type="button" autoFocus onClick={onClose} aria-label="Close" className="-mr-1.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-muted hover:text-paper"><Icon name="close" size={15} /></button>
        </header>
        <div className="custom-scroll min-h-0 flex-1 overflow-y-auto px-4 pb-4">{children}</div>
        {footer && <div className="border-t border-line px-4 py-3">{footer}</div>}
      </div>
    </div>
  )
}

// A panel that slides in from the right over the page, like a drawer pulled out of the rack. Escape, the close
// button and a click outside all close it; focus moves in when it opens and back when it closes.
export function Drawer({ open, title, description, onClose, children, width = 560 }) {
  const shown = useOverlay(open, onClose)
  if (!open) return null
  return (
    <div className={`fixed inset-0 z-40 flex justify-end transition-colors duration-200 ${shown ? 'bg-black/55' : 'bg-black/0'}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label={title} className={`flex h-full w-full flex-col border-l-2 border-rail bg-bg shadow-[-24px_0_48px_rgba(0,0,0,0.35)] transition-transform duration-200 ease-out motion-reduce:transition-none ${shown ? 'translate-x-0' : 'translate-x-full'}`} style={{ maxWidth: width }}>
        <header className="flex items-start justify-between gap-4 border-b border-line px-6 pb-4 pt-5">
          <div><h2 className="m-0 text-[17px] font-semibold text-paper">{title}</h2>{description && <p className="mb-0 mt-1.5 text-[13px] leading-5 text-muted">{description}</p>}</div>
          <button type="button" autoFocus onClick={onClose} aria-label="Close" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-edge bg-ink text-soft hover:bg-raised hover:text-paper"><Icon name="close" size={14} /></button>
        </header>
        <div className="custom-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  )
}
