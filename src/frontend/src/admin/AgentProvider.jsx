// Which AI provider and model the incident agent uses, and its API key. The key is sent once and stored encrypted;
// the backend never sends it back, so the field only ever shows whether one is saved. Saving decides where the
// agent's data goes, so it is confirmed with the password and a two-factor code first (#184).
import { useState } from 'react'
import { apiRequest } from '../api.js'
import { Field, Outcome, Row, useAction } from '../ui.jsx'
import ConfirmDialog from './ConfirmDialog.jsx'

export default function AgentProvider({ agent, user, secretsConfigured, onChanged }) {
  const providers = agent.providers
  const defaultModel = (id) => providers.find((item) => item.id === id)?.default_model || ''
  const [provider, setProvider] = useState(agent.provider || providers[0].id)
  const [model, setModel] = useState(agent.model || defaultModel(provider))
  const [apiKey, setApiKey] = useState('')
  const [state, run] = useAction()
  const [test, runTestAction] = useAction()
  const [tested, setTested] = useState(null)
  const [confirming, setConfirming] = useState(null)
  const chosen = providers.find((item) => item.id === agent.provider)
  const switching = Boolean(agent.provider) && provider !== agent.provider

  const choose = (id) => { setProvider(id); setModel(defaultModel(id)) }
  const save = (event) => {
    event.preventDefault()
    setTested(null)
    const name = providers.find((item) => item.id === provider).name
    const newModel = model.trim() || defaultModel(provider)
    const [from, to] = provider !== agent.provider ? [chosen?.name || 'None', name]
      : newModel !== agent.model ? [agent.model, newModel] : [agent.key_configured ? 'Saved key' : 'No key', 'New key']
    setConfirming({
      body: { provider, model: model.trim() || null, api_key: apiKey.trim() || null },
      change: { title: 'Model provider', from, to, detail: 'Incident evidence and chat questions will be sent to', target: `${name} · ${newModel}`,
        intro: 'The provider decides where Infradeck sends data from your servers.', action: 'Verify and save' },
    })
  }
  const confirm = async (fields) => {
    await apiRequest('/admin/agent/provider', { method: 'PUT', body: JSON.stringify({ ...confirming.body, ...fields }) })
    setApiKey('')
    await run(onChanged, 'Saved.')
  }
  const removeKey = () => {
    if (!window.confirm(`Remove the ${chosen.name} API key? The agent stops until a new key is saved.`)) return
    setTested(null)
    run(async () => { await apiRequest('/admin/agent/key', { method: 'DELETE' }); await onChanged() }, 'Key removed.')
  }
  const runTest = () => { setTested(null); return runTestAction(async () => setTested(await apiRequest('/admin/agent/test', { method: 'POST' }))) }

  const detail = agent.key_configured ? `${chosen.name} · ${agent.model} · key saved`
    : chosen ? `${chosen.name} has no API key yet. The agent stays off until one is saved.` : 'No provider chosen yet. Pick one and paste its API key to turn the agent on.'
  return (
    <>
      <Row icon="AI" title="Model provider" detail={detail} tone={agent.key_configured ? undefined : 'warn'}>
        <button type="button" className="btn btn-sm" disabled={!agent.key_configured || test.busy} title={agent.key_configured ? 'Checks the key, the model, and one small request with the agent\'s tools (a few thousand tokens).' : 'Save an API key first.'} onClick={runTest}>{test.busy ? 'Testing…' : 'Test connection'}</button>
      </Row>
      {(test.error || tested) && <div className="px-[18px] py-2.5">{test.error ? <p role="alert" className="m-0 text-xs text-warn">{test.error}</p> : <p role="status" className="m-0 text-xs text-ok">Connected. {tested.provider} accepts the key, offers {tested.model} and takes the agent's tools{tested.tokens ? ` (${tested.tokens.toLocaleString()} tokens)` : ''}.</p>}</div>}
      <form className="grid items-start gap-3 px-[18px] py-3.5 md:grid-cols-[10rem_minmax(0,1fr)_minmax(0,1.5fr)_auto]" onSubmit={save}>
        <Field label="Provider">
          <select className="select h-9 w-full" value={provider} disabled={state.busy} onChange={(event) => choose(event.target.value)}>
            {providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </Field>
        <Field label="Model" hint={`Empty: ${defaultModel(provider)}`}>
          <input className="field mono h-9 w-full" maxLength={100} value={model} disabled={state.busy} onChange={(event) => setModel(event.target.value)} />
        </Field>
        <Field label="API key" hint={!secretsConfigured ? 'Set SECRETS_KEY in .env first: the key is stored encrypted with it.' : switching ? 'Each provider keeps its own key. Leave empty to keep a key saved earlier.' : agent.key_configured ? 'Saved. Paste a new key to replace it.' : 'Stored encrypted; never shown again.'}>
          <input type="password" className="field mono h-9 w-full" autoComplete="off" spellCheck={false} maxLength={512} value={apiKey} disabled={state.busy || !secretsConfigured}
            placeholder={agent.key_configured && !switching ? '••••••••' : ''} onChange={(event) => setApiKey(event.target.value)} />
        </Field>
        <div className="flex h-9 items-center gap-3 md:mt-[22px]">
          <button type="submit" className="btn btn-primary h-9" disabled={state.busy}>Save</button>
          {agent.key_configured && <button type="button" className="link text-xs" disabled={state.busy} onClick={removeKey}>Remove key</button>}
        </div>
        <div className="empty:hidden md:col-span-4"><Outcome state={state} /></div>
      </form>
      {confirming && <ConfirmDialog change={confirming.change} user={user} onConfirm={confirm} onClose={() => setConfirming(null)} />}
    </>
  )
}
