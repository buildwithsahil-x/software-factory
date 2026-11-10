import type { RunStatus } from '../api'

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge badge-${status}`}>{status}</span>
}
