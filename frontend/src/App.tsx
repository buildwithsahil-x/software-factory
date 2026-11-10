import { useCallback, useEffect, useState } from 'react'
import { api, subscribe, type Agent, type Health, type Run } from './api'
import { NewRunForm } from './components/NewRunForm'
import { RunList } from './components/RunList'
import { RunDetail } from './components/RunDetail'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [agents, setAgents] = useState<Agent[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const refreshRuns = useCallback(() => {
    api.runs().then(setRuns).catch(() => { /* shown via health banner */ })
  }, [])

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setHealthError(e.message))
    api.agents().then(setAgents).catch(() => {})
    refreshRuns()
    // Any run status change anywhere refreshes the list.
    let pending: number | undefined
    const unsubscribe = subscribe('/api/stream', (e) => {
      if (e.type !== 'status') return
      window.clearTimeout(pending)
      pending = window.setTimeout(refreshRuns, 150)
    })
    return () => { unsubscribe(); window.clearTimeout(pending) }
  }, [refreshRuns])

  const selected = runs.find((r) => r.id === selectedId) ?? null

  return (
    <div className="app">
      <header className="topbar">
        <h1>Software Factory</h1>
        <HealthBadge health={health} error={healthError} />
      </header>

      {health && !health.api_key_configured && (
        <div className="banner warn">
          No Claude API key configured. Set <code>ANTHROPIC_API_KEY</code> in the factory&apos;s <code>.env</code> and
          restart the backend; agent runs will fail until then.
        </div>
      )}

      <main className="layout">
        <section className="sidebar">
          <NewRunForm
            agents={agents}
            onCreated={(run) => { setRuns((rs) => [run, ...rs]); setSelectedId(run.id) }}
          />
          <RunList runs={runs} selectedId={selectedId} onSelect={setSelectedId} />
        </section>
        <section className="content">
          {selected ? (
            <RunDetail key={selected.id} run={selected} agent={agents.find((a) => a.key === selected.agent)} />
          ) : (
            <div className="empty">
              <h2>No run selected</h2>
              <p>Start an agent run on the left, or pick one from the list to watch what it does.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

function HealthBadge({ health, error }: { health: Health | null; error: string | null }) {
  if (error) return <span className="pill bad">Backend unreachable</span>
  if (!health) return <span className="pill">Connecting…</span>
  return (
    <span className="pill ok" title={`v${health.version}`}>
      Backend OK · up to {health.max_concurrent_agents} agents at once
    </span>
  )
}
