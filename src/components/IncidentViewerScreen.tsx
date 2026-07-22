import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type SyntheticEvent } from 'react'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { open as openDialog, save as saveDialog } from '@tauri-apps/plugin-dialog'
import { CrashSafeBoundary, logIncidentDiagnostic } from './CrashSafeBoundary'
import type { MimirCameraClip, MimirIncident, MimirSession, MimirTimelineMarker } from '../types'

interface IncidentViewerScreenProps {
  incident: MimirIncident
  session?: MimirSession
  severityResolution: SeverityResolution
  onBack: () => void
  onReloadSession: () => Promise<MimirSession | null>
  onIncidentUpdated: (incident: MimirIncident) => void
  onManualStatusChange: (status: SeverityGroup) => void
}

interface ClipActionResult {
  ok: boolean
  action: string
  incident_id: string
  message: string
  updated_session: string
  stdout?: string
  stderr?: string
}

interface StorageActionResult {
  ok: boolean
  action: string
  incident_id: string
  message: string
  updated_session: string
  report_json: string
  backend_runner?: 'exe' | 'python_script' | string
  stdout?: string
  stderr?: string
}

interface StorageActionReport {
  ok?: boolean
  action?: string
  moved_files?: unknown[]
  failed_files?: unknown[]
  skipped_files?: unknown[]
  failures?: unknown[]
}

type MediaMode = 'video' | 'image' | 'empty'
type IncidentAction = 'set_status_IGNORE' | 'set_status_REVIEW' | 'set_status_IMPORTANT' | 'move_to_library' | 'move_to_trash' | 'restore_from_trash' | 'delete' | 'save_note' | 'save_feedback' | 'save_key_moment'
type AiFeedbackChoice = 'Correct' | 'Should be Important' | 'Should be Review' | 'Should be Ignore' | 'Weird AI flag' | 'Missed obvious event'
type SeverityGroup = 'IMPORTANT' | 'REVIEW' | 'IGNORE'

interface SeverityResolution {
  mimirSeverity: SeverityGroup
  displaySeverity: SeverityGroup
  isManualOverride: boolean
  source: 'manual' | 'mimir'
}

interface ViewerMediaChoice {
  mode: MediaMode
  path: string
  label: string
}

type ViewerMode = 'focus' | 'grid'

interface ViewerSeekRequest {
  time: number
  nonce: number
}

interface ViewerPlaybackRequest {
  nonce: number
}

interface CameraFeed {
  key: string
  camera: string
  label: string
  path: string
  filename: string
  durationSec?: number | null
  exists: boolean | null
  isPrimary: boolean
}

type ViewerFileAction = 'original' | 'current' | 'library' | 'trash'

interface IncidentFeedbackResult {
  ok: boolean
  feedback_folder: string
  feedback_file: string
  video_copied: boolean
  message: string
}

interface TrainingContributionResult {
  ok: boolean
  output_path: string
  backend_runner: string
  backend_command: string
  message: string
}

const videoExtensionPattern = /\.(mp4|mov|m4v|avi|mkv|webm)$/i
const imageExtensionPattern = /\.(jpe?g|png|webp|bmp|gif)$/i
const feedbackChoices: AiFeedbackChoice[] = [
  'Correct',
  'Should be Important',
  'Should be Review',
  'Should be Ignore',
  'Weird AI flag',
  'Missed obvious event',
]

type GridSecondaryPlaybackMode = 'video' | 'poster'

const GRID_SECONDARY_PLAYBACK_MODE: GridSecondaryPlaybackMode = 'video'
const SECONDARY_SYNC_INTERVAL_MS = 750
const SECONDARY_SYNC_DRIFT_SEC = 0.35
const UI_TIME_UPDATE_MS = 250

function localFileSrc(path: string) {
  return convertFileSrc(path, 'asset')
}

function isAbsoluteLocalPath(value?: string) {
  if (!value) {
    return false
  }

  return /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith('\\\\') || value.startsWith('//') || value.startsWith('/')
}

function isTextInputElement(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  const tagName = target.tagName.toLowerCase()

  return tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target.isContentEditable
}

function isVideoPath(value?: string) {
  return Boolean(value && videoExtensionPattern.test(value))
}

function isImagePath(value?: string) {
  return Boolean(value && imageExtensionPattern.test(value))
}

function cleanPath(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function firstCameraClipPath(incident: MimirIncident) {
  const cameraClips = incident.camera_clips

  if (Array.isArray(cameraClips)) {
    for (const clip of cameraClips) {
      const path = cleanPath(clip?.path) || cleanPath(clip?.video_path) || cleanPath(clip?.source_video) || cleanPath(clip?.source_clip)

      if (path) {
        return path
      }
    }
  }

  if (cameraClips && typeof cameraClips === 'object') {
    for (const value of Object.values(cameraClips)) {
      const path =
        typeof value === 'string'
          ? cleanPath(value)
          : cleanPath(value?.path) || cleanPath(value?.video_path) || cleanPath(value?.source_video) || cleanPath(value?.source_clip)

      if (path) {
        return path
      }
    }
  }

  return ''
}

function normalizeCameraKey(value?: string | null) {
  return String(value || 'camera').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_') || 'camera'
}

function cameraLabel(value?: string | null) {
  const normalized = normalizeCameraKey(value)

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

  return String(value || 'Camera')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ') || 'Camera'
}

function playableCameraPath(clip: MimirCameraClip) {
  return (
    cleanPath(clip.library_path) ||
    cleanPath(clip.path) ||
    cleanPath(clip.video_path) ||
    cleanPath(clip.source_video) ||
    cleanPath(clip.source_clip) ||
    cleanPath(clip.original_source_video)
  )
}

function normalizeCameraClips(incident: MimirIncident): MimirCameraClip[] {
  const raw = incident.camera_clips
  const clips: MimirCameraClip[] = []

  if (Array.isArray(raw)) {
    clips.push(...raw.filter((clip): clip is MimirCameraClip => Boolean(clip && typeof clip === 'object')))
  } else if (raw && typeof raw === 'object') {
    for (const [camera, value] of Object.entries(raw)) {
      if (typeof value === 'string') {
        clips.push({ camera, path: value, filename: sourceFilename(value), exists: null })
        continue
      }

      if (value && typeof value === 'object') {
        clips.push({ ...value, camera: value.camera ?? camera })
      }
    }
  }

  const seen = new Set<string>()

  return clips.filter(clip => {
    const camera = normalizeCameraKey(clip.camera)
    const path = (
      cleanPath(clip.library_path) ||
      cleanPath(clip.trash_path) ||
      cleanPath(clip.path) ||
      cleanPath(clip.video_path) ||
      cleanPath(clip.source_video) ||
      cleanPath(clip.source_clip) ||
      cleanPath(clip.original_source_video) ||
      cleanPath(clip.filename)
    ).toLowerCase()
    const key = `${camera}|${path}`

    if (!key.trim() || seen.has(key)) {
      return false
    }

    seen.add(key)
    return true
  })
}

function feedSortScore(feed: CameraFeed, primaryCamera?: string | null) {
  const normalized = normalizeCameraKey(feed.camera)
  const wantedPrimary = normalizeCameraKey(primaryCamera)

  if (feed.isPrimary || (wantedPrimary !== 'camera' && normalized === wantedPrimary)) {
    return 0
  }

  if (normalized === 'back' || normalized === 'rear') {
    return 1
  }

  if (normalized === 'front') {
    return 2
  }

  if (normalized.includes('left')) {
    return 3
  }

  if (normalized.includes('right')) {
    return 4
  }

  return 5
}

function cameraFeedsForIncident(incident: MimirIncident) {
  const clips = normalizeCameraClips(incident)
  const primaryCamera = normalizeCameraKey(incident.primary_camera)
  const primaryPath =
    cleanPath(incident.video_path) ||
    cleanPath(incident.library_video_path) ||
    cleanPath(incident.source_video) ||
    cleanPath(incident.original_source_video) ||
    cleanPath(incident.source_clip)
  const feeds = new Map<string, CameraFeed>()

  for (const clip of clips) {
    const rawCamera = cleanPath(clip.camera) || 'camera'
    const keyBase = normalizeCameraKey(rawCamera)
    const path = playableCameraPath(clip)
    const isPrimary = normalizeCameraKey(rawCamera) === primaryCamera || Boolean(primaryPath && path === primaryPath)
    const key = `${keyBase}-${path || cleanPath(clip.filename) || feeds.size}`

    feeds.set(key, {
      key,
      camera: rawCamera,
      label: cameraLabel(rawCamera),
      path,
      filename: cleanPath(clip.filename) || sourceFilename(path),
      durationSec: typeof clip.duration_sec === 'number' ? clip.duration_sec : null,
      exists: typeof clip.exists === 'boolean' ? clip.exists : null,
      isPrimary,
    })
  }

  if (primaryPath && !Array.from(feeds.values()).some(feed => feed.path === primaryPath)) {
    const primaryLabel = cleanPath(incident.primary_camera) || 'Primary'
    feeds.set(`primary-${primaryPath}`, {
      key: `primary-${primaryPath}`,
      camera: primaryLabel,
      label: cameraLabel(primaryLabel),
      path: primaryPath,
      filename: sourceFilename(primaryPath),
      durationSec: null,
      exists: incident.video_exists === false ? false : null,
      isPrimary: true,
    })
  }

  return Array.from(feeds.values())
    .filter(feed => feed.path || feed.exists === false)
    .sort((left, right) => feedSortScore(left, incident.primary_camera) - feedSortScore(right, incident.primary_camera))
}

function canUseKnownVideoPath(incident: MimirIncident, value: string | undefined, label: string) {
  if (!isAbsoluteLocalPath(value) || !isVideoPath(value)) {
    return false
  }

  return true
}

function videoCandidatesForIncident(incident: MimirIncident) {
  return [
    { path: cleanPath(incident.video_path), label: 'video_path' },
    { path: cleanPath(incident.library_video_path), label: 'library_video_path' },
    { path: cleanPath(incident.source_video), label: 'source_video' },
    { path: cleanPath(incident.original_source_video), label: 'original_source_video' },
    { path: firstCameraClipPath(incident), label: 'camera_clips' },
  ].filter(candidate => canUseKnownVideoPath(incident, candidate.path, candidate.label))
}

function attemptedVideoPathForIncident(incident: MimirIncident) {
  return videoCandidatesForIncident(incident)[0]?.path || cleanPath(incident.video_path) || cleanPath(incident.library_video_path) || cleanPath(incident.source_video)
}

function resolveViewerMedia(incident: MimirIncident): ViewerMediaChoice {
  const videoCandidate = videoCandidatesForIncident(incident)[0]

  if (videoCandidate?.path) {
    return { mode: 'video', path: videoCandidate.path, label: videoCandidate.label }
  }

  const imageCandidates = [
    { path: cleanPath(incident.hero_thumbnail), label: 'hero_thumbnail' },
    { path: cleanPath(incident.contact_sheet), label: 'contact_sheet' },
    { path: cleanPath(incident.thumbnail), label: 'thumbnail' },
  ]
  const imageCandidate = imageCandidates.find(candidate => isAbsoluteLocalPath(candidate.path) && isImagePath(candidate.path))

  if (imageCandidate?.path) {
    return { mode: 'image', path: imageCandidate.path, label: imageCandidate.label }
  }

  return { mode: 'empty', path: '', label: 'none' }
}

function normalizeSeverity(severity?: string) {
  const value = String(severity ?? '').toUpperCase()

  if (value === 'IMPORTANT' || value === 'REVIEW') {
    return value
  }

  return 'IGNORE'
}

function severityCopy(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'Important'
  }

  if (value === 'REVIEW') {
    return 'Review'
  }

  return 'Ignored'
}

function severityClass(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'border-[rgba(196,119,114,0.28)] bg-[rgba(196,119,114,0.115)] text-red-100/92'
  }

  if (value === 'REVIEW') {
    return 'border-[rgba(195,160,98,0.28)] bg-[rgba(195,160,98,0.115)] text-amber-100/92'
  }

  return 'border-[rgba(133,139,139,0.20)] bg-[rgba(133,139,139,0.085)] text-[var(--mimir-text-muted)]'
}

function markerClass(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'border-red-100/55 bg-[var(--mimir-status-red)] shadow-[0_0_0_5px_rgba(185,101,97,0.12)]'
  }

  if (value === 'REVIEW') {
    return 'border-amber-100/55 bg-[var(--mimir-status-amber)] shadow-[0_0_0_5px_rgba(173,139,85,0.12)]'
  }

  return 'border-white/40 bg-[var(--mimir-status-slate)]'
}

function markerVisualClass(marker: MimirTimelineMarker) {
  const type = String(marker.type || '').toLowerCase()
  if (type === 'user_corrected') {
    return 'border-emerald-100/70 bg-[var(--mimir-accent)] shadow-[0_0_0_7px_rgba(157,183,170,0.15),0_0_26px_rgba(157,183,170,0.20)]'
  }
  if (type === 'impact_contact' || type.includes('impact') || type.includes('contact')) {
    return 'border-red-100/70 bg-[var(--mimir-status-red)] shadow-[0_0_0_7px_rgba(185,101,97,0.16),0_0_28px_rgba(185,101,97,0.22)]'
  }

  return markerClass(marker.severity)
}

function markerAccentClass(severity?: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'bg-[var(--mimir-status-red)]'
  }

  if (value === 'REVIEW') {
    return 'bg-[var(--mimir-status-amber)]'
  }

  return 'bg-white/32'
}

function markerAccentForMarker(marker: MimirTimelineMarker) {
  const type = String(marker.type || '').toLowerCase()
  if (type === 'impact_contact' || type.includes('impact') || type.includes('contact')) {
    return 'bg-[var(--mimir-status-red)]'
  }

  return markerAccentClass(marker.severity)
}

