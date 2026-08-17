import { normalizeSeverity } from './incidentStatus'

// Storage badge and stripe styling specific to the incident library view.
// The severity label and badge are shared with the viewer -- see
// severityStyles.ts, re-exported at the bottom of this file.

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
    return 'border-green-300/[0.18] bg-green-500/10 text-green-100/[0.86]'
  }

  if (state === 'In Mimir Trash') {
    return 'border-red-300/[0.18] bg-red-500/10 text-red-100/[0.86]'
  }

  if (state === 'Missing file') {
    return 'border-amber-300/20 bg-amber-500/10 text-amber-100/[0.88]'
  }

  return 'border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)]'
}

export { severityClass, severityCopy } from './severityStyles'
