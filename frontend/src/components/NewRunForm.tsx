import { useState, type FormEvent } from 'react'
import { api, type Agent, type Run } from '../api'

interface Props {
  agents: Agent[]
  onCreated: (run: Run) => void
}

export function NewRunForm({ agents, onCreated }: Props) {
  const [chosenKey, setAgentKey] = useState('')
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Default to the first agent until the user picks one.
  const agentKey = chosenKey || agents[0]?.key || ''
  const agent = agents.find((a) => a.key === agentKey)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!agentKey || !prompt.trim()) return
    setBusy(true)
    setError(null)
    try {
      onCreated(await api.createRun(agentKey, prompt.trim()))
      setPrompt('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New agent run</h2>
      <label>
        Agent
        <select value={agentKey} onChange={(e) => setAgentKey(e.target.value)}>
          {agents.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
        </select>
      </label>
      {agent && (
        <p className="hint">
          {agent.description} <br />
          <span className="mono">{agent.model}</span> · max {agent.max_turns} turns · budget ${agent.max_budget_usd.toFixed(2)}
        </p>
      )}
      <label>
        Task
        <textarea
          rows={4}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. Create hello.md with a short note, then list the files in the workspace."
        />
      </label>
      {error && <p className="error-text">{error}</p>}
      <button type="submit" disabled={busy || !prompt.trim() || !agentKey}>
        {busy ? 'Starting…' : 'Start run'}
      </button>
    </form>
  )
}
