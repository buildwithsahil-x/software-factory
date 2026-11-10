import { useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribe, type Agent, type FactoryEvent, type Run } from '../api'
import { StatusBadge } from './StatusBadge'
import { formatCost, formatDuration, formatTime } from './format'

interface Props {
  run: Run
  agent?: Agent
}

export function RunDetail({ run, agent }: Props) {
  const [events, setEvents] = useState<FactoryEvent[]>([])
  const [follow, setFollow] = useState(true)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const feedEnd = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const seen = new Set<number>()
    return subscribe(`/api/runs/${run.id}/stream`, (e) => {
      if (seen.has(e.id)) return
      seen.add(e.id)
      setEvents((prev) => [...prev, e])
    })
  }, [run.id])

  useEffect(() => {
    if (follow) feedEnd.current?.scrollIntoView({ block: 'end' })
  }, [events, follow])

  // Attach each tool result to the tool call that produced it.
  const results = useMemo(() => {
    const map = new Map<string, FactoryEvent>()
    for (const e of events) if (e.type === 'tool_result') map.set(String(e.data.tool_use_id), e)
    return map
  }, [events])

  const active = run.status === 'queued' || run.status === 'running'

  async function cancel() {
    setCancelError(null)
    try {
      await api.cancelRun(run.id)
    } catch (err) {
      setCancelError((err as Error).message)
    }
  }

  return (
    <div className="run-detail">
      <div className="card">
        <div className="run-head">
          <h2>Run #{run.id} · {agent?.name ?? run.agent}</h2>
          <StatusBadge status={run.status} />
          {active && <button type="button" className="secondary" onClick={cancel}>Cancel</button>}
        </div>
        {cancelError && <p className="error-text">{cancelError}</p>}
        <p className="prompt">{run.prompt}</p>
        <dl className="facts">
          <div><dt>Model</dt><dd className="mono">{run.model}</dd></div>
          <div><dt>Turns</dt><dd>{run.num_turns ?? '—'}</dd></div>
          <div><dt>Cost</dt><dd>{formatCost(run.cost_usd)}</dd></div>
          <div><dt>Duration</dt><dd>{formatDuration(run.started_at, run.finished_at)}</dd></div>
          <div className="wide"><dt>Workspace</dt><dd className="mono">{run.workspace ?? '—'}</dd></div>
        </dl>
        {run.error && <p className="error-text">{run.error}</p>}
      </div>

      <div className="card feed-card">
        <div className="feed-head">
          <h2>Live activity</h2>
          <label className="inline">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow
          </label>
        </div>
        <ol className="feed">
          {events.filter((e) => e.type !== 'tool_result').map((e) => (
            <EventRow key={e.id} event={e} result={e.type === 'tool_use' ? results.get(String(e.data.id)) : undefined} />
          ))}
        </ol>
        {active && <p className="hint pulse">Agent is working…</p>}
        <div ref={feedEnd} />
      </div>
    </div>
  )
}

function str(v: unknown): string {
  if (v == null) return ''
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
}

function toolSummary(name: string, input: unknown): string {
  if (input && typeof input === 'object') {
    const i = input as Record<string, unknown>
    if (typeof i.command === 'string') return i.command
    if (typeof i.file_path === 'string') return i.file_path
    if (typeof i.pattern === 'string') return i.pattern
    if (typeof i.message === 'string') return i.message
  }
  return name
}

function EventRow({ event, result }: { event: FactoryEvent; result?: FactoryEvent }) {
  const { type, data } = event
  const time = <span className="time">{formatTime(event.ts)}</span>

  switch (type) {
    case 'status':
      return <li className="ev ev-status">{time}<span>Status → <strong>{str(data.status)}</strong></span></li>
    case 'session_started':
      return (
        <li className="ev ev-muted">{time}
          <span>Session started · <span className="mono">{str(data.model)}</span> · {(data.tools as string[] | undefined)?.length ?? 0} tools</span>
        </li>
      )
    case 'progress':
      return <li className="ev ev-progress">{time}<span>📍 {str(data.message)}</span></li>
    case 'assistant_text':
      return <li className="ev ev-text">{time}<span className="pre">{str(data.text)}</span></li>
    case 'thinking':
      return (
        <li className="ev ev-muted">{time}
          <details><summary>Thinking</summary><pre>{str(data.text)}</pre></details>
        </li>
      )
    case 'tool_use': {
      const failed = Boolean(result?.data.is_error)
      return (
        <li className={`ev ev-tool ${failed ? 'ev-tool-failed' : ''}`}>{time}
          <details>
            <summary><span className="tool-name">{str(data.name)}</span> <span className="mono">{toolSummary(str(data.name), data.input)}</span>{failed && ' · failed'}</summary>
            <pre>{str(data.input)}</pre>
            {result && <pre className="tool-output">{str(result.data.output) || '(no output)'}</pre>}
          </details>
        </li>
      )
    }
    case 'guardrail_block':
      return (
        <li className="ev ev-block">{time}
          <span>🛡️ <strong>Guardrail blocked {str(data.tool)}</strong>: {str(data.reason)}</span>
        </li>
      )
    case 'result':
      return (
        <li className={`ev ${data.is_error ? 'ev-block' : 'ev-result'}`}>{time}
          <span>Finished ({str(data.subtype)}) · {str(data.num_turns)} turns · {formatCost((data.cost_usd as number | null) ?? null)}
            {data.result ? <span className="pre result-text">{str(data.result)}</span> : null}
          </span>
        </li>
      )
    case 'error':
      return <li className="ev ev-block">{time}<span>Error: {str(data.message)}</span></li>
    default:
      return <li className="ev ev-muted">{time}<span>{type}</span></li>
  }
}
