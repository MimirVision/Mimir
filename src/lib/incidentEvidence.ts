import { formatEventType } from './incidentDisplay'
import { severityCopy } from './incidentViewerStyles'
import type { MimirIncident } from '../types'

// Local + AI evidence reading, and the formatting helpers evidence display
// depends on. Kept as one module rather than split further -- the AI-evidence
// functions reference the formatting functions and vice versa closely enough
// that separating them would just mean two files importing each other.

export function classificationDebug(incident: MimirIncident) {
  return incident.classification_debug &&
    typeof incident.classification_debug === 'object' &&
    !Array.isArray(incident.classification_debug)
    ? incident.classification_debug as Record<string, unknown>
    : {}
}

export function objectRecord(value: unknown) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function aiEvidence(incident: MimirIncident) {
  const direct = objectRecord(incident.ai_evidence)
  if (Object.keys(direct).length > 0) {
    return direct
  }

  const review = objectRecord(incident.ai_evidence_review)
  return objectRecord(review.ai_evidence)
}

export function localEvidence(incident: MimirIncident) {
  const direct = objectRecord(incident.local_evidence)
  if (Object.keys(direct).length > 0) {
    return direct
  }

  return objectRecord(incident.local_evidence_summary)
}

export function booleanEvidence(incident: MimirIncident, key: string, directValue?: boolean | null) {
  if (typeof directValue === 'boolean') {
    return directValue
  }

  const debugValue = classificationDebug(incident)[key]
  return typeof debugValue === 'boolean' ? debugValue : false
}

export function stringEvidence(incident: MimirIncident, key: string, directValue?: string | null) {
  const value = typeof directValue === 'string' ? directValue : classificationDebug(incident)[key]
  return typeof value === 'string' ? value.trim() : ''
}

export function contactEvidenceLevel(incident: MimirIncident) {
  const value = (
    stringEvidence(incident, 'contact_evidence_level', incident.contact_evidence_level) ||
    incident.contact_level ||
    ''
  ).toUpperCase()

  return ['NONE', 'LOW', 'MEDIUM', 'HIGH'].includes(value) ? value : 'NONE'
}

export function impactEvidenceLevel(incident: MimirIncident) {
  const value = (
    stringEvidence(incident, 'impact_evidence_level', incident.impact_evidence_level) ||
    incident.impact_level ||
    ''
  ).toUpperCase()

  return ['NONE', 'LOW', 'MEDIUM', 'HIGH'].includes(value) ? value : 'NONE'
}

export function importantEvidenceFound(incident: MimirIncident) {
  if (typeof incident.important_evidence_found === 'boolean') {
    return incident.important_evidence_found
  }

  const debugValue = classificationDebug(incident).important_evidence_found
  return typeof debugValue === 'boolean' ? debugValue : false
}

export function personNearOnlyIncident(incident: MimirIncident) {
  return booleanEvidence(incident, 'person_near_only', incident.person_near_only)
}

export function hasSupportedContactEvidence(incident: MimirIncident) {
  const level = contactEvidenceLevel(incident)
  return Boolean(incident.possible_contact) && (level === 'MEDIUM' || level === 'HIGH')
}

export function hasSupportedImpactEvidence(incident: MimirIncident) {
  const level = impactEvidenceLevel(incident)
  return Boolean(incident.possible_impact) && (level === 'MEDIUM' || level === 'HIGH')
}

export function calmerPersonNearWording(incident: MimirIncident) {
  return personNearOnlyIncident(incident) && !importantEvidenceFound(incident)
}

export function eventDisplayTitle(incident: MimirIncident) {
  if (calmerPersonNearWording(incident)) {
    return 'Person near vehicle'
  }

  return formatEventType(incident.event_type)
}

export function contactLevelCopy(incident: MimirIncident) {
  const level = contactEvidenceLevel(incident)

  if (level === 'NONE' || level === 'LOW') {
    return 'No clear contact detected'
  }

  return level
}

export function importantNotAppliedNote(incident: MimirIncident) {
  const debug = classificationDebug(incident)
  const hasBlockOrCap =
    booleanEvidence(incident, 'severity_cap_applied', incident.severity_cap_applied) ||
    Boolean(safeText(debug.ai_blocked_reason, '').trim())

  return hasBlockOrCap && !importantEvidenceFound(incident)
}

export function formatConfidence(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Not provided'
  }

  return `${Math.round(value * 100)}%`
}

export function formatNumber(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Not available'
  }

  return Number.isInteger(value) ? `${value}` : value.toFixed(1)
}

