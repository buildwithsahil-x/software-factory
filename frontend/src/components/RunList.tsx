import type { Run } from '../api'
import { StatusBadge } from './StatusBadge'
import { formatCost, formatTime } from './format'

interface Props {
  runs: Run[]
  selectedId: number | null
  onSelect: (id: number) => void
}

export function RunList({ runs, selectedId, onSelect }: Props) {
  return (
    <div className="card">
      <h2>Runs</h2>
      {runs.length === 0 && <p className="hint">No runs yet.</p>}
      <ul className="run-list">
        {runs.map((run) => (
          <li key={run.id}>
            <button
              type="button"
              className={`run-item ${run.id === selectedId ? 'active' : ''}`}
              onClick={() => onSelect(run.id)}
            >
              <span className="run-item-top">
                <span className="mono">#{run.id} {run.agent}</span>
                <StatusBadge status={run.status} />
              </span>
              <span className="run-item-prompt">{run.prompt}</span>
              <span className="run-item-meta">{formatTime(run.created_at)} · {formatCost(run.cost_usd)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
