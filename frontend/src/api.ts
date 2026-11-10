export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Health {
  status: string
  version: string
  api_key_configured: boolean
  max_concurrent_agents: number
}

export interface Agent {
  key: string
  name: string
  description: string
  model: string
  tools: string[]
  skills: string[]
  max_turns: number
  max_budget_usd: number
}

export interface Run {
  id: number
  project_id: number | null
  agent: string
  model: string
  prompt: string
  status: RunStatus
  workspace: string | null
  session_id: string | null
  num_turns: number | null
  cost_usd: number | null
  result: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface FactoryEvent {
  id: number
  run_id: number
  ts: string
  type: string
  data: Record<string, unknown>
}

export const EVENT_TYPES = [
  'status', 'session_started', 'assistant_text', 'thinking', 'tool_use', 'tool_result',
  'progress', 'guardrail_block', 'result', 'error',
] as const

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* keep statusText */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/api/health'),
  agents: () => request<Agent[]>('/api/agents'),
  runs: () => request<Run[]>('/api/runs'),
  run: (id: number) => request<Run>(`/api/runs/${id}`),
  createRun: (agent: string, prompt: string) =>
    request<Run>('/api/runs', { method: 'POST', body: JSON.stringify({ agent, prompt }) }),
  cancelRun: (id: number) => request<{ cancelled: boolean }>(`/api/runs/${id}/cancel`, { method: 'POST' }),
}

/** Subscribe to server-sent events; returns an unsubscribe function. */
export function subscribe(path: string, onEvent: (e: FactoryEvent) => void): () => void {
  const source = new EventSource(path)
  const handler = (msg: MessageEvent) => onEvent(JSON.parse(msg.data) as FactoryEvent)
  for (const type of EVENT_TYPES) source.addEventListener(type, handler)
  return () => source.close()
}
