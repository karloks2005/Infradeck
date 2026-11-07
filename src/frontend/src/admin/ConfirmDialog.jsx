// "Confirm it's you" before a change that sends data from the servers to an AI provider (design/mobile/11-agent-confirm.pdf,
// #184). Step 1 checks the password on the server and gets a short-lived token; step 2 sends the token and a
// two-factor code with the change itself (``onConfirm``). Cancel, ✕, Escape or a click outside leave things as they were.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '../api.js'
import { Icon } from '../icons.jsx'
import { CODE_LENGTH, CodeBoxes, useOverlay } from '../ui.jsx'

const initials = (name) => { const parts = (name || '?').trim().split(/\s+/); return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2)).toUpperCase() }
const quietLink = 'font-medium text-paper underline decoration-muted underline-offset-4 hover:decoration-paper'

// What is about to change: the setting, its old and new value, and where the data will go.
function Change({ change }) {
  return (
    <div className="rounded-md border border-line bg-bg px-3.5 py-3">
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="min-w-0 text-paper">{change.title}</span>
        <span className="flex shrink-0 items-center gap-2 text-muted">{change.from}<span aria-hidden="true">→</span><span className="sr-only">to</span><span className="font-semibold text-paper">{change.to}</span></span>
      </div>
      <p className="mb-0 mt-1.5 text-xs text-muted">{change.detail} <span className="whitespace-nowrap">{change.target}</span></p>
    </div>
  )
}

function Steps({ step, icon, title, onClose }) {
  return (
    <header className="flex items-start gap-3 px-5 pb-1 pt-5">
      <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-edge bg-bg text-paper"><Icon name={icon} size={15} /></span>
      <div className="min-w-0 flex-1">
        <p className="m-0 flex items-center gap-2.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted">Step {step} of 2
          <span aria-hidden="true" className="flex gap-1">{[1, 2].map((index) => <span key={index} className={`h-[3px] w-4 rounded-full ${index <= step ? 'bg-paper' : 'bg-edge'}`} />)}</span>
        </p>
        <h2 className="m-0 mt-1 text-[17px] font-semibold text-paper">{title}</h2>
      </div>
      <button type="button" onClick={onClose} aria-label="Close" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-edge bg-ink text-soft hover:bg-raised hover:text-paper"><Icon name="close" size={14} /></button>
    </header>
  )
}

// How long the popup takes to fade and scale away before it is removed.
const LEAVE_MS = 160

