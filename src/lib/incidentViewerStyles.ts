import { normalizeSeverity } from './incidentStatus'
import type { MimirTimelineMarker } from '../types'

// Severity/marker visual styling specific to the incident viewer screen. The
// library view has its own near-identical but not-quite-identical palette
// (different opacity values) -- kept separate rather than force-unified.

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
    return 'border-[rgba(196,119,114,0.28)] bg-[rgba(196,119,114,0.115)] text-red-100/92'
  }

  if (value === 'REVIEW') {
    return 'border-[rgba(195,160,98,0.28)] bg-[rgba(195,160,98,0.115)] text-amber-100/92'
  }

  return 'border-[rgba(133,139,139,0.20)] bg-[rgba(133,139,139,0.085)] text-[var(--mimir-text-muted)]'
}

export function markerClass(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'border-red-100/55 bg-[var(--mimir-status-red)] shadow-[0_0_0_5px_rgba(185,101,97,0.12)]'
  }

  if (value === 'REVIEW') {
    return 'border-amber-100/55 bg-[var(--mimir-status-amber)] shadow-[0_0_0_5px_rgba(173,139,85,0.12)]'
  }

  return 'border-white/40 bg-[var(--mimir-status-slate)]'
}

export function markerVisualClass(marker: MimirTimelineMarker) {
  const type = String(marker.type || '').toLowerCase()
  if (type === 'user_corrected') {
    return 'border-emerald-100/70 bg-[var(--mimir-accent)] shadow-[0_0_0_7px_rgba(157,183,170,0.15),0_0_26px_rgba(157,183,170,0.20)]'
  }
  if (type === 'impact_contact' || type.includes('impact') || type.includes('contact')) {
    return 'border-red-100/70 bg-[var(--mimir-status-red)] shadow-[0_0_0_7px_rgba(185,101,97,0.16),0_0_28px_rgba(185,101,97,0.22)]'
  }

  return markerClass(marker.severity)
}

export function markerAccentClass(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'bg-[var(--mimir-status-red)]'
  }

  if (value === 'REVIEW') {
    return 'bg-[var(--mimir-status-amber)]'
  }

  return 'bg-white/32'
}

export function markerAccentForMarker(marker: MimirTimelineMarker) {
  const type = String(marker.type || '').toLowerCase()
  if (type === 'impact_contact' || type.includes('impact') || type.includes('contact')) {
    return 'bg-[var(--mimir-status-red)]'
  }

  return markerAccentClass(marker.severity)
}

export function markerSeverityCopy(severity?: string) {
  const value = String(severity ?? '').toUpperCase()

  if (value === 'IMPORTANT') {
    return 'Important'
  }

  if (value === 'REVIEW') {
    return 'Review'
  }

  if (value === 'NEUTRAL') {
    return 'Neutral'
  }

  return value || 'Neutral'
}
