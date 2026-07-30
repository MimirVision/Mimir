import { formatDateTime, formatEventType, sourceEventReason, sourceEventTimestamp, sourceFilename } from './incidentDisplay'
import { resolveIncidentSeverity, type ManualStatusOverrides, type SeverityGroup } from './incidentStatus'
import type { MimirCameraClip, MimirIncident, MimirSession } from '../types'

// Session/incident data helpers for the incident library view. Extracted from
// IncidentLibraryView.tsx.

const severityRank: Record<SeverityGroup, number> = {
  IMPORTANT: 0,
  REVIEW: 1,
  IGNORE: 2,
}

export function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return count === 1 ? singular : plural
}

export function scanResultCopy(session: MimirSession) {
  const incidentCount = (session.incidents ?? []).filter(incident => !incident.user_deleted).length
  const clipCount = session.scan_summary?.clips_scanned ?? session.clips_scanned ?? session.clips_processed ?? 0

  if (incidentCount === 0) {
    return 'No reviewable incidents found.'
  }

  return `${incidentCount} ${pluralize(incidentCount, 'incident')} found from ${clipCount} ${pluralize(
    clipCount,
    'clip',
  )} scanned.`
}

export function reviewModeCopy(session: MimirSession) {
  return session.ai_enabled ? 'Local review + AI second opinion' : 'Local review'
}

export function sessionClipsScanned(session: MimirSession) {
  return session.scan_summary?.clips_scanned ?? session.clips_scanned ?? session.clips_processed ?? null
}

export function sessionFeatureFlags(session: MimirSession) {
  return session.feature_flags && typeof session.feature_flags === 'object' ? session.feature_flags : {}
}

export function sessionMayBeStale(session: MimirSession) {
  const incidentCount = (session.incidents ?? []).filter(incident => !incident.user_deleted).length
  const clipsScanned = sessionClipsScanned(session)
  const featureFlags = sessionFeatureFlags(session)

  return (
    !session.core_version ||
    !session.scan_summary ||
    (incidentCount > 0 && clipsScanned === 0) ||
    featureFlags.no_yolo_crash_fallback !== true ||
    featureFlags.key_moments !== true
  )
}

export function formatTechnicalValue(value?: string | number | null) {
  if (value === undefined || value === null || value === '') {
    return 'Not reported'
  }

  return String(value)
}

export function technicalJson(value: unknown) {
  try {
    return JSON.stringify(value ?? null, null, 2)
  } catch {
    return String(value)
  }
}