export default function ConfirmDialog({ change, user, onConfirm, onClose }) {
  const [leaving, setLeaving] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  const close = () => { if (leaving) return; setLeaving(true); timer.current = setTimeout(onClose, LEAVE_MS) }
  const shown = useOverlay(Boolean(change), close) && !leaving
  const [step, setStep] = useState(1)
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [token, setToken] = useState('')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [shake, setShake] = useState(0)
  const passwordField = useRef(null)
  // The click that opened the popup ends by focusing the switch, so the password field takes focus a frame later.
  useEffect(() => {
    if (step !== 1) return undefined
    const frame = requestAnimationFrame(() => passwordField.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [step])
  if (!change) return null

  const checkPassword = async (event) => {
    event.preventDefault()
    if (busy || !password) return
    setBusy(true); setError('')
    try {
      const result = await apiRequest('/auth/reauth', { method: 'POST', body: JSON.stringify({ password }) })
      setToken(result.reauth_token); setPassword(''); setStep(2)
    } catch (requestError) { setError(requestError.message || 'The password is not correct.') }
    setBusy(false)
  }
  const verify = async (value) => {
    if (busy || !value.trim()) return
    setBusy(true); setError('')
    try {
      await onConfirm({ reauth_token: token, code: value.trim() })
      close()
    } catch (requestError) {
      const message = requestError.message || 'That code is not valid.'
      setBusy(false); setCode('')
      if (/start again/i.test(message)) { setStep(1); setToken(''); setError(message); return }
      setError(message); setShake((count) => count + 1)
    }
  }
  const submitCode = (event) => {
    event.preventDefault()
    if (!recovery && code.length < CODE_LENGTH) setError('Type all six digits of the code.')
    else verify(code)
  }
  const back = () => { setStep(1); setToken(''); setCode(''); setError('') }

  return (
    <div className={`fixed inset-0 z-50 flex items-center justify-center px-4 transition-colors duration-200 ${shown ? 'bg-black/55' : 'bg-black/0'}`} onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
      <div role="dialog" aria-modal="true" aria-label={step === 1 ? "Confirm it's you" : 'Enter your 2FA code'} className={`relative w-full max-w-[440px] rounded-lg border border-edge bg-ink shadow-[0_24px_48px_rgba(0,0,0,0.45)] transition duration-200 ease-out motion-reduce:transition-none ${shown ? 'translate-y-0 scale-100 opacity-100' : 'translate-y-2 scale-95 opacity-0'}`}>
        <span aria-hidden="true" className="pointer-events-none absolute -left-[2px] bottom-[18px] top-[18px] w-[3px] rounded-full bg-signal" />
        {step === 1 ? (
          <form key="password" className="dialog-step" onSubmit={checkPassword}>
            <Steps step={1} icon="lock" title="Confirm it's you" onClose={close} />
            <div className="flex flex-col gap-4 px-5 pb-5 pt-3">
              <p className="m-0 text-[13px] leading-5 text-soft">{change.intro} Enter your password to continue.</p>
              <Change change={change} />
              {user.two_factor_enabled === false
                ? <p role="alert" className="m-0 text-xs leading-5 text-warn">This needs two-factor sign-in. Set it up in <Link to="/account?section=security" className="underline underline-offset-4">Account › Security</Link> first.</p>
                : <>
                  <p className="m-0 flex items-center gap-2.5 text-[13px]">
                    <span aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-[10px] font-semibold text-paper">{initials(user.name)}</span>
                    <span className="min-w-0 truncate"><span className="font-medium text-paper">{user.name}</span> <span className="text-muted">· {user.email}</span></span>
                  </p>
                  <label className="block text-xs text-soft"><span className="mb-1.5 block">Password</span>
                    <span className="relative block">
                      <input ref={passwordField} type={reveal ? 'text' : 'password'} autoComplete="current-password" maxLength={1024} value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} className="field h-11 w-full pr-16" />
                      <button type="button" onClick={() => setReveal((value) => !value)} className="absolute inset-y-0 right-3 text-xs text-muted hover:text-paper">{reveal ? 'Hide' : 'Show'}</button>
                    </span>
                  </label>
                </>}
              {error && <p role="alert" className="m-0 text-xs text-warn">{error}</p>}
            </div>
            <footer className="flex justify-end gap-2.5 border-t border-line px-5 py-3.5">
              <button type="button" className="btn" onClick={close}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={busy || !password || user.two_factor_enabled === false}>{busy ? 'Checking…' : 'Continue'}</button>
            </footer>
          </form>
        ) : (
          <form key="code" className="dialog-step" onSubmit={submitCode}>
            <Steps step={2} icon="key" title="Enter your 2FA code" onClose={close} />
            <div className="flex flex-col gap-4 px-5 pb-5 pt-3">
              <p className="m-0 text-[13px] leading-5 text-soft">Password accepted. {recovery ? 'Type one of the recovery codes you saved when you turned two-factor on.' : <>Open your authenticator app and type the 6-digit code for <span className="font-medium text-paper">{user.email}</span>.</>}</p>
              <div key={shake} className={shake ? 'shake' : undefined}>
                {recovery
                  ? <input autoFocus value={code} onChange={(event) => setCode(event.target.value)} maxLength={32} autoComplete="off" autoCapitalize="none" spellCheck={false} aria-label="Recovery code" placeholder="xxxx-xxxx-xxxx" className="field mono h-11 w-full tracking-wider" />
                  : <CodeBoxes value={code} onChange={setCode} onComplete={verify} disabled={busy} />}
              </div>
              {error && <p role="alert" className="m-0 text-xs text-warn">{error}</p>}
              <p className="m-0 text-xs text-muted">{recovery ? 'Have your phone after all? ' : 'Lost your phone? '}<button type="button" className={quietLink} onClick={() => { setRecovery((mode) => !mode); setCode(''); setError('') }}>{recovery ? 'Use the 6-digit code' : 'Use a recovery code'}</button></p>
              <Change change={change} />
            </div>
            <footer className="flex items-center gap-2.5 border-t border-line px-5 py-3.5">
              <button type="button" className="mr-auto text-xs text-muted underline decoration-edge underline-offset-4 hover:text-paper" onClick={back}>Back</button>
              <button type="button" className="btn" onClick={close}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={busy}><Icon name="check" size={13} />{busy ? 'Checking…' : change.action}</button>
            </footer>
          </form>
        )}
      </div>
    </div>
  )
}
