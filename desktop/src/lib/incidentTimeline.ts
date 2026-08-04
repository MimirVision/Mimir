import {
  calmerPersonNearWording,
  hasSupportedContactEvidence,
  hasSupportedImpactEvidence,
  impactEvidenceLevel,
  localEvidence,
  safeText,
} from './incidentEvidence'
import { cameraFeedsForIncident } from './incidentVideoPaths'
import { normalizeSeverity } from './incidentStatus'
import type { MimirIncident, MimirTimelineMarker } from '../types'

// Timeline/key-moment marker helpers: time formatting, labeling, priority
// ordering, and deriving the review timeline from raw incident evidence.
// Extracted from IncidentViewerScreen.tsx.

export function formatTime(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '0:00'
  }

  const safeValue = Math.max(0, Math.floor(value))
  const minutes = Math.floor(safeValue / 60)
  const seconds = safeValue % 60

  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

export function formatTimecode(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--:--'
  }

  const safeValue = Math.max(0, Math.floor(value))
  const minutes = Math.floor(safeValue / 60)
  const seconds = safeValue % 60

  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}

export function finiteNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

  return null
}

export function markerTime(marker: MimirTimelineMarker) {
  return typeof marker.time_sec === 'number' && Number.isFinite(marker.time_sec) ? marker.time_sec : null
}

export function markerTimeFromEvidence(marker: MimirTimelineMarker) {
  const directTime = markerTime(marker)
  if (directTime !== null) {
    return directTime
  }

  const record = marker as Record<string, unknown>
  const possibleTime =
    finiteNumber(record.timestamp_sec) ??
    finiteNumber(record.time_seconds) ??
    finiteNumber(record.seconds) ??
    finiteNumber(record.second) ??
    finiteNumber(record.time)

  return possibleTime !== null && possibleTime >= 0 ? possibleTime : null
}

export function markerTypeLabel(type?: string) {
  const value = String(type || '').toLowerCase()

  if (value === 'impact_contact' || (value.includes('impact') && value.includes('contact'))) {
    return 'Impact/contact'
  }

  if (value.includes('impact')) {
    return 'Impact'
  }

  if (value.includes('contact')) {
    return 'Possible contact'
  }

  if (value.includes('motion')) {
    return 'Peak motion'
  }

  if (value.includes('person')) {
    return 'Person nearby'
  }

  if (value.includes('start')) {
    return 'Activity starts'
  }

  if (value.includes('vehicle')) {
    return 'Vehicle nearby'
  }

  if (value.includes('middle')) {
    return 'Middle'
  }

  if (value.includes('review')) {
    return 'Review point'
  }

  return 'Key moment'
}

export function readableMarkerLabel(marker: MimirTimelineMarker) {
  const label = safeText(marker.label, '')
  const typeLabel = markerTypeLabel(marker.type)
  const normalizedLabel = label.toLowerCase().replace(/[_-]+/g, ' ').trim()

  if (!label || normalizedLabel === String(marker.type || '').toLowerCase().replace(/[_-]+/g, ' ').trim()) {
    return typeLabel
  }

  return label
}

export function markerIsPrimaryMoment(marker: MimirTimelineMarker, incident: MimirIncident) {
  const primaryTime = finiteNumber(incident.primary_key_moment_sec)
  const markerValue = markerTime(marker)
  return primaryTime !== null && markerValue !== null && Math.abs(primaryTime - markerValue) <= 1
}

export function markerLabel(marker: MimirTimelineMarker, incident?: MimirIncident) {
  const type = String(marker.type || '').toLowerCase()

  if (incident && (type === 'impact_contact' || (type.includes('impact') && type.includes('contact')))) {
    const local = localEvidence(incident)
    const impactLevel = impactEvidenceLevel(incident)
    if (
      local.strong_impact_like_motion === true ||
      local.crash_safety_triggered === true ||
      local.no_yolo_motion_impact_candidate === true ||
      impactLevel === 'HIGH'
    ) {
      return 'Impact'
    }

    return 'Impact/contact'
  }

  if (incident && markerIsPrimaryMoment(marker, incident)) {
    return type.includes('review') ? 'Review point' : 'Key moment'
  }

  if (incident && marker.type === 'possible_contact' && !hasSupportedContactEvidence(incident)) {
    return calmerPersonNearWording(incident) ? 'Person nearby' : 'Activity near vehicle'
  }

  if (
    incident &&
    marker.type === 'possible_impact' &&
    calmerPersonNearWording(incident) &&
    !hasSupportedImpactEvidence(incident)
  ) {
    return 'Activity near vehicle'
  }

  return readableMarkerLabel(marker)
}