function markerSeverityCopy(severity?: string) {
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

function classificationDebug(incident: MimirIncident) {
  return incident.classification_debug &&
    typeof incident.classification_debug === 'object' &&
    !Array.isArray(incident.classification_debug)
    ? incident.classification_debug as Record<string, unknown>
    : {}
}

function objectRecord(value: unknown) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function aiEvidence(incident: MimirIncident) {
  const direct = objectRecord(incident.ai_evidence)
  if (Object.keys(direct).length > 0) {
    return direct
  }

  const review = objectRecord(incident.ai_evidence_review)
  return objectRecord(review.ai_evidence)
}

function localEvidence(incident: MimirIncident) {
  const direct = objectRecord(incident.local_evidence)
  if (Object.keys(direct).length > 0) {
    return direct
  }

  return objectRecord(incident.local_evidence_summary)
}

function booleanEvidence(incident: MimirIncident, key: string, directValue?: boolean | null) {
  if (typeof directValue === 'boolean') {
    return directValue
  }

  const debugValue = classificationDebug(incident)[key]
  return typeof debugValue === 'boolean' ? debugValue : false
}

function stringEvidence(incident: MimirIncident, key: string, directValue?: string | null) {
  const value = typeof directValue === 'string' ? directValue : classificationDebug(incident)[key]
  return typeof value === 'string' ? value.trim() : ''
}

function contactEvidenceLevel(incident: MimirIncident) {
  const value = (
    stringEvidence(incident, 'contact_evidence_level', incident.contact_evidence_level) ||
    incident.contact_level ||
    ''
  ).toUpperCase()

  return ['NONE', 'LOW', 'MEDIUM', 'HIGH'].includes(value) ? value : 'NONE'
}

function impactEvidenceLevel(incident: MimirIncident) {
  const value = (
    stringEvidence(incident, 'impact_evidence_level', incident.impact_evidence_level) ||
    incident.impact_level ||
    ''
  ).toUpperCase()

  return ['NONE', 'LOW', 'MEDIUM', 'HIGH'].includes(value) ? value : 'NONE'
}

function importantEvidenceFound(incident: MimirIncident) {
  if (typeof incident.important_evidence_found === 'boolean') {
    return incident.important_evidence_found
  }

  const debugValue = classificationDebug(incident).important_evidence_found
  return typeof debugValue === 'boolean' ? debugValue : false
}

function personNearOnlyIncident(incident: MimirIncident) {
  return booleanEvidence(incident, 'person_near_only', incident.person_near_only)
}

function hasSupportedContactEvidence(incident: MimirIncident) {
  const level = contactEvidenceLevel(incident)
  return Boolean(incident.possible_contact) && (level === 'MEDIUM' || level === 'HIGH')
}

function hasSupportedImpactEvidence(incident: MimirIncident) {
  const level = impactEvidenceLevel(incident)
  return Boolean(incident.possible_impact) && (level === 'MEDIUM' || level === 'HIGH')
}

function calmerPersonNearWording(incident: MimirIncident) {
  return personNearOnlyIncident(incident) && !importantEvidenceFound(incident)
}

function eventDisplayTitle(incident: MimirIncident) {
  if (calmerPersonNearWording(incident)) {
    return 'Person near vehicle'
  }

  return formatEventType(incident.event_type)
}

function assessmentCopy(incident: MimirIncident) {
  if (calmerPersonNearWording(incident)) {
    return 'Mimir saw a person near the vehicle, but no clear contact or tampering was detected.'
  }

  return incident.summary || 'No summary was included for this incident.'
}

function contactLevelCopy(incident: MimirIncident) {
  const level = contactEvidenceLevel(incident)

  if (level === 'NONE' || level === 'LOW') {
    return 'No clear contact detected'
  }

  return level
}

function importantNotAppliedNote(incident: MimirIncident) {
  const debug = classificationDebug(incident)
  const hasBlockOrCap =
    booleanEvidence(incident, 'severity_cap_applied', incident.severity_cap_applied) ||
    Boolean(safeText(debug.ai_blocked_reason, '').trim())

  return hasBlockOrCap && !importantEvidenceFound(incident)
}

function formatEventType(value?: string) {
  if (!value) {
    return 'Unclassified moment'
  }

  return value
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return ''
  }

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function sourceEventReason(incident: MimirIncident) {
  return incident.source_event_reason || incident.tesla_event_reason || ''
}

function sourceEventTimestamp(incident: MimirIncident) {
  return incident.source_event_timestamp || incident.tesla_event_timestamp || incident.created_at
}

function formatConfidence(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Not provided'
  }

  return `${Math.round(value * 100)}%`
}

function formatNumber(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Not available'
  }

  return Number.isInteger(value) ? `${value}` : value.toFixed(1)
}

function safeText(value: unknown, fallback = 'Not provided') {
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

function safeTextList(value: unknown) {
  return Array.isArray(value) ? value.map(item => safeText(item, '')).filter(Boolean) : []
}

function prettyJson(value: unknown) {
  if (value === null || value === undefined || value === '') {
    return 'Not provided'
  }

  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return safeText(value)
  }
}

function aiReviewed(incident: MimirIncident) {
  if (typeof incident.ai_reviewed === 'boolean') {
    return incident.ai_reviewed
  }

  const review = objectRecord(incident.ai_evidence_review)
  return typeof review.ai_reviewed === 'boolean' ? review.ai_reviewed : false
}

function aiModelName(incident: MimirIncident) {
  if (incident.ai_model) {
    return incident.ai_model
  }

  const review = objectRecord(incident.ai_evidence_review)
  return safeText(review.ai_model || review.model, '')
}

function aiSceneType(incident: MimirIncident) {
  const value = safeText(incident.ai_scene_type || aiEvidence(incident).scene_type, '')
  return value ? formatEventType(value) : ''
}

function aiRecommendedSeverity(incident: MimirIncident) {
  const value = safeText(incident.ai_recommended_severity || aiEvidence(incident).recommended_severity, '')
  return value ? severityCopy(value) : 'Not provided'
}

function aiConfidenceCopy(incident: MimirIncident) {
  const value = aiEvidence(incident).confidence
  if (typeof value === 'number') {
    return formatConfidence(value)
  }

  return formatConfidence(incident.ai_confidence)
}

function reviewBadgeCopy(incident: MimirIncident) {
  return aiReviewed(incident) ? 'Local review + AI second opinion' : 'Local review'
}

function reviewBadgeDescription(incident: MimirIncident) {
  if (aiReviewed(incident)) {
    return 'Mimir reviewed this locally, with an experimental AI second opinion kept separate from the final result.'
  }

  return 'Mimir reviewed this locally.'
}

function experimentalAiUsed(incident: MimirIncident) {
  return Boolean(aiReviewed(incident) || aiModelName(incident) || Object.keys(aiEvidence(incident)).length > 0)
}

function aiQualityWarning(incident: MimirIncident) {
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

function yesNo(value: unknown) {
  return value === true ? 'Yes' : value === false ? 'No' : 'Not provided'
}

function evidenceMetricValue(evidence: Record<string, unknown>, key: string) {
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

function severityReasonList(incident: MimirIncident) {
  return safeTextList(incident.severity_reasons)
}

function pathFolder(value?: string) {
  if (!value) {
    return ''
  }

  const index = Math.max(value.lastIndexOf('\\'), value.lastIndexOf('/'))
  return index > 0 ? value.slice(0, index) : value
}

function sourceFilename(value?: string) {
  if (!value) {
    return ''
  }

  const parts = value.split(/[\\/]/)
  return parts[parts.length - 1] || value
}

function readLocalSetting(key: string) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function originalVideoPath(incident: MimirIncident) {
  return cleanPath(incident.original_source_video) || cleanPath(incident.source_video) || cleanPath(incident.source_clip)
}

function currentVideoPath(incident: MimirIncident) {
  if (incident.user_deleted && incident.trash_video_path) {
    return incident.trash_video_path
  }

  return attemptedVideoPathForIncident(incident) || cleanPath(incident.source_clip)
}

function incidentFeedbackPayload(
  incident: MimirIncident,
  feedback: AiFeedbackChoice,
  notes: string,
  includeVideo: boolean,
  session?: MimirSession,
) {
  const sourcePath = cleanPath(incident.source_video) || cleanPath(incident.original_source_video) || cleanPath(incident.source_clip) || attemptedVideoPathForIncident(incident)

  return {
    incident_id: incident.id || String(incident.event_id ?? ''),
    current_severity: normalizeSeverity(incident.severity),
    user_selected_feedback: feedback,
    notes: notes.trim(),
    source_filename: sourceFilename(sourcePath),
    timestamp: new Date().toISOString(),
    ai_evidence_review: incident.ai_evidence_review ?? null,
    classification_debug: incident.classification_debug ?? null,
    experimental_ai_enabled: readLocalSetting('experimental_ai_enabled'),
    experimental_ai_model: readLocalSetting('experimental_ai_model'),
    experimental_ai_budget: readLocalSetting('experimental_ai_budget'),
    experimental_ai_timeout_sec: readLocalSetting('experimental_ai_timeout_sec'),
    ai_enabled: session?.ai_enabled ?? aiReviewed(incident),
    ai_model: session?.ai_model ?? (aiModelName(incident) || null),
    ai_reviewed_groups: session?.ai_reviewed_groups ?? null,
    ai_failed_groups: session?.ai_failed_groups ?? null,
    thumbnail_path: cleanPath(incident.thumbnail) || cleanPath(incident.hero_thumbnail),
    hero_thumbnail_path: cleanPath(incident.hero_thumbnail),
    contact_sheet_path: cleanPath(incident.contact_sheet),
    include_video_clip: includeVideo,
    video_included_by_user: includeVideo,
    automatic_upload: false,
    user_key_moment_sec: incident.user_key_moment_sec ?? null,
    mimir_primary_key_moment_sec: incident.primary_key_moment_sec ?? null,
  }
}

function storageState(incident: MimirIncident) {
  const state = cleanPath(incident.storage_state).toLowerCase()

  if (
    incident.user_deleted ||
    incident.trash_video_path ||
    state === 'trash' ||
    state === 'partial_trash' ||
    incident.storage_action_applied === 'mimir_trash'
  ) {
    return state === 'partial_trash' ? 'Mimir Trash (partial)' : 'Mimir Trash'
  }

  if (
    incident.moved_to_library ||
    incident.library_video_path ||
    state === 'library' ||
    state === 'partial_library' ||
    incident.storage_action_applied === 'move_to_library'
  ) {
    return state === 'partial_library' ? 'Mimir Library (partial)' : 'Mimir Library'
  }

  if (incident.video_exists === false) {
    return 'Missing file'
  }

  return 'Original source'
}

function ignoreStorageCopy(incident: MimirIncident) {
  if (normalizeSeverity(incident.severity) !== 'IGNORE') {
    return ''
  }

  const state = storageState(incident)

  if (state === 'Mimir Library') {
    return 'This ignored clip is stored in Mimir Library.'
  }

  if (state === 'Mimir Trash') {
    return 'This ignored clip is in Mimir Trash.'
  }

  if (state === 'Original source') {
    return 'This clip is marked Ignore, but the original file is still in the source folder.'
  }

  return 'This clip is marked Ignore, but Mimir cannot confirm the current file location.'
}

function incidentActionId(incident: MimirIncident) {
  return incident.id || String(incident.event_id ?? '')
}

function findIncident(session: MimirSession, incident: MimirIncident) {
  const wantedId = incident.id
  const wantedEventId = incident.event_id

  return session.incidents.find(candidate => {
    if (wantedId && candidate.id === wantedId) {
      return true
    }

    return String(candidate.event_id ?? '') === String(wantedEventId ?? '')
  })
}

function actionButtonTone(status: string, active: boolean) {
  const severity = normalizeSeverity(status)

  if (severity === 'IMPORTANT') {
    return active
      ? 'border-[rgba(185,101,97,0.36)] bg-[rgba(185,101,97,0.14)] text-red-100'
      : 'border-white/[0.075] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:bg-white/[0.05] hover:text-[var(--mimir-text)]'
  }

  if (severity === 'REVIEW') {
    return active
      ? 'border-[rgba(173,139,85,0.42)] bg-[rgba(173,139,85,0.18)] text-amber-100'
      : 'border-[rgba(173,139,85,0.18)] bg-[rgba(173,139,85,0.07)] text-amber-100/78 hover:bg-[rgba(173,139,85,0.12)]'
  }

  return active
    ? 'border-white/24 bg-white/[0.095] text-[var(--mimir-text)]'
    : 'border-white/[0.075] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:bg-white/[0.05] hover:text-[var(--mimir-text)]'
}

function SmallSpinner() {
  return <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current/25 border-t-current" />
}

function FilePathRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-t border-white/[0.055] py-2.5 first:border-t-0">
      <div className="text-[10px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-1 break-all text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        {value || 'Not available'}
      </div>
    </div>
  )
}

function FolderButton({
  label,
  disabled = false,
  onClick,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="h-8 rounded-md border border-white/[0.07] bg-white/[0.025] px-2.5 text-[11px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-45"
    >
      {label}
    </button>
  )
}

function FileLocationPanel({
  incident,
  onOpenOriginalFolder,
  onOpenCurrentFolder,
  onOpenLibrary,
  onOpenTrash,
}: {
  incident: MimirIncident
  onOpenOriginalFolder: () => void
  onOpenCurrentFolder: () => void
  onOpenLibrary: () => void
  onOpenTrash: () => void
}) {
  const originalPath = originalVideoPath(incident)
  const currentPath = currentVideoPath(incident)
  const currentState = storageState(incident)
  const ignoreCopy = ignoreStorageCopy(incident)

  return (
    <div className="mt-5 rounded-xl border border-white/[0.045] bg-black/12 p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[12px] font-semibold text-[var(--mimir-text)]">File location</div>
          <p className="mt-1 text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
            Current storage state: <span className="text-[var(--mimir-text-muted)]">{currentState}</span>
          </p>
        </div>
        {currentState === 'Missing file' && (
          <span className="rounded-full border border-red-300/20 bg-red-500/10 px-2.5 py-1 text-[11px] font-semibold text-red-100/85">
            Missing file
          </span>
        )}
      </div>

      {ignoreCopy && (
        <div className="mb-3 rounded-lg border border-[rgba(127,133,136,0.16)] bg-white/[0.018] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          {ignoreCopy}
        </div>
      )}

      <div className="rounded-lg border border-white/[0.04] bg-white/[0.012] px-3">
        <FilePathRow label="Current storage" value={currentState} />
        <FilePathRow label="Original source" value={originalPath ? 'Available' : 'Not available'} />
        <FilePathRow label="Mimir Library" value={incident.library_video_path ? 'Available' : 'Not available'} />
        <FilePathRow label="Mimir Trash" value={incident.trash_video_path ? 'Available' : 'Not available'} />
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <FolderButton label="Open original folder" disabled={!originalPath} onClick={onOpenOriginalFolder} />
        <FolderButton label="Open current folder" disabled={!currentPath} onClick={onOpenCurrentFolder} />
        <FolderButton label="Open Mimir Library" onClick={onOpenLibrary} />
        <FolderButton label="Open Mimir Trash" onClick={onOpenTrash} />
      </div>
    </div>
  )
}

