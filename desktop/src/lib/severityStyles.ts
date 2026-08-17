import { normalizeSeverity } from './incidentStatus'

// Severity label and badge styling shared by the viewer and the library.
//
// These lived in both incidentViewerStyles.ts and incidentLibraryStyles.ts,
// each carrying a comment claiming the other's palette was "near-identical but
// not-quite-identical". They returned the same strings, so a colour change made
// in one place silently only applied to half the app.

export function severityCopy(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'Important'
  }

  if (value === 'REVIEW') {
    return 'Review'
  }

  return 'Ignored'
}

export function severityClass(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'border-[rgba(196,119,114,0.28)] bg-[rgba(196,119,114,0.115)] text-red-100/[0.92]'
  }

  if (value === 'REVIEW') {
    return 'border-[rgba(195,160,98,0.28)] bg-[rgba(195,160,98,0.115)] text-amber-100/[0.92]'
  }

  return 'border-[rgba(133,139,139,0.20)] bg-[rgba(133,139,139,0.085)] text-[var(--mimir-text-muted)]'
}