export function markerDescription(marker: MimirTimelineMarker, incident?: MimirIncident) {
  if (
    incident &&
    calmerPersonNearWording(incident) &&
    (marker.type === 'possible_contact' || marker.type === 'possible_impact') &&
    !hasSupportedContactEvidence(incident) &&
    !hasSupportedImpactEvidence(incident)
  ) {
    return 'Mimir saw nearby activity, but no clear contact or tampering was detected.'
  }

  return marker.reason || marker.description || 'Jump to this point in the clip.'
}

export function validTimelineMarkers(markers?: MimirIncident['timeline_markers']) {
  return Array.isArray(markers) ? markers.filter(marker => marker && typeof marker === 'object') : []
}

export function timedMarkers(markers: MimirTimelineMarker[]) {
  return markers
    .filter(marker => markerTime(marker) !== null)
    .sort((left, right) => (markerTime(left) ?? 0) - (markerTime(right) ?? 0))
}

export function incidentDurationSeconds(incident: MimirIncident) {
  const directDuration = finiteNumber(incident.duration)
  if (directDuration !== null && directDuration > 0) {
    return directDuration
  }

  if (typeof incident.duration === 'string') {
    const text = incident.duration.trim()
    const numeric = Number(text)
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric
    }

    const parts = text.split(':').map(part => Number(part))
    if (parts.length === 2 && parts.every(part => Number.isFinite(part))) {
      return Math.max(0, parts[0] * 60 + parts[1])
    }
  }

  const cameraDurations = cameraFeedsForIncident(incident)
    .map(feed => finiteNumber(feed.durationSec))
    .filter((value): value is number => value !== null && value > 0)
  if (cameraDurations.length > 0) {
    return Math.max(...cameraDurations)
  }

  const localDuration = finiteNumber(localEvidence(incident).total_duration_sec)
  if (localDuration !== null && localDuration > 0) {
    return localDuration
  }

  return null
}

export function reviewTimelineDuration(incident: MimirIncident, videoDuration = 0) {
  if (Number.isFinite(videoDuration) && videoDuration > 0) {
    return videoDuration
  }

  return incidentDurationSeconds(incident) ?? 0
}

export function localNumberEvidence(incident: MimirIncident, key: string) {
  const local = localEvidence(incident)
  const value = local[key]
  return finiteNumber(value)
}

export function localBooleanEvidence(incident: MimirIncident, key: string) {
  const local = localEvidence(incident)
  return local[key] === true
}

export function hasImpactOrContactSignal(incident: MimirIncident) {
  return (
    Boolean(incident.possible_impact) ||
    Boolean(incident.possible_contact) ||
    localBooleanEvidence(incident, 'possible_impact') ||
    localBooleanEvidence(incident, 'possible_contact')
  )
}

export function markerIsNear(markers: MimirTimelineMarker[], time: number, tolerance = 0.75) {
  return markers.some(marker => {
    const markerValue = markerTime(marker)
    return markerValue !== null && Math.abs(markerValue - time) <= tolerance
  })
}

export function keyMomentSeverity(marker: MimirTimelineMarker, incident: MimirIncident) {
  const type = String(marker.type || '').toLowerCase()
  if (type === 'impact_contact' || type.includes('impact') || type.includes('contact')) {
    return normalizeSeverity(incident.final_severity || incident.severity) === 'IMPORTANT' ? 'IMPORTANT' : 'REVIEW'
  }

  if (type.includes('motion') || type.includes('person') || type.includes('vehicle')) {
    return 'REVIEW'
  }

  return 'NEUTRAL'
}

export function normalizeReviewMarker(marker: MimirTimelineMarker, incident: MimirIncident): MimirTimelineMarker | null {
  const time = markerTimeFromEvidence(marker)
  if (time === null) {
    return null
  }

  const readableLabel = readableMarkerLabel(marker)
  return {
    ...marker,
    time_sec: time,
    label: readableLabel,
    severity: marker.severity || keyMomentSeverity(marker, incident),
    description: marker.reason || marker.description || 'Jump to this point in the clip.',
  }
}