export function safeText(value: unknown, fallback = 'Not provided') {
  if (value === null || value === undefined) {
    return fallback
  }

  if (typeof value === 'string') {
    return value.trim() || fallback
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  try {
    return JSON.stringify(value)
  } catch {
    return fallback
  }
}

export function safeTextList(value: unknown) {
  return Array.isArray(value) ? value.map(item => safeText(item, '')).filter(Boolean) : []
}

export function prettyJson(value: unknown) {
  if (value === null || value === undefined || value === '') {
    return 'Not provided'
  }

  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return safeText(value)
  }
}

export function aiReviewed(incident: MimirIncident) {
  if (typeof incident.ai_reviewed === 'boolean') {
    return incident.ai_reviewed
  }

  const review = objectRecord(incident.ai_evidence_review)
  return typeof review.ai_reviewed === 'boolean' ? review.ai_reviewed : false
}

export function aiModelName(incident: MimirIncident) {
  if (incident.ai_model) {
    return incident.ai_model
  }

  const review = objectRecord(incident.ai_evidence_review)
  return safeText(review.ai_model || review.model, '')
}

export function aiSceneType(incident: MimirIncident) {
  const value = safeText(incident.ai_scene_type || aiEvidence(incident).scene_type, '')
  return value ? formatEventType(value) : ''
}

export function aiRecommendedSeverity(incident: MimirIncident) {
  const value = safeText(incident.ai_recommended_severity || aiEvidence(incident).recommended_severity, '')
  return value ? severityCopy(value) : 'Not provided'
}

export function aiConfidenceCopy(incident: MimirIncident) {
  const value = aiEvidence(incident).confidence
  if (typeof value === 'number') {
    return formatConfidence(value)
  }

  return formatConfidence(incident.ai_confidence)
}

export function reviewBadgeCopy(incident: MimirIncident) {
  return aiReviewed(incident) ? 'Local review + AI second opinion' : 'Local review'
}

export function experimentalAiUsed(incident: MimirIncident) {
  // aiModelName() alone is not proof of a real review: a clip still queued
  // for asynchronous AI enrichment gets a placeholder record with the model
  // name filled in but ai_reviewed=false and schema-default values
  // (recommended_severity "IGNORE", confidence 0.0) standing in until the
  // real pass runs. Treating that placeholder as a completed opinion showed
  // testers a fake "AI recommends Ignore, 0% confidence" on clips the AI
  // had never actually looked at.
  return Boolean(aiReviewed(incident) || Object.keys(aiEvidence(incident)).length > 0)
}

export function aiPendingReason(incident: MimirIncident) {
  if (experimentalAiUsed(incident)) {
    return ''
  }

  return safeText(incident.ai_review_skipped_reason, '')
}

export function aiQualityWarning(incident: MimirIncident) {
  if (incident.ai_quality_warning) {
    return incident.ai_quality_warning
  }

  const local = localEvidence(incident)
  const debug = classificationDebug(incident)
  const hasHardLocalEvidence = Boolean(
    local.strong_impact_like_motion ||
      local.hard_contact_candidate ||
      local.rear_impact_candidate ||
      debug.strong_impact_like_motion ||
      debug.hard_contact_candidate ||
      debug.rear_impact_candidate ||
      contactEvidenceLevel(incident) === 'HIGH' ||
      impactEvidenceLevel(incident) === 'HIGH',
  )
  const sceneType = safeText(aiEvidence(incident).scene_type, '').toLowerCase()
  const recommendedSeverity = safeText(aiEvidence(incident).recommended_severity, '').toUpperCase()

  if (hasHardLocalEvidence && (sceneType.includes('normal') || recommendedSeverity === 'IGNORE')) {
    return 'AI may have underestimated hard local impact/contact evidence. Mimir kept the local safety result.'
  }

  return ''
}

export function yesNo(value: unknown) {
  return value === true ? 'Yes' : value === false ? 'No' : 'Not provided'
}

export function evidenceMetricValue(evidence: Record<string, unknown>, key: string) {
  const value = evidence[key]
  if (typeof value === 'number') {
    return formatNumber(value)
  }

  if (typeof value === 'boolean') {
    return yesNo(value)
  }

  if (Array.isArray(value)) {
    return value.length ? safeText(value.join(', ')) : 'None'
  }

  return safeText(value)
}

export function severityReasonList(incident: MimirIncident) {
  return safeTextList(incident.severity_reasons)
}
