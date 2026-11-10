export function formatCost(cost: number | null): string {
  return cost == null ? '—' : `$${cost.toFixed(cost < 0.01 ? 4 : 2)}`
}

export function formatTime(iso: string | null): string {
  if (!iso) return '—'
  // Backend timestamps are UTC; older rows may lack the offset.
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function formatDuration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const toDate = (s: string) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(s) ? s : `${s}Z`)
  const ms = (end ? toDate(end) : new Date()).getTime() - toDate(start).getTime()
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}