export function searchText(incident: MimirIncident) {
  return [
    incident.id,
    incident.source_video,
    incident.severity,
    incident.ai_decision,
    incident.event_type,
    incident.summary,
    incident.recommended_action,
    incident.user_note,
    sourceEventReason(incident),
    sourceEventTimestamp(incident),
    ...(incident.evidence ?? []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

export function sortIncidents(incidents: MimirIncident[], manualOverrides: ManualStatusOverrides, identity: string) {
  return [...incidents].sort((left, right) => {
    const severityDelta =
      severityRank[resolveIncidentSeverity(left, manualOverrides, identity).displaySeverity] -
      severityRank[resolveIncidentSeverity(right, manualOverrides, identity).displaySeverity]

    if (severityDelta !== 0) {
      return severityDelta
    }

    return String(left.created_at || '').localeCompare(String(right.created_at || ''))
  })
}

export function incidentCardImages(incident: MimirIncident) {
  return [
    incident.hero_thumbnail,
    incident.thumbnail,
    incident.best_frame_image,
    incident.contact_sheet,
  ].filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
}

export function incidentActionId(incident: MimirIncident) {
  return incident.id || String(incident.event_id ?? '')
}

export function cameraClips(incident: MimirIncident) {
  const raw = incident.camera_clips
  const clips: MimirCameraClip[] = []

  if (Array.isArray(raw)) {
    clips.push(...raw.filter((clip): clip is MimirCameraClip => Boolean(clip && typeof clip === 'object')))
  } else if (raw && typeof raw === 'object') {
    clips.push(
      ...Object.entries(raw)
        .map(([camera, value]): MimirCameraClip | null => {
          if (!value) {
            return null
          }

          if (typeof value === 'string') {
            return { camera, path: value }
          }

          return { ...value, camera: value.camera || camera }
        })
        .filter((value): value is MimirCameraClip => value !== null),
    )
  }

  const seen = new Set<string>()

  return clips.filter(clip => {
    const camera = String(clip.camera || '').trim().toLowerCase()
    const path = String(clip.library_path || clip.trash_path || clip.path || clip.video_path || clip.source_video || clip.source_clip || clip.filename || '').trim().toLowerCase()
    const key = `${camera}|${path}`

    if (!key.trim() || seen.has(key)) {
      return false
    }

    seen.add(key)
    return true
  })
}

export function cameraCount(incident: MimirIncident) {
  const explicitCount = typeof incident.camera_count === 'number' ? incident.camera_count : 0
  return Math.max(explicitCount, cameraClips(incident).length, 1)
}

export function cameraCountLabel(incident: MimirIncident) {
  const count = cameraCount(incident)
  return `${count} ${count === 1 ? 'angle' : 'angles'}`
}

export function cameraNameLabel(value?: string | null) {
  const raw = typeof value === 'string' ? value.trim() : ''

  if (!raw) {
    return ''
  }

  const normalized = raw.toLowerCase().replace(/[^a-z0-9]+/g, '_')

  if (normalized === 'front') {
    return 'Front'
  }

  if (normalized === 'back' || normalized === 'rear') {
    return 'Rear'
  }

  if (normalized === 'left_repeater') {
    return 'Left repeater'
  }

  if (normalized === 'right_repeater') {
    return 'Right repeater'
  }

  if (normalized === 'left_pillar') {
    return 'Left pillar'
  }

  if (normalized === 'right_pillar') {
    return 'Right pillar'
  }

  if (normalized === 'left') {
    return 'Left'
  }

  if (normalized === 'right') {
    return 'Right'
  }

  return raw
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function eventLabel(incident: MimirIncident) {
  const summary = typeof incident.summary === 'string' ? incident.summary.trim() : ''
  if (summary) {
    return summary.length > 82 ? `${summary.slice(0, 79)}...` : summary
  }

  if (incident.event_type) {
    return formatEventType(incident.event_type)
  }

  return 'Review moment'
}

export function incidentCardTitle(severity: SeverityGroup) {
  if (severity === 'IMPORTANT') {
    return 'Impact/contact detected'
  }

  if (severity === 'REVIEW') {
    return 'Needs review'
  }

  return 'No issue found'
}

export function incidentCardSubtitle(incident: MimirIncident) {
  const explicitTitle = incident.display_title || incident.source_stem || ''
  const fileName =
    incident.source_filename ||
    sourceFilename(incident.source_video || incident.original_source_video || incident.video_path || '') ||
    ''
  const timestamp = formatDateTime(sourceEventTimestamp(incident))
  const source = explicitTitle || fileName

  return [timestamp, source].filter(Boolean).join(' - ')
}

export function isGenericBestEffortIncident(incident: MimirIncident) {
  return incident.filename_timestamp_detected === false
}

export function currentStorageState(incident: MimirIncident) {
  if (incident.user_deleted || incident.storage_state === 'trash') {
    return 'In Mimir Trash'
  }

  if (incident.moved_to_library || incident.library_video_path || incident.storage_state === 'library') {
    return 'In Mimir Library'
  }

  if (incident.video_exists === false) {
    return 'Missing file'
  }

  return 'On USB / Original source'
}

export function incidentFolderPath(incident: MimirIncident) {
  const paths = [
    incident.library_video_path,
    incident.trash_video_path,
    incident.video_path,
    incident.source_video,
    incident.original_source_video,
    ...cameraClips(incident).map(clip => clip.library_path || clip.trash_path || clip.path || clip.video_path || clip.source_video),
  ]

  return paths.find((value): value is string => typeof value === 'string' && value.trim().length > 0) || ''
}

export function sourceClipsRemain(incidents: MimirIncident[]) {
  return incidents.some(incident => {
    if (incident.user_deleted || incident.moved_to_library || incident.storage_state === 'trash' || incident.storage_state === 'library') {
      return false
    }

    return Boolean(incident.source_video || incident.original_source_video || cameraClips(incident).length > 0)
  })
}

export function incidentsForSeverity(
  incidents: MimirIncident[],
  severity: SeverityGroup,
  manualOverrides: ManualStatusOverrides,
  identity: string,
) {
  return incidents.filter(incident => resolveIncidentSeverity(incident, manualOverrides, identity).displaySeverity === severity)
}
