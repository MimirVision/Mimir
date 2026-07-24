import { normalizeSeverity } from './incidentStatus'

// Severity/storage badge styling specific to the incident library view. The
// viewer screen has its own near-identical but not-quite-identical palette
// (different opacity values) -- kept separate rather than force-unified.

export function severityCopy(severity: string) {
  const normalized = normalizeSeverity(severity)

  if (normalized === 'IMPORTANT') {
    return 'Important'
  }

  if (normalized === 'REVIEW') {
    return 'Review'
  }

  return 'Ignored'
}

export function severityClass(severity: string) {
  const normalized = normalizeSeverity(severity)

  if (normalized === 'IMPORTANT') {
    return 'border-[rgba(196,119,114,0.28)] bg-[rgba(196,119,114,0.115)] text-red-100/92'
  }

  if (normalized === 'REVIEW') {
    return 'border-[rgba(195,160,98,0.28)] bg-[rgba(195,160,98,0.115)] text-amber-100/92'
  }

  return 'border-[rgba(133,139,139,0.20)] bg-[rgba(133,139,139,0.085)] text-[var(--mimir-text-muted)]'
}

// A left-edge color stripe alongside the text badge above, so severity reads at a
// glance across a shelf of cards instead of requiring each label to be read.
export function severityStripeClass(severity: string) {
  const normalized = normalizeSeverity(severity)

  if (normalized === 'IMPORTANT') {
    return 'before:bg-[var(--mimir-status-red)]'
  }

  if (normalized === 'REVIEW') {
    return 'before:bg-[var(--mimir-status-amber)]'
  }

  return 'before:bg-[var(--mimir-status-slate)]'
}

export function storageBadgeClass(state: string) {
  if (state === 'In Mimir Library') {
    return 'border-green-300/18 bg-green-500/10 text-green-100/86'
  }

  if (state === 'In Mimir Trash') {
    return 'border-red-300/18 bg-red-500/10 text-red-100/86'
  }

  if (state === 'Missing file') {
    return 'border-amber-300/20 bg-amber-500/10 text-amber-100/88'
  }

  return 'border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)]'
}
