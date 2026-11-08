import { Caret, Icon } from '../icons.jsx'
import { lookupCount, stepWindow } from './stepsLogic.js'

// The agent's look-ups behind a diagnosis or a chat reply (#158), folded away by default. Each step shows the
// question the agent gave for the call (its own words, written before it saw the result), what it looked up, and
// one line of facts from the result, written by Infradeck's code, not by the model. See docs/agent-investigation.md.
const STATUS = {
  ok: ['check', 'text-ok', 'Looked up'],
  error: ['close', 'text-warn', 'Failed'],
  refused: ['close', 'text-muted', 'Not run'],
}

export function Steps({ steps, title, className = '' }) {
  if (!steps?.length) return null
  return (
    <details className={`group ${className}`}>
      <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-muted hover:text-soft">
        <Caret open={false} className="transition-transform group-open:rotate-90" />
        {title || 'How the agent got there'} · {lookupCount(steps)}
      </summary>
      <ol className="m-0 mt-2 list-none space-y-2.5 border-l border-line p-0 pl-3.5">
        {steps.map((step, index) => {
          const [icon, tone, label] = STATUS[step.status] || STATUS.error
          const window = stepWindow(step)
          return (
            <li key={index} className="text-xs leading-[18px]">
              {step.reason && <span className="block text-soft">“{step.reason}”</span>}
              <span className="block text-muted">{step.asked}{window ? ` · ${window}` : ''}</span>
              <span className="mt-0.5 flex items-start gap-1.5 text-paper"><Icon name={icon} size={12} className={`mt-[3px] shrink-0 ${tone}`} /><span className="sr-only">{label}: </span><span className="min-w-0">{step.found}</span></span>
            </li>
          )
        })}
      </ol>
      <p className="mb-0 mt-2 text-[11px] leading-4 text-dim">The questions are the agent's own; what it looked up and found is taken from Infradeck's data.</p>
    </details>
  )
}