export function deriveReviewTimelineMarkers(incident: MimirIncident, videoDuration = 0) {
  const markers: MimirTimelineMarker[] = []
  const duration = reviewTimelineDuration(incident, videoDuration)
  const addMarker = (marker: MimirTimelineMarker) => {
    const normalized = normalizeReviewMarker(marker, incident)
    if (!normalized) {
      return
    }

    const time = markerTime(normalized)
    if (time === null || markerIsNear(markers, time)) {
      return
    }

    markers.push(normalized)
  }

  const userCorrectedTime = finiteNumber(incident.user_key_moment_sec)
  if (userCorrectedTime !== null && userCorrectedTime >= 0) {
    addMarker({
      type: 'user_corrected',
      severity: 'NEUTRAL',
      label: 'Actual moment',
      reason: 'Moment corrected by you.',
      source: 'user',
      time_sec: userCorrectedTime,
    })
  }

  for (const marker of validTimelineMarkers(incident.key_moments)) {
    addMarker(marker)
  }

  if (markers.length === 0) {
    const primaryTime = finiteNumber(incident.primary_key_moment_sec)
    if (primaryTime !== null && primaryTime >= 0) {
      const isImpactContact = hasImpactOrContactSignal(incident)
      addMarker({
        type: isImpactContact ? 'impact_contact' : 'review_point',
        severity: isImpactContact ? keyMomentSeverity({ type: 'impact_contact' }, incident) : 'REVIEW',
        label: incident.primary_key_moment_label || (isImpactContact ? 'Impact/contact' : 'Review point'),
        reason: 'Primary key moment from Mimir.',
        time_sec: primaryTime,
      })
    }
  }

  if (markers.length === 0) {
    const motionSpikeTime = localNumberEvidence(incident, 'motion_spike_time_sec')
    if (motionSpikeTime !== null && motionSpikeTime >= 0) {
      const isImpactContact = normalizeSeverity(incident.final_severity || incident.severity) === 'IMPORTANT' && hasImpactOrContactSignal(incident)
      addMarker({
        type: isImpactContact ? 'impact_contact' : 'motion_spike',
        severity: isImpactContact ? 'IMPORTANT' : normalizeSeverity(incident.final_severity || incident.severity),
        label: isImpactContact ? 'Impact/contact' : 'Peak motion',
        reason: isImpactContact ? 'Mimir found the strongest impact/contact evidence here.' : 'Mimir found the strongest local motion here.',
        time_sec: motionSpikeTime,
      })
    }
  }

  if (markers.length === 0 && duration > 0) {
    addMarker({
      type: 'review_point',
      severity: 'REVIEW',
      label: 'Review point',
      reason: 'No timed local evidence was available; start from the middle of the clip.',
      time_sec: duration / 2,
    })
  }

  return timedMarkers(markers)
}

export function markerPosition(marker: MimirTimelineMarker, index: number, markerCount: number, duration: number) {
  const time = markerTime(marker)

  if (time !== null && duration > 0) {
    return Math.max(0, Math.min(100, (time / duration) * 100))
  }

  if (markerCount <= 1) {
    return 50
  }

  return Math.max(0, Math.min(100, (index / (markerCount - 1)) * 100))
}

export function markerKey(marker: MimirTimelineMarker, index: number) {
  return `${marker.type || 'marker'}-${index}-${marker.time_sec ?? 'no-time'}-${marker.frame_index ?? 'no-frame'}`
}

export function markerPresentationPriority(marker: MimirTimelineMarker, incident: MimirIncident) {
  const type = String(marker.type || '').toLowerCase()
  const label = markerLabel(marker, incident).toLowerCase()

  if (type === 'user_corrected') {
    return 110
  }

  if (markerIsPrimaryMoment(marker, incident)) {
    return 100
  }

  if (label === 'impact' || type === 'impact' || type.includes('strong_impact')) {
    return 95
  }

  if (label.includes('impact/contact') || type === 'impact_contact') {
    return 92
  }

  if (label.includes('possible contact') || type.includes('contact')) {
    return 88
  }

  if (label.includes('review point') || type.includes('review')) {
    return 52
  }

  if (label.includes('key moment')) {
    return 50
  }

  if (type.includes('motion') || label.includes('motion')) {
    return 35
  }

  if (type.includes('person') || type.includes('vehicle') || type.includes('start') || label.includes('nearby') || label.includes('starts')) {
    return 20
  }

  return 30
}

export function markersAreNear(left: MimirTimelineMarker, right: MimirTimelineMarker, tolerance = 1) {
  const leftTime = markerTime(left)
  const rightTime = markerTime(right)
  return leftTime !== null && rightTime !== null && Math.abs(leftTime - rightTime) <= tolerance
}

export function sameMarkerLabel(left: MimirTimelineMarker, right: MimirTimelineMarker, incident: MimirIncident) {
  return markerLabel(left, incident).toLowerCase() === markerLabel(right, incident).toLowerCase()
}

export function dedupeMarkersByPriority(markers: MimirTimelineMarker[], incident: MimirIncident, options: { allMoments: boolean }) {
  const sortedByPriority = [...markers].sort((left, right) => {
    const priorityDelta = markerPresentationPriority(right, incident) - markerPresentationPriority(left, incident)
    if (priorityDelta !== 0) {
      return priorityDelta
    }

    return (markerTime(left) ?? 0) - (markerTime(right) ?? 0)
  })
  const kept: MimirTimelineMarker[] = []

  for (const marker of sortedByPriority) {
    const duplicate = kept.some(existing => {
      if (!markersAreNear(existing, marker, 1)) {
        return false
      }

      return !options.allMoments || sameMarkerLabel(existing, marker, incident)
    })

    if (!duplicate) {
      kept.push(marker)
    }
  }

  return timedMarkers(kept)
}