function ViewerFilesDrawer({
  incident,
  onClose,
  onOpenFileAction,
}: {
  incident: MimirIncident
  onClose: () => void
  onOpenFileAction: (action: ViewerFileAction) => void
}) {
  const clips = normalizeCameraClips(incident)
  const state = storageState(incident)
  const currentPath = currentVideoPath(incident)
  const originalPath = originalVideoPath(incident)

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/62 backdrop-blur-sm">
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="Close files drawer"
        onClick={onClose}
      />
      <aside className="relative h-full w-full max-w-[420px] overflow-y-auto border-l border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <div className="text-[12px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">Files</div>
            <h2 className="mt-2 text-[22px] font-semibold text-[var(--mimir-text)]">Incident files</h2>
            <p className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
              Storage state: <span className="text-[var(--mimir-text)]">{state}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="h-9 rounded-lg bg-white/[0.045] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.075] hover:text-[var(--mimir-text)]"
          >
            Close
          </button>
        </div>

        <div className="rounded-2xl border border-white/[0.06] bg-black/14 p-4">
          <div className="mb-3 text-[12px] font-semibold text-[var(--mimir-text)]">Camera clips</div>
          {clips.length > 0 ? (
            <div className="space-y-2">
              {clips.map((clip, index) => {
                const camera = cameraLabel(clip.camera)
                const path = playableCameraPath(clip)
                const available = clip.exists === false ? false : Boolean(path)

                return (
                  <div
                    key={`${camera}-${path || index}`}
                    className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.045] bg-white/[0.018] px-3 py-2.5"
                  >
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-[var(--mimir-text)]">{camera}</div>
                      <div className="mt-0.5 truncate text-[12px] text-[var(--mimir-text-subtle)]">
                        {clip.filename || sourceFilename(path) || 'Filename unavailable'}
                      </div>
                    </div>
                    <span
                      className={`shrink-0 rounded-full border px-2 py-1 text-[10px] font-semibold ${
                        available
                          ? 'border-white/[0.08] bg-white/[0.04] text-[var(--mimir-text-muted)]'
                          : 'border-red-300/18 bg-red-500/10 text-red-100/78'
                      }`}
                    >
                      {available ? 'Available' : 'Missing'}
                    </span>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="rounded-xl border border-white/[0.045] bg-white/[0.018] p-3 text-[13px] text-[var(--mimir-text-muted)]">
              No grouped camera clip list was included.
            </div>
          )}
        </div>

        <div className="mt-4 rounded-2xl border border-white/[0.06] bg-black/14 p-4">
          <div className="mb-3 text-[12px] font-semibold text-[var(--mimir-text)]">Folders</div>
          <div className="grid gap-2">
            <FolderButton label="Open original folder" disabled={!originalPath} onClick={() => onOpenFileAction('original')} />
            <FolderButton label="Open current folder" disabled={!currentPath} onClick={() => onOpenFileAction('current')} />
            <FolderButton label="Open Mimir Library" onClick={() => onOpenFileAction('library')} />
            <FolderButton label="Open Mimir Trash" onClick={() => onOpenFileAction('trash')} />
          </div>
        </div>
      </aside>
    </div>
  )
}

function actionErrorMessage(error: unknown) {
  if (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    typeof (error as { message?: unknown }).message === 'string'
  ) {
    return (error as { message: string }).message
  }

  return error instanceof Error ? error.message : String(error)
}

function parseStorageActionReport(value: string): StorageActionReport {
  if (!value.trim()) {
    return {}
  }

  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as StorageActionReport : {}
  } catch {
    return {}
  }
}

function storageActionDetails(reportJson: string, result?: StorageActionResult) {
  const report = parseStorageActionReport(reportJson)
  const details = {
    report,
    backend_runner: result?.backend_runner || '',
    stdout: result?.stdout || '',
    stderr: result?.stderr || '',
  }

  return JSON.stringify(details, null, 2)
}

function storageActionStatus(report: StorageActionReport) {
  const movedCount = Array.isArray(report.moved_files) ? report.moved_files.length : 0
  const failedCount = Array.isArray(report.failed_files) ? report.failed_files.length : 0
  const failureCount = Array.isArray(report.failures) ? report.failures.length : 0

  return {
    movedCount,
    failedCount,
    failureCount,
    partial: movedCount > 0 && (failedCount > 0 || failureCount > 0),
    failed: movedCount === 0 && (failedCount > 0 || failureCount > 0 || report.ok === false),
  }
}

function formatTime(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '0:00'
  }

  const safeValue = Math.max(0, Math.floor(value))
  const minutes = Math.floor(safeValue / 60)
  const seconds = safeValue % 60

  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function formatTimecode(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--:--'
  }

  const safeValue = Math.max(0, Math.floor(value))
  const minutes = Math.floor(safeValue / 60)
  const seconds = safeValue % 60

  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}

function readableMarkerLabel(marker: MimirTimelineMarker) {
  const label = safeText(marker.label, '')
  const typeLabel = markerTypeLabel(marker.type)
  const normalizedLabel = label.toLowerCase().replace(/[_-]+/g, ' ').trim()

  if (!label || normalizedLabel === String(marker.type || '').toLowerCase().replace(/[_-]+/g, ' ').trim()) {
    return typeLabel
  }

  return label
}

function markerLabel(marker: MimirTimelineMarker, incident?: MimirIncident) {
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

function markerDescription(marker: MimirTimelineMarker, incident?: MimirIncident) {
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

function validTimelineMarkers(markers?: MimirIncident['timeline_markers']) {
  return Array.isArray(markers) ? markers.filter(marker => marker && typeof marker === 'object') : []
}

function timedMarkers(markers: MimirTimelineMarker[]) {
  return markers
    .filter(marker => markerTime(marker) !== null)
    .sort((left, right) => (markerTime(left) ?? 0) - (markerTime(right) ?? 0))
}

function finiteNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

  return null
}

function incidentDurationSeconds(incident: MimirIncident) {
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

function reviewTimelineDuration(incident: MimirIncident, videoDuration = 0) {
  if (Number.isFinite(videoDuration) && videoDuration > 0) {
    return videoDuration
  }

  return incidentDurationSeconds(incident) ?? 0
}

function localNumberEvidence(incident: MimirIncident, key: string) {
  const local = localEvidence(incident)
  const value = local[key]
  return finiteNumber(value)
}

function localBooleanEvidence(incident: MimirIncident, key: string) {
  const local = localEvidence(incident)
  return local[key] === true
}

function markerTypeLabel(type?: string) {
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

function markerTimeFromEvidence(marker: MimirTimelineMarker) {
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

function hasImpactOrContactSignal(incident: MimirIncident) {
  return (
    Boolean(incident.possible_impact) ||
    Boolean(incident.possible_contact) ||
    localBooleanEvidence(incident, 'possible_impact') ||
    localBooleanEvidence(incident, 'possible_contact')
  )
}

function markerIsNear(markers: MimirTimelineMarker[], time: number, tolerance = 0.75) {
  return markers.some(marker => {
    const markerValue = markerTime(marker)
    return markerValue !== null && Math.abs(markerValue - time) <= tolerance
  })
}

function keyMomentSeverity(marker: MimirTimelineMarker, incident: MimirIncident) {
  const type = String(marker.type || '').toLowerCase()
  if (type === 'impact_contact' || type.includes('impact') || type.includes('contact')) {
    return normalizeSeverity(incident.final_severity || incident.severity) === 'IMPORTANT' ? 'IMPORTANT' : 'REVIEW'
  }

  if (type.includes('motion') || type.includes('person') || type.includes('vehicle')) {
    return 'REVIEW'
  }

  return 'NEUTRAL'
}

function normalizeReviewMarker(marker: MimirTimelineMarker, incident: MimirIncident): MimirTimelineMarker | null {
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

function deriveReviewTimelineMarkers(incident: MimirIncident, videoDuration = 0) {
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

function timelineDuration(markers: MimirTimelineMarker[], incident: MimirIncident, videoDuration = 0) {
  const markerMax = markers.reduce((max, marker) => {
    const value = typeof marker.time_sec === 'number' && Number.isFinite(marker.time_sec) ? marker.time_sec : 0
    return Math.max(max, value)
  }, 0)

  if (Number.isFinite(videoDuration) && videoDuration > 0) {
    return Math.max(videoDuration, markerMax, 1)
  }

  if (typeof incident.duration === 'number' && Number.isFinite(incident.duration)) {
    return Math.max(incident.duration, markerMax, 1)
  }

  return Math.max(markerMax, 1)
}

function DetailMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="border-t border-white/[0.065] py-3 first:border-t-0">
      <div className="text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-1.5 break-words text-[13px] font-medium leading-5 text-[var(--mimir-text)]">
        {value}
      </div>
    </div>
  )
}

function TechnicalJsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-lg border border-white/[0.045] bg-black/20 p-3">
      <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">{label}</div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-[var(--mimir-text-muted)]">
        {prettyJson(value)}
      </pre>
    </div>
  )
}

function ViewerMedia({
  incident,
  seekRequest,
  playbackRequest,
  currentTime,
  duration,
  onTimeUpdate,
  onDurationChange,
}: {
  incident: MimirIncident
  seekRequest: ViewerSeekRequest | null
  playbackRequest: ViewerPlaybackRequest | null
  currentTime: number
  duration: number
  onTimeUpdate: (time: number) => void
  onDurationChange: (duration: number) => void
}) {
  const cameraFeeds = useMemo(() => cameraFeedsForIncident(incident), [incident])
  const videoCandidates = useMemo(() => videoCandidatesForIncident(incident), [incident])
  const media = useMemo(() => resolveViewerMedia(incident), [incident])
  const fallbackImage =
    [incident.hero_thumbnail, incident.contact_sheet, incident.thumbnail, incident.best_frame_image]
      .map(cleanPath)
      .find(path => isAbsoluteLocalPath(path) && isImagePath(path)) ?? ''
  const [viewerMode, setViewerMode] = useState<ViewerMode>('focus')
  const [selectedFeedKey, setSelectedFeedKey] = useState('')
  const [failedFeedKeys, setFailedFeedKeys] = useState<Set<string>>(() => new Set())
  const [loadedFeedKeys, setLoadedFeedKeys] = useState<Set<string>>(() => new Set())
  const [loadingFeedKeys, setLoadingFeedKeys] = useState<Set<string>>(() => new Set())
  const [imageFailed, setImageFailed] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [selectedGridKey, setSelectedGridKey] = useState('')
  const videoRefs = useRef<Map<string, HTMLVideoElement>>(new Map())
  const lastUiTimeUpdateRef = useRef(0)
  const lastSeekNonceRef = useRef<number | null>(null)
  const lastPlaybackNonceRef = useRef<number | null>(null)
  const isPlayingRef = useRef(false)
  const pendingCameraHandoffRef = useRef<{ key: string; time: number; playing: boolean } | null>(null)
  const primaryFeed = cameraFeeds.find(feed => feed.isPrimary) || cameraFeeds[0]
  const selectedFeed = cameraFeeds.find(feed => feed.key === selectedFeedKey) || primaryFeed
  const playableFeeds = cameraFeeds.filter(feed => feed.path && isAbsoluteLocalPath(feed.path) && isVideoPath(feed.path) && feed.exists !== false)
  const canUseMultiCamera = cameraFeeds.length > 1
  const showSingleVideo = !canUseMultiCamera && Boolean(videoCandidates[0]?.path) && !failedFeedKeys.has('single-video')
  const allVisibleFeeds = canUseMultiCamera ? cameraFeeds : []
  const secondaryFeeds = canUseMultiCamera ? allVisibleFeeds.filter(feed => feed.key !== selectedFeed?.key) : []
  const gridFeeds = canUseMultiCamera ? allVisibleFeeds : []
  const anyVideoFailed = failedFeedKeys.size > 0
  const imagePath =
    (!canUseMultiCamera && !showSingleVideo && fallbackImage) || media.mode === 'image'
      ? fallbackImage || media.path
      : ''
  const showImage = Boolean(imagePath) && !imageFailed
  const showVideoFallbackMessage = !canUseMultiCamera && failedFeedKeys.has('single-video') && Boolean(fallbackImage)
  const showMissingVideoMessage = !showSingleVideo && !canUseMultiCamera && showImage
  const videoSrc = showSingleVideo && videoCandidates[0] ? localFileSrc(videoCandidates[0].path) : ''
  const imageSrc = showImage ? localFileSrc(imagePath) : ''
  const selectedFeedLabel = selectedFeed?.isPrimary
    ? `Best: ${selectedFeed.label}`
    : selectedFeed?.label || 'Camera'
  const masterVideoKey = canUseMultiCamera ? selectedFeed?.key || '' : showSingleVideo ? 'single-video' : ''

  const getMasterVideo = useCallback(() => {
    if (masterVideoKey) {
      const masterVideo = videoRefs.current.get(masterVideoKey)
      if (masterVideo) {
        return masterVideo
      }
    }

    return Array.from(videoRefs.current.values()).find(video => video.readyState > 0) || null
  }, [masterVideoKey])

  const updateUiTime = useCallback(
    (time: number, force = false) => {
      if (!Number.isFinite(time)) {
        return
      }

      const now = window.performance.now()
      if (!force && now - lastUiTimeUpdateRef.current < UI_TIME_UPDATE_MS) {
        return
      }

      lastUiTimeUpdateRef.current = now
      onTimeUpdate(Math.max(0, time))
    },
    [onTimeUpdate],
  )

  const syncSecondaryVideos = useCallback(
    (time: number, options: { playing?: boolean; force?: boolean } = {}) => {
      if (!Number.isFinite(time)) {
        return
      }

      const targetTime = Math.max(0, time)
      const shouldPlay = Boolean(options.playing)

      for (const [key, video] of videoRefs.current.entries()) {
        if (!video || key === masterVideoKey || video.readyState === 0 || failedFeedKeys.has(key)) {
          continue
        }

        try {
          const drift = Math.abs(video.currentTime - targetTime)
          if (options.force || drift > SECONDARY_SYNC_DRIFT_SEC) {
            video.currentTime = targetTime
          }

          if (shouldPlay) {
            void video.play().catch(() => {
              // A secondary camera refusing playback should not stop the master feed.
            })
          } else if (!video.paused) {
            video.pause()
          }
        } catch {
          // Keep other camera feeds usable if one media element refuses a seek/play.
        }
      }
    },
    [failedFeedKeys, masterVideoKey],
  )

  const syncAllVideosTo = useCallback(
    (time: number, playing: boolean) => {
      if (!Number.isFinite(time)) {
        return
      }

      const targetTime = Math.max(0, time)

      for (const [key, video] of videoRefs.current.entries()) {
        if (!video || video.readyState === 0 || failedFeedKeys.has(key)) {
          continue
        }

        try {
          if (Math.abs(video.currentTime - targetTime) > 0.05) {
            video.currentTime = targetTime
          }

          if (playing) {
            void video.play().catch(() => {
              if (key === masterVideoKey) {
                setIsPlaying(false)
              }
            })
          } else if (!video.paused) {
            video.pause()
          }
        } catch {
          // Shared seeking is best-effort per feed.
        }
      }

      updateUiTime(targetTime, true)
    },
    [failedFeedKeys, masterVideoKey, updateUiTime],
  )

  const handleSharedPlayToggle = useCallback(() => {
    const masterVideo = getMasterVideo()
    const baseTime = masterVideo?.currentTime ?? currentTime
    const nextPlaying = !isPlaying

    setIsPlaying(nextPlaying)
    syncAllVideosTo(baseTime, nextPlaying)
  }, [currentTime, getMasterVideo, isPlaying, syncAllVideosTo])

  useEffect(() => {
    isPlayingRef.current = isPlaying
  }, [isPlaying])

  useEffect(() => {
    const nextPrimary = cameraFeeds.find(feed => feed.isPrimary) || cameraFeeds[0]
    setViewerMode('focus')
    setSelectedFeedKey(nextPrimary?.key || '')
    setSelectedGridKey(nextPrimary?.key || '')
    setFailedFeedKeys(new Set())
    setLoadedFeedKeys(nextPrimary?.key ? new Set([nextPrimary.key]) : new Set())
    setLoadingFeedKeys(new Set())
    setImageFailed(false)
    setIsPlaying(false)
    videoRefs.current.clear()
  }, [incident.id, cameraFeeds.map(feed => `${feed.key}:${feed.path}:${feed.exists}`).join('|'), media.path])

  useEffect(() => {
    if (!selectedFeed?.key) {
      return
    }

    setLoadedFeedKeys(previous => {
      if (previous.has(selectedFeed.key)) {
        return previous
      }

      const next = new Set(previous)
      next.add(selectedFeed.key)
      return next
    })
  }, [selectedFeed?.key])

  useEffect(() => {
    if (
      !seekRequest ||
      lastSeekNonceRef.current === seekRequest.nonce ||
      !Number.isFinite(seekRequest.time) ||
      seekRequest.time < 0
    ) {
      return
    }

    lastSeekNonceRef.current = seekRequest.nonce
    const safeTime = Math.max(0, seekRequest.time)
    syncAllVideosTo(safeTime, isPlayingRef.current)
  }, [seekRequest, syncAllVideosTo])

  useEffect(() => {
    if (!playbackRequest || lastPlaybackNonceRef.current === playbackRequest.nonce) {
      return
    }

    lastPlaybackNonceRef.current = playbackRequest.nonce
    handleSharedPlayToggle()
  }, [handleSharedPlayToggle, playbackRequest])

  useEffect(() => {
    if (!isPlaying || !canUseMultiCamera) {
      return
    }

    const intervalId = window.setInterval(() => {
      const masterVideo = getMasterVideo()
      if (!masterVideo || masterVideo.paused || masterVideo.readyState === 0) {
        return
      }

      syncSecondaryVideos(masterVideo.currentTime, { playing: true })
    }, SECONDARY_SYNC_INTERVAL_MS)

    return () => window.clearInterval(intervalId)
  }, [canUseMultiCamera, getMasterVideo, isPlaying, syncSecondaryVideos])

  const setVideoRef = useCallback(
    (key: string) => (node: HTMLVideoElement | null) => {
      if (node) {
        videoRefs.current.set(key, node)
      } else {
        videoRefs.current.delete(key)
      }
    },
    [],
  )

  const markFeedFailed = (feed: CameraFeed | null, errorMessage = 'Video element failed to load local camera feed.') => {
    const key = feed?.key || 'single-video'
    const failedPath = feed?.path || videoCandidates[0]?.path || media.path

    void logIncidentDiagnostic({
      incidentId: incidentActionId(incident),
      attemptedVideoPath: failedPath,
      errorMessage,
    })

    setFailedFeedKeys(previous => {
      const next = new Set(previous)
      next.add(key)
      return next
    })
    setLoadingFeedKeys(previous => {
      const next = new Set(previous)
      next.delete(key)
      return next
    })
  }

  const selectCameraFeed = (feed: CameraFeed) => {
    const previousMaster = getMasterVideo()
    const handoffTime = previousMaster?.currentTime ?? currentTime
    const shouldKeepPlaying = previousMaster ? !previousMaster.paused : isPlayingRef.current
    pendingCameraHandoffRef.current = {
      key: feed.key,
      time: Number.isFinite(handoffTime) ? Math.max(0, handoffTime) : 0,
      playing: shouldKeepPlaying,
    }

    setSelectedFeedKey(feed.key)
    setSelectedGridKey(feed.key)
    setLoadedFeedKeys(previous => {
      const next = new Set(previous)
      next.add(feed.key)
      return next
    })

    window.requestAnimationFrame(() => {
      const nextMaster = videoRefs.current.get(feed.key)
      if (!nextMaster || nextMaster.readyState === 0) {
        return
      }

      try {
        const targetDuration = Number.isFinite(nextMaster.duration) && nextMaster.duration > 0 ? nextMaster.duration : Number.POSITIVE_INFINITY
        if (Number.isFinite(handoffTime)) {
          nextMaster.currentTime = Math.min(Math.max(0, handoffTime), targetDuration)
        }

        if (shouldKeepPlaying) {
          void nextMaster.play().catch(() => {
            setIsPlaying(false)
          })
        }
        pendingCameraHandoffRef.current = null
      } catch {
        // Camera switching should never break the rest of the viewer.
      }
    })
  }

  const applyPendingCameraHandoff = (feed: CameraFeed, video: HTMLVideoElement) => {
    const handoff = pendingCameraHandoffRef.current
    if (!handoff || handoff.key !== feed.key) {
      return
    }

    try {
      const targetDuration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Number.POSITIVE_INFINITY
      video.currentTime = Math.min(handoff.time, targetDuration)

      if (handoff.playing) {
        void video.play().catch(() => {
          setIsPlaying(false)
        })
      } else if (!video.paused) {
        video.pause()
      }
    } catch {
      // Camera handoff is best-effort; loading failures are handled separately.
    } finally {
      pendingCameraHandoffRef.current = null
    }
  }

  const markFeedLoading = (key: string, loading: boolean) => {
    setLoadingFeedKeys(previous => {
      const next = new Set(previous)
      if (loading) {
        next.add(key)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  const handleMasterTimeUpdate = (event: SyntheticEvent<HTMLVideoElement>) => {
    const time = event.currentTarget.currentTime
    updateUiTime(time)
  }

  const renderUnavailable = (label: string, className = '', loadFailed = false) => (
    <div className={`grid min-h-[180px] place-items-center bg-[linear-gradient(145deg,#08090a,#020202)] text-center ${className}`}>
      <div className="px-5">
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-white/12" />
        <div className="text-[15px] font-semibold text-[var(--mimir-text)]">
          {loadFailed ? `Could not load ${label} camera` : `${label} unavailable`}
        </div>
        <p className="mt-2 text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
          The rest of the incident is still available.
        </p>
      </div>
    </div>
  )

  const renderFeedVideo = (feed: CameraFeed, options: { main?: boolean; muted?: boolean; className?: string }) => {
    const failed = failedFeedKeys.has(feed.key)
    const isMasterFeed = feed.key === masterVideoKey
    const shouldRenderSecondaryVideo = options.main || viewerMode !== 'grid' || GRID_SECONDARY_PLAYBACK_MODE === 'video'
    const shouldLoad =
      shouldRenderSecondaryVideo &&
      (options.main ||
        viewerMode === 'grid' ||
        !options.main ||
        loadedFeedKeys.has(feed.key) ||
        feed.key === selectedFeed?.key)
    const loading = loadingFeedKeys.has(feed.key)

    if (failed || !feed.path || !isAbsoluteLocalPath(feed.path) || !isVideoPath(feed.path) || feed.exists === false) {
      return renderUnavailable(feed.label, options.className, failed)
    }

    if (!shouldRenderSecondaryVideo) {
      return renderUnavailable(feed.label, options.className)
    }

    return (
      <div className={`relative overflow-hidden bg-black ${options.className || ''}`}>
        <video
          key={`${feed.key}-${feed.path}`}
          ref={setVideoRef(feed.key)}
          src={shouldLoad ? localFileSrc(feed.path) : undefined}
          preload={options.main || viewerMode === 'grid' ? 'auto' : 'metadata'}
          playsInline
          controls={isMasterFeed}
          muted={!isMasterFeed || Boolean(options.muted)}
          className="h-full w-full bg-black object-contain"
          onLoadedMetadata={event => {
            markFeedLoading(feed.key, false)
            const durationValue = event.currentTarget.duration
            if (isMasterFeed && Number.isFinite(durationValue) && durationValue > 0) {
              onDurationChange(durationValue)
            }
            if (isMasterFeed) {
              applyPendingCameraHandoff(feed, event.currentTarget)
              updateUiTime(event.currentTarget.currentTime, true)
            }
          }}
          onDurationChange={event => {
            const durationValue = event.currentTarget.duration
            if (isMasterFeed && Number.isFinite(durationValue) && durationValue > 0) {
              onDurationChange(durationValue)
            }
          }}
          onTimeUpdate={isMasterFeed ? handleMasterTimeUpdate : undefined}
          onSeeked={event => {
            if (!isMasterFeed) {
              return
            }

            syncSecondaryVideos(event.currentTarget.currentTime, {
              playing: !event.currentTarget.paused,
              force: true,
            })
            updateUiTime(event.currentTarget.currentTime, true)
          }}
          onPlay={event => {
            if (!isMasterFeed) {
              return
            }

            setIsPlaying(true)
            syncSecondaryVideos(event.currentTarget.currentTime, { playing: true, force: true })
          }}
          onPause={event => {
            if (!isMasterFeed) {
              return
            }

            setIsPlaying(false)
            syncSecondaryVideos(event.currentTarget.currentTime, { playing: false })
          }}
          onLoadStart={() => markFeedLoading(feed.key, true)}
          onLoadedData={() => markFeedLoading(feed.key, false)}
          onCanPlay={event => {
            markFeedLoading(feed.key, false)
            if (isMasterFeed) {
              applyPendingCameraHandoff(feed, event.currentTarget)
            }
          }}
          onError={() => markFeedFailed(feed)}
        />
        <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-1.5">
          <span className="rounded-full border border-white/10 bg-black/62 px-2.5 py-1 text-[11px] font-semibold text-white/78 backdrop-blur-md">
            {feed.label}
          </span>
          {feed.isPrimary && (
            <span className="rounded-full border border-white/10 bg-white/[0.095] px-2.5 py-1 text-[11px] font-semibold text-white/72 backdrop-blur-md">
              Best angle
            </span>
          )}
        </div>
        {loading && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center bg-black/28 backdrop-blur-[1px]">
            <span className="h-6 w-6 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="relative overflow-hidden rounded-[20px] bg-[radial-gradient(circle_at_50%_0%,rgba(255,255,255,0.055),transparent_34%),#020202] shadow-[inset_0_1px_0_rgba(255,255,255,0.035)]">
      {canUseMultiCamera && (
        <div className="border-b border-white/[0.07] bg-[linear-gradient(180deg,rgba(255,255,255,0.045),rgba(255,255,255,0.014))] px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {(['focus', 'grid'] as const).map(mode => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setViewerMode(mode)}
                  className={`h-8 rounded-lg px-3 text-[12px] font-semibold capitalize transition ${
                    viewerMode === mode
                      ? 'bg-white text-black'
                      : 'border border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)] hover:bg-white/[0.07] hover:text-[var(--mimir-text)]'
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-white/[0.045] px-2.5 py-1 text-[11px] text-[var(--mimir-text-subtle)]">
                {cameraFeeds.length} angles
              </span>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="mr-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">
              Camera
            </span>
            {cameraFeeds.map(feed => {
              const selected = selectedFeed?.key === feed.key
              const label = feed.isPrimary ? 'Best' : feed.label

              return (
                <button
                  key={feed.key}
                  type="button"
                  onClick={() => selectCameraFeed(feed)}
                  className={`h-8 rounded-full border px-3 text-[12px] font-semibold transition ${
                    selected
                      ? 'border-white/28 bg-white/[0.13] text-[var(--mimir-text)]'
                      : 'border-white/[0.08] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:bg-white/[0.06] hover:text-[var(--mimir-text)]'
                  }`}
                  title={feed.isPrimary ? `Best angle: ${feed.label}` : feed.label}
                >
                  {label}
                  {feed.isPrimary && <span className="ml-1 font-medium text-white/52">{feed.label}</span>}
                </button>
              )
            })}
            <span className="ml-auto hidden text-[11px] text-[var(--mimir-text-subtle)] md:inline">
              Showing {selectedFeedLabel}
            </span>
          </div>
        </div>
      )}

      {canUseMultiCamera && viewerMode === 'focus' && selectedFeed && (
        <div className="grid gap-3 p-3">
          {renderFeedVideo(selectedFeed, {
            main: true,
            muted: false,
            className: 'min-h-[420px] rounded-[18px] lg:min-h-[640px]',
          })}
          {secondaryFeeds.length > 0 && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
              {secondaryFeeds.map(feed => {
                const isSelected = selectedFeed.key === feed.key
                return (
                  <button
                    key={feed.key}
                    type="button"
                    onClick={() => {
                      selectCameraFeed(feed)
                    }}
                    className={`overflow-hidden rounded-[14px] border bg-black/45 text-left shadow-[0_12px_30px_rgba(0,0,0,0.18)] transition ${
                      isSelected
                        ? 'border-white/30 ring-2 ring-white/18'
                        : 'border-white/[0.055] hover:border-white/18 hover:bg-white/[0.025]'
                    }`}
                  >
                    {renderFeedVideo(feed, {
                      muted: true,
                      className: 'aspect-video min-h-0',
                    })}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}

      {canUseMultiCamera && viewerMode === 'grid' && (
        <div className={`grid gap-3 p-3 ${gridFeeds.length === 4 ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-3'}`}>
          {gridFeeds.map(feed => {
            const selected = (selectedGridKey || selectedFeed?.key) === feed.key
            return (
              <div
                key={feed.key}
                role="button"
                tabIndex={0}
                onClick={() => {
                  if (!selected) {
                    selectCameraFeed(feed)
                  }
                }}
                onKeyDown={event => {
                  if (!selected && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault()
                    selectCameraFeed(feed)
                  }
                }}
                className={`overflow-hidden rounded-[18px] border bg-black/45 text-left shadow-[0_18px_46px_rgba(0,0,0,0.22)] transition ${
                  selected ? 'border-white/28 ring-2 ring-white/14' : 'border-white/[0.055] hover:border-white/16'
                }`}
              >
                {renderFeedVideo(feed, {
                  main: feed.key === selectedFeed?.key,
                  muted: feed.key !== selectedFeed?.key,
                  className: 'aspect-video min-h-[240px]',
                })}
              </div>
            )
          })}
        </div>
      )}

      {showSingleVideo && (
        <video
          key={videoCandidates[0].path}
          ref={setVideoRef('single-video')}
          src={videoSrc}
          controls
          preload="metadata"
          playsInline
          className="max-h-[76vh] w-full bg-black object-contain"
          onLoadedMetadata={event => {
            onDurationChange(event.currentTarget.duration)
            onTimeUpdate(event.currentTarget.currentTime)
          }}
          onDurationChange={event => onDurationChange(event.currentTarget.duration)}
          onTimeUpdate={event => onTimeUpdate(event.currentTarget.currentTime)}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onError={() => markFeedFailed(null)}
        />
      )}

      {showImage && (
        <div className="w-full">
          {(showVideoFallbackMessage || showMissingVideoMessage) && (
            <div className="border-b border-white/10 bg-white/[0.035] px-5 py-3 text-[13px] text-[var(--mimir-text-muted)]">
              {showMissingVideoMessage
                ? 'Video file not found. Showing available evidence image instead.'
                : incident.contact_sheet
                ? 'Video could not be loaded. Showing contact sheet instead.'
                : 'Video could not be loaded. Showing available evidence image instead.'}
            </div>
          )}
          <img
            key={imagePath}
            src={imageSrc}
            alt=""
            className="max-h-[76vh] w-full object-contain"
            onError={() => setImageFailed(true)}
          />
        </div>
      )}

      {(media.mode === 'empty' || (!canUseMultiCamera && !showSingleVideo && !showImage)) && (
        <div className="grid min-h-[390px] place-items-center px-8 text-center lg:min-h-[640px]">
          <div>
            <div className="mx-auto mb-5 h-1 w-14 rounded-full bg-white/18" />
            <h3 className="text-[20px] font-semibold text-[var(--mimir-text)]">Video file not found</h3>
            <p className="mt-3 max-w-[360px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir could not find a playable local video for this incident. The incident details are still available.
            </p>
          </div>
        </div>
      )}

      {canUseMultiCamera && anyVideoFailed && (
        <div className="border-t border-white/[0.07] bg-white/[0.025] px-4 py-2 text-[12px] text-[var(--mimir-text-subtle)]">
          One camera feed could not be loaded. The remaining feeds are still available.
        </div>
      )}

      {!canUseMultiCamera && playableFeeds.length === 1 && media.mode === 'empty' && (
        <div className="grid min-h-[390px] place-items-center px-8 text-center lg:min-h-[640px]">
          <div className="mx-auto mb-5 h-1 w-14 rounded-full bg-white/18" />
          <h3 className="text-[20px] font-semibold text-[var(--mimir-text)]">Video file not found</h3>
          <p className="mt-3 max-w-[360px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
            Mimir could not find a playable local video for this incident. The incident details are still available.
          </p>
        </div>
      )}

    </div>
  )
}

function markerTime(marker: MimirTimelineMarker) {
  return typeof marker.time_sec === 'number' && Number.isFinite(marker.time_sec) ? marker.time_sec : null
}

function markerPosition(marker: MimirTimelineMarker, index: number, markerCount: number, duration: number) {
  const time = markerTime(marker)

  if (time !== null && duration > 0) {
    return Math.max(0, Math.min(100, (time / duration) * 100))
  }

  if (markerCount <= 1) {
    return 50
  }

  return Math.max(0, Math.min(100, (index / (markerCount - 1)) * 100))
}

function markerKey(marker: MimirTimelineMarker, index: number) {
  return `${marker.type || 'marker'}-${index}-${marker.time_sec ?? 'no-time'}-${marker.frame_index ?? 'no-frame'}`
}

function markerIsPrimaryMoment(marker: MimirTimelineMarker, incident: MimirIncident) {
  const primaryTime = finiteNumber(incident.primary_key_moment_sec)
  const markerValue = markerTime(marker)
  return primaryTime !== null && markerValue !== null && Math.abs(primaryTime - markerValue) <= 1
}

function markerPresentationPriority(marker: MimirTimelineMarker, incident: MimirIncident) {
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

function markerIsHighValue(marker: MimirTimelineMarker, incident: MimirIncident) {
  if (String(marker.type || '').toLowerCase() === 'user_corrected') {
    return true
  }
  const priority = markerPresentationPriority(marker, incident)
  return priority >= 80 || markerIsPrimaryMoment(marker, incident)
}

function markersAreNear(left: MimirTimelineMarker, right: MimirTimelineMarker, tolerance = 1) {
  const leftTime = markerTime(left)
  const rightTime = markerTime(right)
  return leftTime !== null && rightTime !== null && Math.abs(leftTime - rightTime) <= tolerance
}

function sameMarkerLabel(left: MimirTimelineMarker, right: MimirTimelineMarker, incident: MimirIncident) {
  return markerLabel(left, incident).toLowerCase() === markerLabel(right, incident).toLowerCase()
}

function dedupeMarkersByPriority(markers: MimirTimelineMarker[], incident: MimirIncident, options: { allMoments: boolean }) {
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

function presentationTimelineMarkers(markers: MimirTimelineMarker[], incident: MimirIncident, showAllMoments: boolean) {
  if (showAllMoments) {
    return dedupeMarkersByPriority(markers, incident, { allMoments: true })
  }

  const highValue = markers.filter(marker => markerIsHighValue(marker, incident))
  const betterThanReviewPoint = highValue.some(marker => {
    const label = markerLabel(marker, incident).toLowerCase()
    const type = String(marker.type || '').toLowerCase()
    return !label.includes('review point') && !type.includes('review')
  })
  const filtered = betterThanReviewPoint
    ? highValue.filter(marker => {
        const label = markerLabel(marker, incident).toLowerCase()
        const type = String(marker.type || '').toLowerCase()
        return !label.includes('review point') && !type.includes('review')
      })
    : highValue
  const candidates = filtered.length > 0 ? filtered : markers.slice(0, 1)

  return dedupeMarkersByPriority(candidates, incident, { allMoments: false }).slice(0, 3)
}

function IncidentTimelineMarkers({
  markers,
  incident,
  currentTime,
  duration,
  onSeek,
  onSetActualMoment,
}: {
  markers: MimirTimelineMarker[]
  incident: MimirIncident
  currentTime: number
  duration: number
  onSeek: (time: number) => void
  onSetActualMoment: (time: number) => void
}) {
  const [hoveredMarkerIndex, setHoveredMarkerIndex] = useState<number | null>(null)
  const [selectedMarkerIndex, setSelectedMarkerIndex] = useState<number | null>(null)
  const [showAllMoments, setShowAllMoments] = useState(false)
  const knownDuration = reviewTimelineDuration(incident, duration)
  const displayedMarkers = useMemo(
    () => presentationTimelineMarkers(markers, incident, showAllMoments),
    [incident, markers, showAllMoments],
  )
  const canSeekRail = Number.isFinite(knownDuration) && knownDuration > 0
  const effectiveDuration = canSeekRail ? knownDuration : 0
  const progressPercent =
    effectiveDuration > 0 ? Math.max(0, Math.min(100, (currentTime / effectiveDuration) * 100)) : 0
  const selectedMarker = selectedMarkerIndex === null ? null : displayedMarkers[selectedMarkerIndex]
  const hasHiddenMoments = markers.length > displayedMarkers.length
  const selectMarker = (index: number) => {
    const marker = displayedMarkers[index]
    setSelectedMarkerIndex(index)

    const time = markerTime(marker)
    if (time !== null) {
      onSeek(time)
    }
  }
  const selectAdjacentMarker = (direction: 'previous' | 'next') => {
    if (displayedMarkers.length === 0) {
      return
    }

    const currentIndex = selectedMarkerIndex ?? 0
    const nextIndex =
      direction === 'previous'
        ? Math.max(0, currentIndex - 1)
        : Math.min(displayedMarkers.length - 1, currentIndex + 1)

    selectMarker(nextIndex)
  }

  useEffect(() => {
    setHoveredMarkerIndex(null)
    setSelectedMarkerIndex(displayedMarkers.length > 0 ? 0 : null)
  }, [incident.id, displayedMarkers.length, showAllMoments])

  useEffect(() => {
    setShowAllMoments(false)
  }, [incident.id, incident.primary_key_moment_sec, incident.key_moment_version, markers.length])

  if (!canSeekRail) {
    return (
      <div className="rounded-2xl bg-white/[0.014] p-5">
        <div className="text-[13px] text-[var(--mimir-text-muted)]">Loading key moments...</div>
      </div>
    )
  }

  if (displayedMarkers.length === 0) {
    return (
      <div className="rounded-2xl bg-white/[0.014] p-5">
        <div className="text-[13px] text-[var(--mimir-text-muted)]">No key moments found.</div>
      </div>
    )
  }

  const handleRailClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!canSeekRail) {
      return
    }

    const bounds = event.currentTarget.getBoundingClientRect()
    if (!Number.isFinite(bounds.width) || bounds.width <= 0) {
      return
    }

    const ratio = (event.clientX - bounds.left) / bounds.width
    const seekTime = Math.max(0, Math.min(knownDuration, ratio * knownDuration))

    onSeek(seekTime)
  }

  return (
    <div className="rounded-2xl bg-black/12 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.018)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[12px] font-semibold text-[var(--mimir-text-subtle)]">
            {showAllMoments ? 'All moments' : 'Primary moments'}
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => onSetActualMoment(currentTime)}
            className="h-8 rounded-full border border-white/[0.06] bg-white/[0.018] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)]"
            title="Save the current video position as the actual moment"
          >
            Use current time
          </button>
          {displayedMarkers.length > 1 && (
            <>
              <button
                type="button"
                onClick={() => selectAdjacentMarker('previous')}
                disabled={selectedMarkerIndex === 0}
                className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-35"
              >
                Previous
              </button>
              <button
                type="button"
                onClick={() => selectAdjacentMarker('next')}
                disabled={selectedMarkerIndex === displayedMarkers.length - 1}
                className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-35"
              >
                Next
              </button>
            </>
          )}
          {hasHiddenMoments && (
            <button
              type="button"
              onClick={() => setShowAllMoments(value => !value)}
              className="h-8 rounded-full border border-white/[0.06] bg-white/[0.018] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)]"
            >
              {showAllMoments ? 'Show fewer' : 'Show all moments'}
            </button>
          )}
          <div className="flex items-center gap-2 rounded-full bg-white/[0.025] px-3 py-1 text-[12px] text-[var(--mimir-text-subtle)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--mimir-accent)]" />
            {formatTime(currentTime)} / {formatTime(effectiveDuration)}
          </div>
        </div>
      </div>

      <div
        role="presentation"
        onClick={handleRailClick}
        className={`relative mx-2 h-[138px] ${canSeekRail ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <div className="absolute left-0 right-0 top-[48px] h-px bg-white/[0.08]" />
        <div className="absolute left-0 right-0 top-[46px] h-3 rounded-full bg-black/28 shadow-[inset_0_1px_1px_rgba(0,0,0,0.34)]" />
        <div
          className="absolute left-0 top-[46px] h-3 rounded-full bg-[rgba(157,183,170,0.34)] transition-[width]"
          style={{ width: `${progressPercent}%` }}
        />
        <div
          className="absolute top-[34px] h-12 w-px bg-white/42"
          style={{ left: `${progressPercent}%` }}
        />
        {displayedMarkers.map((marker, index) => {
          const time = markerTime(marker)
          const position = markerPosition(marker, index, displayedMarkers.length, effectiveDuration)
          const isHovered = hoveredMarkerIndex === index
          const isSelected = selectedMarkerIndex === index
          const isTimed = time !== null

          return (
            <button
              key={markerKey(marker, index)}
              type="button"
              onMouseEnter={() => setHoveredMarkerIndex(index)}
              onFocus={() => setHoveredMarkerIndex(index)}
              onBlur={() => setHoveredMarkerIndex(null)}
              onClick={event => {
                event.stopPropagation()
                selectMarker(index)
              }}
              onMouseLeave={() => setHoveredMarkerIndex(null)}
              title={`${markerLabel(marker, incident)} - ${isTimed ? formatTime(time) : 'time unavailable'}${markerDescription(marker, incident) ? `\n${markerDescription(marker, incident)}` : ''}`}
              className="group absolute top-[22px] flex -translate-x-1/2 cursor-pointer flex-col items-center rounded-full outline-none"
              style={{ left: `${position}%` }}
              aria-label={`${markerLabel(marker, incident)} ${isTimed ? formatTime(time) : 'time unavailable'}`}
            >
              <span className="absolute left-1/2 top-9 h-6 w-px -translate-x-1/2 bg-white/[0.08]" />
              <span
                className={`relative z-[1] block rounded-full border transition duration-150 group-focus-visible:ring-2 group-focus-visible:ring-white/28 ${markerVisualClass(marker)} ${
                  isSelected
                    ? 'h-10 w-10 scale-110 ring-2 ring-white/24 brightness-110'
                    : isHovered
                      ? 'h-9 w-9 scale-110'
                      : String(marker.type || '').toLowerCase() === 'impact_contact'
                        ? 'h-8 w-8'
                        : 'h-7 w-7'
                }`}
              />
              <span
                className={`mt-7 max-w-[132px] truncate rounded-full border px-2.5 py-1 text-[11px] font-semibold shadow-[0_10px_26px_rgba(0,0,0,0.22)] transition ${
                  isSelected
                    ? 'border-white/20 bg-white/[0.12] text-[var(--mimir-text)]'
                    : 'border-white/[0.08] bg-black/26 text-[var(--mimir-text-muted)] group-hover:bg-white/[0.075] group-hover:text-[var(--mimir-text)]'
                }`}
              >
                {markerLabel(marker, incident)}
              </span>
              <span className="mt-1 text-[11px] font-medium text-[var(--mimir-text-subtle)]">
                {isTimed ? formatTime(time) : '--:--'}
              </span>
              <span className="sr-only">{markerLabel(marker, incident)}</span>

              {isHovered && (
                <span className="pointer-events-none absolute bottom-[108px] left-1/2 z-10 block w-56 -translate-x-1/2 rounded-lg bg-[var(--mimir-surface-soft)] p-3 text-left shadow-[0_18px_50px_rgba(0,0,0,0.42)]">
                  <span className="block text-[11px] font-semibold text-[var(--mimir-text-subtle)]">
                    {markerLabel(marker, incident)} - {time === null ? 'time unavailable' : formatTime(time)}
                  </span>
                  <span className="mt-2 block text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                    {markerDescription(marker, incident)}
                  </span>
                </span>
              )}
            </button>
          )
        })}
        <div className="absolute bottom-0 left-0 text-[11px] text-[var(--mimir-text-subtle)]">0:00</div>
        <div className="absolute bottom-0 right-0 text-[11px] text-[var(--mimir-text-subtle)]">{formatTime(knownDuration)}</div>
      </div>

      <div className="mt-3 rounded-xl bg-white/[0.018] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.015)]">
        {selectedMarker ? (
          <div className="relative flex flex-wrap items-start gap-4 pl-4">
            <div className={`absolute bottom-1 left-0 top-1 w-1 rounded-full ${markerAccentForMarker(selectedMarker)}`} />
            <div className="min-w-[74px]">
              <div className="text-[10px] font-medium text-[var(--mimir-text-subtle)]">Marker</div>
              <div className="mt-1 text-[18px] font-semibold text-[var(--mimir-text)]">
                {formatTimecode(markerTime(selectedMarker))}
              </div>
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[14px] font-semibold text-[var(--mimir-text)]">{markerLabel(selectedMarker, incident)}</span>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${severityClass(selectedMarker.severity)}`}>
                  {markerSeverityCopy(selectedMarker.severity)}
                </span>
              </div>
              <p className="mt-1.5 text-[12px] leading-5 text-[var(--mimir-text-muted)]">{markerDescription(selectedMarker, incident)}</p>
            </div>
          </div>
        ) : (
          <div className="text-[13px] leading-6 text-[var(--mimir-text-muted)]">Select a marker to inspect the moment.</div>
        )}
      </div>
    </div>
  )
}

function DetailsPanel({
  incident,
  session,
  severityResolution,
}: {
  incident: MimirIncident
  session?: MimirSession
  severityResolution: SeverityResolution
}) {
  const evidence = safeTextList(incident.evidence)
  const impactReasons = safeTextList(incident.impact_reasons)
  const contactReasons = safeTextList(incident.contact_reasons)
  const calmPersonNear = calmerPersonNearWording(incident)
  const supportedContact = hasSupportedContactEvidence(incident)
  const supportedImpact = hasSupportedImpactEvidence(incident)
  const showImpactReasons = impactReasons.length > 0 && (!calmPersonNear || supportedImpact)
  const showContactReasons = contactReasons.length > 0 && (!calmPersonNear || supportedContact)
  const displayEvidence = calmPersonNear ? ['No clear contact detected'] : evidence
  const local = localEvidence(incident)
  const ai = aiEvidence(incident)
  const debug = classificationDebug(incident)
  const aiEvidenceItems = safeTextList(ai.evidence)
  const aiConcernItems = safeTextList(ai.concerns).concat(safeTextList(incident.ai_concerns))
  const aiWarning = aiQualityWarning(incident)
  const resolverReasons = severityReasonList(incident)
  const severityCapReason = safeText(debug.severity_cap_reason || incident.severity_cap_reason, '')
  const severityFloorReason = safeText(debug.severity_floor_reason, '')
  const reviewBadgeTone = aiReviewed(incident)
    ? 'border-sky-200/18 bg-sky-300/10 text-sky-100/88'
    : 'border-white/[0.07] bg-white/[0.035] text-[var(--mimir-text-muted)]'
  const severity = severityResolution.displaySeverity
  const reviewCopy =
    severity === 'IMPORTANT'
      ? 'Possible impact/contact detected.'
      : severity === 'REVIEW'
        ? 'Uncertain activity worth checking.'
        : 'No concerning evidence found.'
  const keyReasons = [
    ...resolverReasons,
    ...displayEvidence,
    ...(showImpactReasons ? impactReasons : []),
    ...(showContactReasons ? contactReasons : []),
    severityCapReason,
    severityFloorReason,
  ].filter(Boolean)
  const localSummary = [
    `Impact: ${calmPersonNear && !supportedImpact ? 'No clear impact detected' : incident.impact_level || evidenceMetricValue(local, 'impact_level')}`,
    `Contact: ${contactLevelCopy(incident)}`,
    `Motion: ${evidenceMetricValue(local, 'max_motion_score')}`,
    `Person detected: ${evidenceMetricValue(local, 'person_detected')}`,
    `Vehicle detected: ${evidenceMetricValue(local, 'vehicle_detected')}`,
  ].filter(item => !item.endsWith('Not provided') && !item.endsWith(''))
  const filePaths = {
    video_path: incident.video_path || null,
    source_video: incident.source_video || null,
    original_source_video: incident.original_source_video || null,
    library_video_path: incident.library_video_path || null,
    trash_video_path: incident.trash_video_path || null,
    thumbnail: incident.thumbnail || null,
    hero_thumbnail: incident.hero_thumbnail || null,
    contact_sheet: incident.contact_sheet || null,
  }
  const backendMetadata = {
    schema_version: session?.schema_version,
    scanner_version: session?.scanner_version,
    core_version: session?.core_version,
    core_build_id: session?.core_build_id,
    backend_runtime: session?.backend_runtime,
    session_created_at: session?.session_created_at,
    output_path: session?.output_path,
    feature_flags: session?.feature_flags,
  }

  return (
    <aside className="mb-3 rounded-2xl border border-white/[0.04] bg-white/[0.016] p-4 shadow-[0_12px_34px_rgba(0,0,0,0.14)]">
      <div className="mb-4 flex flex-wrap gap-2">
        <span className={`rounded-full border px-3 py-1 text-[12px] font-semibold ${severityClass(severityResolution.displaySeverity)}`}>
          {severityCopy(severityResolution.displaySeverity)}
        </span>
        {severityResolution.isManualOverride && (
          <span className="rounded-full border border-white/[0.07] bg-white/[0.035] px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
            Changed by you
          </span>
        )}
        <span className={`rounded-full border px-3 py-1 text-[12px] font-medium ${reviewBadgeTone}`}>
          {reviewBadgeCopy(incident)}
        </span>
      </div>

      <div className="rounded-xl bg-black/12 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.018)]">
        <div className="text-[12px] font-semibold text-[var(--mimir-text-subtle)]">
          Mimir review
        </div>
        <p className="mt-3 text-[15px] font-medium leading-6 text-[var(--mimir-text)]">
          {reviewCopy}
        </p>
        <p className="mt-1.5 text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
          Based on local evidence.
        </p>
        {importantNotAppliedNote(incident) && (
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.026] px-3 py-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
            Important was not applied because no contact or impact evidence was found.
          </div>
        )}
      </div>

      <details className="mt-3 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[13px] font-semibold text-[var(--mimir-text)]">
          Why?
        </summary>
        <div className="mt-4 grid gap-4">
          {keyReasons.length > 0 && (
            <div>
              <div className="mb-2 text-[12px] font-medium text-[var(--mimir-text-subtle)]">
                Key reasons
              </div>
              <ul className="space-y-2">
                {keyReasons.slice(0, 8).map((item, index) => (
                  <li key={`${incident.id}-why-${index}`} className="flex gap-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
                    <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-white/35" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {localSummary.length > 0 && (
            <div className="rounded-lg bg-white/[0.012] px-3">
              <DetailMetric label="Mimir result" value={severityCopy(severityResolution.mimirSeverity)} />
              <DetailMetric label="Local evidence" value={localSummary.join(' / ')} />
            </div>
          )}

          {experimentalAiUsed(incident) && (
            <div className="rounded-lg border border-sky-200/10 bg-sky-300/[0.035] p-3">
              <div className="text-[12px] font-semibold text-sky-50/85">AI second opinion</div>
              <div className="mt-2 grid gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                <div>Model: {aiModelName(incident) || 'Not provided'}</div>
                {aiSceneType(incident) && <div>Scene: {aiSceneType(incident)}</div>}
                <div>Suggested severity: {aiRecommendedSeverity(incident)}</div>
                <div>Confidence: {aiConfidenceCopy(incident)}</div>
                {aiWarning && <div className="text-amber-50/78">{aiWarning}</div>}
              </div>
              {aiEvidenceItems.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {aiEvidenceItems.map((item, index) => (
                    <li key={`${incident.id}-ai-evidence-${index}`} className="flex gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-sky-200/45" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
              {aiConcernItems.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {aiConcernItems.map((item, index) => (
                    <li key={`${incident.id}-ai-concern-${index}`} className="flex gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-200/45" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </details>

      <details className="mt-3 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[13px] font-medium text-[var(--mimir-text-muted)]">
          Technical details
        </summary>
        <div className="mt-4 grid gap-3">
          <DetailMetric label="Incident ID" value={safeText(incident.id, 'Not provided')} />
          <DetailMetric label="Event type" value={eventDisplayTitle(incident)} />
          <DetailMetric label="Mimir result" value={severityCopy(severityResolution.mimirSeverity)} />
          <DetailMetric label="Displayed status" value={severityCopy(severityResolution.displaySeverity)} />
          <DetailMetric label="Manual override" value={yesNo(severityResolution.isManualOverride)} />
          <DetailMetric label="Score" value={formatNumber(incident.score)} />
          <DetailMetric label="Persons" value={formatNumber(incident.persons)} />
          <DetailMetric label="Vehicles" value={formatNumber(incident.vehicles)} />
          <DetailMetric label="Active frames" value={formatNumber(incident.active_frames)} />
          <DetailMetric label="Motion score" value={formatNumber(incident.max_motion_score)} />
          <DetailMetric label="Impact score" value={formatNumber(incident.impact_score)} />
          <DetailMetric
            label="Source event timestamp"
            value={formatDateTime(sourceEventTimestamp(incident)) || 'Not provided'}
          />
          <TechnicalJsonBlock label="Backend metadata" value={backendMetadata} />
          <TechnicalJsonBlock label="File paths" value={filePaths} />
          <TechnicalJsonBlock label="Classification debug" value={incident.classification_debug} />
          <TechnicalJsonBlock label="Local evidence JSON" value={incident.local_evidence ?? incident.local_evidence_summary} />
          {experimentalAiUsed(incident) && (
            <>
              <DetailMetric label="AI parse error" value={yesNo(incident.ai_parse_error)} />
              <DetailMetric label="AI skipped reason" value={safeText(incident.ai_review_skipped_reason, 'Not provided')} />
              <TechnicalJsonBlock label="AI raw result" value={incident.ai_evidence_review ?? incident.ai_raw_response ?? incident.ai_evidence} />
            </>
          )}
        </div>
      </details>
    </aside>
  )
}

function AiFeedbackPanel({
  selectedFeedback,
  notes,
  includeVideo,
  busy,
  message,
  error,
  onSelectFeedback,
  onNotesChange,
  onIncludeVideoChange,
  onSubmit,
}: {
  selectedFeedback: AiFeedbackChoice | ''
  notes: string
  includeVideo: boolean
  busy: boolean
  message: string
  error: string
  onSelectFeedback: (value: AiFeedbackChoice) => void
  onNotesChange: (value: string) => void
  onIncludeVideoChange: (value: boolean) => void
  onSubmit: () => void
}) {
  return (
    <div className="mt-4 rounded-xl bg-black/10 p-3.5">
      <div className="mb-3">
        <div className="text-[12px] font-semibold text-[var(--mimir-text)]">AI feedback</div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {feedbackChoices.map(choice => (
          <button
            key={choice}
            type="button"
            onClick={() => onSelectFeedback(choice)}
            disabled={busy}
            className={`min-h-9 rounded-lg border px-2.5 py-2 text-[11px] font-semibold transition disabled:cursor-wait disabled:opacity-60 ${
              selectedFeedback === choice
                ? 'border-white/24 bg-white/[0.095] text-[var(--mimir-text)]'
                : 'border-white/[0.07] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:bg-white/[0.055] hover:text-[var(--mimir-text)]'
            }`}
          >
            {choice}
          </button>
        ))}
      </div>

      <textarea
        value={notes}
        onChange={event => onNotesChange(event.target.value)}
        disabled={busy}
        rows={3}
        className="mt-3 w-full resize-none rounded-lg border border-white/[0.08] bg-black/18 p-3 text-[13px] leading-5 text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:border-white/18 disabled:cursor-wait disabled:opacity-60"
        placeholder="Notes"
      />

      <label className="mt-3 flex items-center gap-2 text-[12px] text-[var(--mimir-text-muted)]">
        <input
          type="checkbox"
          checked={includeVideo}
          onChange={event => onIncludeVideoChange(event.target.checked)}
          disabled={busy}
          className="h-4 w-4 accent-white disabled:cursor-wait"
        />
        Include video clip
      </label>

      <button
        type="button"
        onClick={onSubmit}
        disabled={busy || !selectedFeedback}
        className="mt-3 h-9 w-full rounded-lg bg-[var(--mimir-text)] px-3 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-55"
      >
        <span className="inline-flex items-center gap-2">
          {busy && <SmallSpinner />}
          Save feedback
        </span>
      </button>

      {message && (
        <div className="mt-3 rounded-lg border border-white/[0.075] bg-white/[0.035] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          {message}
        </div>
      )}
      {error && (
        <div className="mt-3 rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/86">
          {error}
        </div>
      )}
    </div>
  )
}

function TrainingContributionPanel({ incident, session }: { incident: MimirIncident; session?: MimirSession }) {
  const [recordedBy, setRecordedBy] = useState('')
  const [rightsBasis, setRightsBasis] = useState<'owned' | 'explicit_permission' | 'public_license'>('owned')
  const [permissionReference, setPermissionReference] = useState('')
  const [permissionRecord, setPermissionRecord] = useState('')
  const [rightsConfirmed, setRightsConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setRecordedBy('')
    setRightsBasis('owned')
    setPermissionReference('')
    setPermissionRecord('')
    setRightsConfirmed(false)
    setMessage('')
    setError('')
  }, [incident.id])

  const choosePermissionRecord = async () => {
    const selected = await openDialog({ multiple: false, directory: false, title: 'Choose permission record' })
    if (typeof selected === 'string') {
      setPermissionRecord(selected)
    }
  }

  const exportPackage = async () => {
    if (!rightsConfirmed || !recordedBy.trim() || !permissionReference.trim()) {
      setError('Confirm your rights and complete the consent details first.')
      return
    }
    const suggestedName = `${safeText(incident.source_stem, incidentActionId(incident))}.mimir-dataset.age`
      .replace(/[<>:"/\\|?*]+/g, '_')
    const destination = await saveDialog({
      title: 'Export encrypted Mimir training package',
      defaultPath: suggestedName,
      filters: [{ name: 'Mimir encrypted dataset', extensions: ['age'] }],
    })
    if (!destination) {
      return
    }
    const outputPath = destination.toLowerCase().endsWith('.mimir-dataset.age')
      ? destination
      : destination.toLowerCase().endsWith('.age')
        ? destination.slice(0, -4) + '.mimir-dataset.age'
        : destination + '.mimir-dataset.age'
    setBusy(true)
    setError('')
    setMessage('Encrypting selected footage locally...')
    try {
      const result = await invoke<TrainingContributionResult>('export_training_contribution', {
        sessionPath: session?.session_archive_path || session?.output_path || null,
        incidentId: incidentActionId(incident),
        outputPath,
        recordedBy: recordedBy.trim(),
        rightsBasis,
        permissionReference: permissionReference.trim(),
        independentPermissionRecord: permissionRecord || null,
      })
      setMessage(result.message)
      setRightsConfirmed(false)
    } catch (value) {
      setMessage('')
      setError(actionErrorMessage(value))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 grid gap-3">
      <p className="text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        Export this incident only after confirming you own the footage or have permission to use it for model development. Nothing uploads automatically.
      </p>
      <input
        value={recordedBy}
        onChange={event => setRecordedBy(event.target.value)}
        placeholder="Your name"
        className="h-10 rounded-lg border border-white/[0.08] bg-black/18 px-3 text-[13px] text-[var(--mimir-text)] outline-none focus:border-white/18"
      />
      <select
        value={rightsBasis}
        onChange={event => setRightsBasis(event.target.value as typeof rightsBasis)}
        className="h-10 rounded-lg border border-white/[0.08] bg-[var(--mimir-bg-depth)] px-3 text-[13px] text-[var(--mimir-text)] outline-none focus:border-white/18"
      >
        <option value="owned">I recorded and own this footage</option>
        <option value="explicit_permission">I have explicit permission</option>
        <option value="public_license">A public license permits this use</option>
      </select>
      <input
        value={permissionReference}
        onChange={event => setPermissionReference(event.target.value)}
        placeholder="Ownership, permission, or license reference"
        className="h-10 rounded-lg border border-white/[0.08] bg-black/18 px-3 text-[13px] text-[var(--mimir-text)] outline-none focus:border-white/18"
      />
      <button
        type="button"
        onClick={() => void choosePermissionRecord()}
        className="h-9 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] hover:bg-white/[0.05] hover:text-[var(--mimir-text)]"
      >
        {permissionRecord ? 'Permission record selected' : 'Attach permission record (optional)'}
      </button>
      <label className="flex items-start gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        <input
          type="checkbox"
          checked={rightsConfirmed}
          onChange={event => setRightsConfirmed(event.target.checked)}
          className="mt-0.5 h-4 w-4 accent-white"
        />
        I confirm these rights apply to this selected incident and its grouped camera angles.
      </label>
      <button
        type="button"
        onClick={() => void exportPackage()}
        disabled={busy || !rightsConfirmed}
        className="h-10 rounded-lg bg-[var(--mimir-text)] px-3 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="inline-flex items-center gap-2">{busy && <SmallSpinner />}Export encrypted package</span>
      </button>
      {message && <div className="text-[12px] leading-5 text-[var(--mimir-text-muted)]">{message}</div>}
      {error && <div className="text-[12px] leading-5 text-red-100/85">{error}</div>}
    </div>
  )
}

function ReviewActionsPanel({
  incident,
  session,
  currentSeverity,
  mimirSeverity,
  isManualOverride,
  busyAction,
  actionMessage,
  actionError,
  actionDetails,
  feedbackChoice,
  feedbackNotes,
  feedbackIncludeVideo,
  feedbackMessage,
  feedbackError,
  noteDraft,
  isEditingNote,
  onFeedbackChoiceChange,
  onFeedbackNotesChange,
  onFeedbackIncludeVideoChange,
  onSubmitFeedback,
  onNoteDraftChange,
  onEditNote,
  onCancelNote,
  onSaveNote,
  onSetStatus,
  onMoveToLibrary,
  onConfirmDelete,
  onRestoreFromTrash,
  onOpenFiles,
}: {
  incident: MimirIncident
  session?: MimirSession
  currentSeverity: SeverityGroup
  mimirSeverity: SeverityGroup
  isManualOverride: boolean
  busyAction: IncidentAction | null
  actionMessage: string
  actionError: string
  actionDetails: string
  feedbackChoice: AiFeedbackChoice | ''
  feedbackNotes: string
  feedbackIncludeVideo: boolean
  feedbackMessage: string
  feedbackError: string
  noteDraft: string
  isEditingNote: boolean
  onFeedbackChoiceChange: (value: AiFeedbackChoice) => void
  onFeedbackNotesChange: (value: string) => void
  onFeedbackIncludeVideoChange: (value: boolean) => void
  onSubmitFeedback: () => void
  onNoteDraftChange: (value: string) => void
  onEditNote: () => void
  onCancelNote: () => void
  onSaveNote: () => void
  onSetStatus: (status: SeverityGroup) => void
  onMoveToLibrary: () => void
  onConfirmDelete: () => void
  onRestoreFromTrash: () => void
  onOpenFiles: () => void
}) {
  const disabled = busyAction !== null
  const [showActionDetails, setShowActionDetails] = useState(false)
  const currentStorageState = storageState(incident)
  const storageBadgeTone =
    currentStorageState.includes('Trash')
      ? 'border-red-300/20 bg-red-500/10 text-red-100/85'
      : currentStorageState.includes('Library')
        ? 'border-emerald-300/18 bg-emerald-400/10 text-emerald-100/85'
        : currentStorageState === 'Missing file'
          ? 'border-amber-300/20 bg-amber-400/10 text-amber-100/85'
          : 'border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)]'

  return (
    <section className="rounded-2xl border border-white/[0.04] bg-white/[0.016] p-4 shadow-[0_12px_34px_rgba(0,0,0,0.14)]">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-[12px] font-semibold text-[var(--mimir-text-subtle)]">
            Status
          </div>
          {isManualOverride && (
            <div className="mt-1 text-[11px] font-medium text-[var(--mimir-text-subtle)]">
              Changed by you
            </div>
          )}
        </div>
        <span className={`rounded-full border px-3 py-1 text-[11px] font-semibold ${storageBadgeTone}`}>
          {currentStorageState === 'Mimir Library'
            ? 'In Mimir Library'
            : currentStorageState === 'Mimir Trash'
              ? 'In Mimir Trash'
              : currentStorageState}
        </span>
      </div>

      <div>
        <div className="grid grid-cols-3 gap-2">
          {(['IGNORE', 'REVIEW', 'IMPORTANT'] as const).map(status => {
            const actionKey = `set_status_${status}` as IncidentAction
            return (
              <button
                key={status}
                onClick={() => onSetStatus(status)}
                disabled={disabled || currentSeverity === status}
                className={`h-10 rounded-lg border px-3 text-[12px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${actionButtonTone(
                  status,
                  currentSeverity === status,
                )}`}
              >
                <span className="inline-flex items-center gap-2">
                  {busyAction === actionKey && <SmallSpinner />}
                  {severityCopy(status)}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-4 grid gap-2">
        {isManualOverride && (
          <div className="rounded-lg bg-white/[0.02] px-3 py-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
            Mimir result: {severityCopy(mimirSeverity)}
          </div>
        )}
        <button
          onClick={onOpenFiles}
          disabled={disabled}
          className="h-10 rounded-lg border border-white/[0.075] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-55"
        >
          Files
        </button>
        {incident.user_deleted || currentStorageState.includes('Trash') ? (
          <button
            onClick={onRestoreFromTrash}
            disabled={disabled}
            className="h-10 rounded-lg border border-white/[0.085] bg-white/[0.035] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.065] disabled:cursor-not-allowed disabled:opacity-55"
          >
            <span className="inline-flex items-center gap-2">
              {busyAction === 'restore_from_trash' && <SmallSpinner />}
              Restore from Mimir Trash
            </span>
          </button>
        ) : <button
          onClick={onMoveToLibrary}
          disabled={disabled || incident.user_deleted}
          className="h-10 rounded-lg border border-white/[0.085] bg-white/[0.035] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.065] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'move_to_library' && <SmallSpinner />}
            Move to Mimir Library
          </span>
        </button>}
        {!incident.user_deleted && !currentStorageState.includes('Trash') && <button
          onClick={onConfirmDelete}
          disabled={disabled || incident.user_deleted}
          className="h-10 rounded-lg border border-red-300/14 bg-red-500/[0.045] px-3 text-[12px] font-semibold text-red-100/82 transition hover:bg-red-500/[0.075] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'move_to_trash' && <SmallSpinner />}
            Move to Mimir Trash
          </span>
        </button>}
      </div>

      <details className="mt-4 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]">
          Feedback
        </summary>
        <AiFeedbackPanel
          selectedFeedback={feedbackChoice}
          notes={feedbackNotes}
          includeVideo={feedbackIncludeVideo}
          busy={busyAction === 'save_feedback'}
          message={feedbackMessage}
          error={feedbackError}
          onSelectFeedback={onFeedbackChoiceChange}
          onNotesChange={onFeedbackNotesChange}
          onIncludeVideoChange={onFeedbackIncludeVideoChange}
          onSubmit={onSubmitFeedback}
        />
      </details>

      <details className="mt-3 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]">
          Export for Mimir training
        </summary>
        <TrainingContributionPanel incident={incident} session={session} />
      </details>

      <div className="mt-4 rounded-xl bg-black/10 p-3.5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="text-[12px] font-semibold text-[var(--mimir-text)]">Note</div>
          <button
            onClick={onEditNote}
            disabled={disabled}
            className="h-8 rounded-lg bg-white/[0.035] px-2.5 text-[11px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.065] hover:text-[var(--mimir-text)] disabled:cursor-wait disabled:opacity-60"
            title="Edit note"
          >
            Edit
          </button>
        </div>
        {isEditingNote ? (
          <div>
            <textarea
              value={noteDraft}
              onChange={event => onNoteDraftChange(event.target.value)}
              rows={4}
              className="w-full resize-none rounded-lg border border-white/[0.08] bg-black/18 p-3 text-[13px] leading-5 text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:border-white/18"
              placeholder="Add your review note..."
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={onCancelNote}
                disabled={disabled}
                className="h-9 rounded-lg bg-white/[0.035] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={onSaveNote}
                disabled={disabled}
                className="h-9 rounded-lg bg-[var(--mimir-text)] px-3 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-wait disabled:opacity-60"
              >
                <span className="inline-flex items-center gap-2">
                  {busyAction === 'save_note' && <SmallSpinner />}
                  Save note
                </span>
              </button>
            </div>
          </div>
        ) : (
          <p className="text-[13px] leading-6 text-[var(--mimir-text-muted)]">
            {incident.user_note || 'No note yet.'}
          </p>
        )}
      </div>

      {actionMessage && (
        <div className="mt-4 rounded-lg border border-white/[0.075] bg-white/[0.035] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          <span className="inline-flex items-center gap-2">
            {(busyAction === 'move_to_library' || busyAction === 'move_to_trash') && <SmallSpinner />}
            {actionMessage}
          </span>
        </div>
      )}
      {actionError && (
        <div className="mt-4 rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/86">
          <div>{actionError}</div>
          {actionDetails && (
            <button
              type="button"
              onClick={() => setShowActionDetails(value => !value)}
              className="mt-2 rounded-md border border-red-100/18 bg-black/18 px-2.5 py-1 text-[11px] font-semibold text-red-50/86 transition hover:bg-black/30"
            >
              {showActionDetails ? 'Hide details' : 'Show details'}
            </button>
          )}
          {showActionDetails && actionDetails && (
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-black/30 p-3 text-[11px] leading-4 text-red-50/75">
              {actionDetails}
            </pre>
          )}
        </div>
      )}
    </section>
  )
}

export function IncidentViewerScreen({
  incident,
  session,
  severityResolution,
  onBack,
  onReloadSession,
  onIncidentUpdated,
  onManualStatusChange,
}: IncidentViewerScreenProps) {
  const title = eventDisplayTitle(incident)
  const timestamp = formatDateTime(sourceEventTimestamp(incident))
  const sourceLabel =
    sourceFilename(cleanPath(incident.source_video) || cleanPath(incident.original_source_video) || cleanPath(incident.video_path)) ||
    'Source filename not provided'
  const isGenericBestEffort = incident.filename_timestamp_detected === false
  const attemptedVideoPath = attemptedVideoPathForIncident(incident)
  const viewerRef = useRef<HTMLElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [seekRequest, setSeekRequest] = useState<ViewerSeekRequest | null>(null)
  const [playbackRequest, setPlaybackRequest] = useState<ViewerPlaybackRequest | null>(null)
  const [busyAction, setBusyAction] = useState<IncidentAction | null>(null)
  const [actionMessage, setActionMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [actionDetails, setActionDetails] = useState('')
  const [showLibraryConfirm, setShowLibraryConfirm] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showFilesDrawer, setShowFilesDrawer] = useState(false)
  const [isEditingNote, setIsEditingNote] = useState(false)
  const [noteDraft, setNoteDraft] = useState(incident.user_note ?? '')
  const [feedbackChoice, setFeedbackChoice] = useState<AiFeedbackChoice | ''>('')
  const [feedbackNotes, setFeedbackNotes] = useState('')
  const [feedbackIncludeVideo, setFeedbackIncludeVideo] = useState(false)
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [feedbackError, setFeedbackError] = useState('')
  const [showKeyMoments, setShowKeyMoments] = useState(true)
  const markers = useMemo(() => deriveReviewTimelineMarkers(incident, duration), [incident, duration])

  useEffect(() => {
    setCurrentTime(0)
    setDuration(0)
    setSeekRequest(null)
    setPlaybackRequest(null)
    setActionMessage('')
    setActionError('')
    setActionDetails('')
    setShowLibraryConfirm(false)
    setShowDeleteConfirm(false)
    setShowFilesDrawer(false)
    setIsEditingNote(false)
    setNoteDraft(incident.user_note ?? '')
    setFeedbackChoice('')
    setFeedbackNotes('')
    setFeedbackIncludeVideo(false)
    setFeedbackMessage('')
    setFeedbackError('')
    setShowKeyMoments(true)
    viewerRef.current?.focus()
  }, [incident.id])

  const refreshCurrentIncident = async (fallbackMessage: string) => {
    const refreshedSession = await onReloadSession()
    const refreshedIncident = refreshedSession ? findIncident(refreshedSession, incident) : null

    if (refreshedIncident) {
      onIncidentUpdated(refreshedIncident)
    }

    setActionMessage(fallbackMessage)
  }

  const updateManualStatus = (status: SeverityGroup) => {
    onManualStatusChange(status)
    setActionError('')
    setActionDetails('')
    setActionMessage(`Status changed to ${severityCopy(status)}.`)
  }

  const runCoreV2StorageAction = async (action: 'move_to_library' | 'move_to_trash' | 'restore_from_trash') => {
    const busyKey: IncidentAction = action
    const successMessage = action === 'move_to_library'
      ? 'Moved to Mimir Library'
      : action === 'restore_from_trash'
        ? 'Restored from Mimir Trash'
        : 'Moved to Mimir Trash'

    setBusyAction(busyKey)
    setActionError('')
    setActionDetails('')
    setActionMessage('Moving clips...')

    try {
      const result = await invoke<StorageActionResult>('run_core_v2_storage_action', {
        incidentId: incidentActionId(incident),
        action,
      })
      const report = parseStorageActionReport(result.report_json)
      const status = storageActionStatus(report)
      const details = storageActionDetails(result.report_json, result)

      await refreshCurrentIncident(status.partial || status.failed ? '' : successMessage)

      if (status.partial) {
        setActionMessage('')
        setActionError('The file action could not finish cleanly. Mimir kept the recovery details below.')
        setActionDetails(details)
      } else if (status.failed || !result.ok) {
        setActionMessage('')
        setActionError(result.message || 'Mimir could not complete this file action.')
        setActionDetails(details)
      } else {
        setActionError('')
        setActionDetails('')
        setActionMessage(successMessage)
      }
    } catch (error) {
      setActionMessage('')
      setActionError(actionErrorMessage(error))
      setActionDetails('')
    } finally {
      setBusyAction(null)
    }
  }

  const saveNote = async () => {
    setBusyAction('save_note')
    setActionError('')
    setActionDetails('')
    setActionMessage('')

    try {
      const result = await invoke<ClipActionResult>('save_incident_note', {
        incidentId: incidentActionId(incident),
        note: noteDraft,
      })

      await refreshCurrentIncident(result.message || 'Note saved.')
      setIsEditingNote(false)
    } catch (error) {
      setActionError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const saveActualMoment = async (timeSec: number) => {
    setBusyAction('save_key_moment')
    setActionError('')
    setActionDetails('')
    try {
      const result = await invoke<ClipActionResult>('save_key_moment_correction', {
        incidentId: incidentActionId(incident),
        timeSec,
      })
      await refreshCurrentIncident(result.message || 'Actual moment saved.')
    } catch (error) {
      setActionMessage('')
      setActionError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const saveFeedback = async () => {
    if (!feedbackChoice) {
      setFeedbackError('Choose a feedback label first.')
      return
    }

    setBusyAction('save_feedback')
    setFeedbackError('')
    setFeedbackMessage('')
    setActionError('')
    setActionDetails('')

    try {
      const videoPath = currentVideoPath(incident)
      const result = await invoke<IncidentFeedbackResult>('save_incident_feedback', {
        feedback: incidentFeedbackPayload(incident, feedbackChoice, feedbackNotes, feedbackIncludeVideo, session),
        includeVideo: feedbackIncludeVideo,
        videoPath: feedbackIncludeVideo ? videoPath : null,
      })

      setFeedbackMessage(result.message || `Feedback saved to ${result.feedback_folder}`)
      setFeedbackNotes('')
      setFeedbackIncludeVideo(false)
    } catch (error) {
      setFeedbackError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const handleSeek = (time: number) => {
    if (!Number.isFinite(time) || time < 0) {
      return
    }

    const safeTime = Math.max(0, time)
    setCurrentTime(safeTime)
    setSeekRequest({ time: safeTime, nonce: Date.now() })
  }

  const seekAdjacentMarker = (direction: 'previous' | 'next') => {
    const markersWithTime = timedMarkers(markers)

    if (markersWithTime.length === 0) {
      const offset = direction === 'previous' ? -5 : 5
      handleSeek(Math.max(0, currentTime + offset))
      return
    }

    const target =
      direction === 'previous'
        ? [...markersWithTime].reverse().find(marker => (markerTime(marker) ?? 0) < currentTime - 0.25) || markersWithTime[0]
        : markersWithTime.find(marker => (markerTime(marker) ?? 0) > currentTime + 0.25) || markersWithTime[markersWithTime.length - 1]

    const targetTime = markerTime(target)
    if (targetTime !== null) {
      handleSeek(targetTime)
    }
  }

  const openContainingFolder = async (path: string, fallbackMessage: string) => {
    if (!path) {
      setActionError(fallbackMessage)
      return
    }

    setActionError('')

    try {
      await invoke<void>('open_containing_folder', { path })
    } catch (error) {
      setActionError(actionErrorMessage(error))
    }
  }

  const openMimirStorageFolder = async (kind: 'library' | 'trash', path?: string) => {
    setActionError('')

    try {
      if (path) {
        await invoke<void>('open_containing_folder', { path })
        return
      }

      await invoke<void>('open_mimir_storage_folder', { kind })
    } catch (error) {
      setActionError(actionErrorMessage(error))
    }
  }

  const openViewerFileAction = (action: ViewerFileAction) => {
    if (action === 'original') {
      void openContainingFolder(originalVideoPath(incident), 'Original source path is not available.')
      return
    }

    if (action === 'current') {
      void openContainingFolder(currentVideoPath(incident), 'Current video path is not available.')
      return
    }

    if (action === 'library') {
      void openMimirStorageFolder('library', cleanPath(incident.library_video_path))
      return
    }

    void openMimirStorageFolder('trash', incident.trash_video_path)
  }

  const handleViewerKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (isTextInputElement(event.target) || busyAction !== null || showLibraryConfirm || showDeleteConfirm || showFilesDrawer) {
      return
    }

    const key = event.key.toLowerCase()
    const currentSeverity = severityResolution.displaySeverity

    if (event.code === 'Space') {
      event.preventDefault()
      setPlaybackRequest({ nonce: Date.now() })
      return
    }

    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      seekAdjacentMarker('previous')
      return
    }

    if (event.key === 'ArrowRight') {
      event.preventDefault()
      seekAdjacentMarker('next')
      return
    }

    if (key === 'i' || key === '1') {
      event.preventDefault()
      if (currentSeverity !== 'IMPORTANT') {
        updateManualStatus('IMPORTANT')
      }
      return
    }

    if (key === 'r' || key === '2') {
      event.preventDefault()
      if (currentSeverity !== 'REVIEW') {
        updateManualStatus('REVIEW')
      }
      return
    }

    if (key === 'g' || key === '3') {
      event.preventDefault()
      if (currentSeverity !== 'IGNORE') {
        updateManualStatus('IGNORE')
      }
      return
    }

    if (event.key === 'Delete' && !incident.user_deleted) {
      event.preventDefault()
      setShowDeleteConfirm(true)
    }
  }

  return (
    <main
      ref={viewerRef}
      tabIndex={-1}
      onKeyDown={handleViewerKeyDown}
      className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1500px] flex-col overflow-hidden rounded-2xl border border-white/[0.065] bg-[radial-gradient(circle_at_48%_-10%,rgba(157,183,170,0.085),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.012)),var(--mimir-bg-depth)] shadow-[0_34px_110px_rgba(0,0,0,0.54)] outline-none sm:min-h-[calc(100vh-48px)]"
    >
      <header className="flex flex-wrap items-center justify-between gap-4 px-5 py-4 lg:px-7">
        <button
          onClick={onBack}
          className="h-9 rounded-lg bg-white/[0.03] px-3.5 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)]"
        >
          Back to Library
        </button>

        <div className="flex items-center gap-2 rounded-full border border-[rgba(157,183,170,0.14)] bg-[var(--mimir-accent-soft)] px-3 py-1.5 text-[12px] text-[var(--mimir-text-muted)]">
          <span className="h-2 w-2 rounded-full bg-[var(--mimir-status-green)]" />
          Local evidence
        </div>
      </header>

      <section className="flex-1 overflow-y-auto px-5 pb-7 pt-2 lg:px-7">
        <div className="mb-4">
          <div className="mb-2 text-[12px] font-medium text-[var(--mimir-text-subtle)]">
            Incident viewer
          </div>
          <h1 className="text-[28px] font-semibold leading-tight text-[var(--mimir-text)] lg:text-[34px]">
            {title}
          </h1>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[var(--mimir-text-muted)]">
            {timestamp && <span>{timestamp}</span>}
            <span className="max-w-full truncate">{sourceLabel}</span>
            {isGenericBestEffort && (
              <span
                className="rounded-full bg-white/[0.035] px-2 py-0.5 text-[11px] text-[var(--mimir-text-subtle)]"
                title="Generic MP4 review is best-effort. Tesla Sentry camera groups receive full support."
              >
                Generic video · best effort
              </span>
            )}
          </div>
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_352px]">
          <div className="min-w-0">
            <section className="rounded-[24px] border border-white/[0.045] bg-black/16 p-2.5 shadow-[0_24px_78px_rgba(0,0,0,0.32)]">
              <CrashSafeBoundary
                title="Video player error"
                incidentId={incidentActionId(incident)}
                attemptedVideoPath={attemptedVideoPath}
                onBack={onBack}
                onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
              >
                <ViewerMedia
                  incident={incident}
                  seekRequest={seekRequest}
                  playbackRequest={playbackRequest}
                  currentTime={currentTime}
                  duration={duration}
                  onTimeUpdate={setCurrentTime}
                  onDurationChange={value => setDuration(Number.isFinite(value) ? value : 0)}
                />
              </CrashSafeBoundary>
              <section className="mt-3 rounded-2xl bg-white/[0.008] p-3">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3 px-1">
                  <div>
                    <h2 className="text-[15px] font-semibold text-[var(--mimir-text)]">Key moments</h2>
                    <p className="mt-1 text-[12px] text-[var(--mimir-text-subtle)]">
                      Jump to moments Mimir found in this clip.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowKeyMoments(value => !value)}
                    className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                  >
                    {showKeyMoments ? 'Hide' : 'Show'}
                  </button>
                </div>
                {showKeyMoments && (
                  <CrashSafeBoundary
                    title="Key moments error"
                    incidentId={incidentActionId(incident)}
                    attemptedVideoPath={attemptedVideoPath}
                    onBack={onBack}
                    onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
                  >
                    <IncidentTimelineMarkers
                      markers={markers}
                      incident={incident}
                      currentTime={currentTime}
                      duration={duration}
                      onSeek={handleSeek}
                      onSetActualMoment={time => void saveActualMoment(time)}
                    />
                  </CrashSafeBoundary>
                )}
              </section>
            </section>
          </div>

          <div className="xl:sticky xl:top-5 xl:self-start">
            <CrashSafeBoundary
              title="Incident details error"
              incidentId={incidentActionId(incident)}
              attemptedVideoPath={attemptedVideoPath}
              onBack={onBack}
              onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
            >
              <DetailsPanel incident={incident} session={session} severityResolution={severityResolution} />
            </CrashSafeBoundary>
            <CrashSafeBoundary
              title="Action panel error"
              incidentId={incidentActionId(incident)}
              attemptedVideoPath={attemptedVideoPath}
              onBack={onBack}
              onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
            >
              <ReviewActionsPanel
                incident={incident}
                session={session}
                currentSeverity={severityResolution.displaySeverity}
                mimirSeverity={severityResolution.mimirSeverity}
                isManualOverride={severityResolution.isManualOverride}
                busyAction={busyAction}
                actionMessage={actionMessage}
                actionError={actionError}
                actionDetails={actionDetails}
                feedbackChoice={feedbackChoice}
                feedbackNotes={feedbackNotes}
                feedbackIncludeVideo={feedbackIncludeVideo}
                feedbackMessage={feedbackMessage}
                feedbackError={feedbackError}
                noteDraft={noteDraft}
                isEditingNote={isEditingNote}
                onFeedbackChoiceChange={setFeedbackChoice}
                onFeedbackNotesChange={setFeedbackNotes}
                onFeedbackIncludeVideoChange={setFeedbackIncludeVideo}
                onSubmitFeedback={saveFeedback}
                onNoteDraftChange={setNoteDraft}
                onEditNote={() => setIsEditingNote(true)}
                onCancelNote={() => {
                  setNoteDraft(incident.user_note ?? '')
                  setIsEditingNote(false)
                }}
                onSaveNote={saveNote}
                onSetStatus={updateManualStatus}
                onMoveToLibrary={() => setShowLibraryConfirm(true)}
                onConfirmDelete={() => setShowDeleteConfirm(true)}
                onRestoreFromTrash={() => void runCoreV2StorageAction('restore_from_trash')}
                onOpenFiles={() => setShowFilesDrawer(true)}
              />
            </CrashSafeBoundary>
          </div>
        </div>
      </section>

      {showFilesDrawer && (
        <ViewerFilesDrawer
          incident={incident}
          onClose={() => setShowFilesDrawer(false)}
          onOpenFileAction={openViewerFileAction}
        />
      )}

      {showLibraryConfirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[480px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[18px] font-semibold text-[var(--mimir-text)]">
              Move this incident to Mimir Library?
            </div>
            <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir will move all camera angles for this incident to Mimir Library and update the review paths after the move is verified.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setShowLibraryConfirm(false)}
                disabled={busyAction !== null}
                className="h-10 rounded-lg bg-white/[0.04] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setShowLibraryConfirm(false)
                  void runCoreV2StorageAction('move_to_library')
                }}
                disabled={busyAction !== null}
                className="h-10 rounded-lg border border-white/[0.1] bg-white/[0.08] px-4 text-[13px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.12] disabled:cursor-wait disabled:opacity-60"
              >
                Move to Mimir Library
              </button>
            </div>
          </section>
        </div>
      )}

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[460px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[18px] font-semibold text-[var(--mimir-text)]">
              Move this incident to Mimir Trash?
            </div>
            <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              This moves the clips to Mimir Trash. It does not permanently delete them.
            </p>
            <p className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-subtle)]">
              All camera angles for this incident will be moved together by Mimir.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                disabled={busyAction !== null}
                className="h-10 rounded-lg bg-white/[0.04] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setShowDeleteConfirm(false)
                  void runCoreV2StorageAction('move_to_trash')
                }}
                disabled={busyAction !== null}
                className="h-10 rounded-lg border border-red-300/20 bg-red-500/12 px-4 text-[13px] font-semibold text-red-100 transition hover:bg-red-500/18 disabled:cursor-wait disabled:opacity-60"
              >
                Move to Mimir Trash
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
