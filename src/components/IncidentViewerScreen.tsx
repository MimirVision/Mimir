import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type SyntheticEvent } from 'react'
import { invoke } from '@tauri-apps/api/tauri'
import { convertFileSrc } from '@tauri-apps/api/tauri'
import { CrashSafeBoundary, logIncidentDiagnostic } from './CrashSafeBoundary'
import type { MimirCameraClip, MimirIncident, MimirSession, MimirTimelineMarker } from '../types'

interface IncidentViewerScreenProps {
  incident: MimirIncident
  onBack: () => void
  onReloadSession: () => Promise<MimirSession | null>
  onIncidentUpdated: (incident: MimirIncident) => void
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

type MediaMode = 'video' | 'image' | 'empty'
type IncidentAction = 'set_status_IGNORE' | 'set_status_REVIEW' | 'set_status_IMPORTANT' | 'move_to_library' | 'delete' | 'save_note' | 'save_feedback'
type AiFeedbackChoice = 'Correct' | 'Should be Important' | 'Should be Review' | 'Should be Ignore' | 'Weird AI flag' | 'Missed obvious event'

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
    return 'border-[rgba(185,101,97,0.26)] bg-[rgba(185,101,97,0.12)] text-red-100/90'
  }

  if (value === 'REVIEW') {
    return 'border-[rgba(173,139,85,0.28)] bg-[rgba(173,139,85,0.12)] text-amber-100/90'
  }

  return 'border-[rgba(127,133,136,0.22)] bg-[rgba(127,133,136,0.10)] text-[var(--mimir-text-muted)]'
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
    thumbnail_path: cleanPath(incident.thumbnail) || cleanPath(incident.hero_thumbnail),
    hero_thumbnail_path: cleanPath(incident.hero_thumbnail),
    contact_sheet_path: cleanPath(incident.contact_sheet),
    include_video_clip: includeVideo,
    video_included_by_user: includeVideo,
    automatic_upload: false,
  }
}

function storageState(incident: MimirIncident) {
  if (incident.user_deleted || incident.trash_video_path || incident.storage_action_applied === 'mimir_trash') {
    return 'Mimir Trash'
  }

  if (incident.moved_to_library || incident.library_video_path || incident.storage_action_applied === 'move_to_library') {
    return 'Mimir Library'
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
      ? 'border-[rgba(185,101,97,0.42)] bg-[rgba(185,101,97,0.18)] text-red-100'
      : 'border-[rgba(185,101,97,0.18)] bg-[rgba(185,101,97,0.07)] text-red-100/78 hover:bg-[rgba(185,101,97,0.12)]'
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
  const originalFolder = pathFolder(originalPath)
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
        <FilePathRow label="Original source folder" value={originalFolder} />
        <FilePathRow label="Current video path" value={currentPath} />
        <FilePathRow label="Library path" value={incident.library_video_path || ''} />
        <FilePathRow label="Trash path" value={incident.trash_video_path || ''} />
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

function markerLabel(marker: MimirTimelineMarker, incident?: MimirIncident) {
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

  return marker.label || marker.type || 'Timeline marker'
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

  return marker.description || 'No marker description was included.'
}

function validTimelineMarkers(markers?: MimirIncident['timeline_markers']) {
  return Array.isArray(markers) ? markers.filter(marker => marker && typeof marker === 'object') : []
}

function timedMarkers(markers: MimirTimelineMarker[]) {
  return markers
    .filter(marker => markerTime(marker) !== null)
    .sort((left, right) => (markerTime(left) ?? 0) - (markerTime(right) ?? 0))
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
    if (!seekRequest || !Number.isFinite(seekRequest.time) || seekRequest.time < 0) {
      return
    }

    const safeTime = Math.max(0, seekRequest.time)

    for (const video of videoRefs.current.values()) {
      if (!video || video.readyState === 0) {
        continue
      }

      try {
        video.currentTime = safeTime
        video.pause()
      } catch {
        // A single unavailable camera should not break shared timeline seeking.
      }
    }

    setIsPlaying(false)
  }, [seekRequest])

  useEffect(() => {
    if (!playbackRequest) {
      return
    }

    handleSharedPlayToggle()
  }, [playbackRequest])

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

  const syncVideosTo = (time: number, playing: boolean) => {
    for (const video of videoRefs.current.values()) {
      if (!video || video.readyState === 0) {
        continue
      }

      try {
        if (Number.isFinite(time) && Math.abs(video.currentTime - time) > 0.35) {
          video.currentTime = Math.max(0, time)
        }

        if (playing) {
          void video.play().catch(() => {
            setIsPlaying(false)
          })
        } else {
          video.pause()
        }
      } catch {
        // Keep other camera feeds usable if one media element refuses a seek/play.
      }
    }
  }

  const handleSharedPlayToggle = () => {
    const firstReadyVideo = Array.from(videoRefs.current.values()).find(video => video.readyState > 0)
    const baseTime = firstReadyVideo?.currentTime ?? 0
    const nextPlaying = !isPlaying

    setIsPlaying(nextPlaying)
    syncVideosTo(baseTime, nextPlaying)
  }

  const selectCameraFeed = (feed: CameraFeed) => {
    setSelectedFeedKey(feed.key)
    setSelectedGridKey(feed.key)
    setLoadedFeedKeys(previous => {
      const next = new Set(previous)
      next.add(feed.key)
      return next
    })
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

  const handleVideoTimeUpdate = (event: SyntheticEvent<HTMLVideoElement>) => {
    const time = event.currentTarget.currentTime
    onTimeUpdate(time)

    if (!isPlaying) {
      return
    }

    for (const [key, video] of videoRefs.current.entries()) {
      if (video === event.currentTarget || video.readyState === 0 || failedFeedKeys.has(key)) {
        continue
      }

      try {
        if (Math.abs(video.currentTime - time) > 0.45) {
          video.currentTime = time
        }
      } catch {
        // Sync is best-effort per feed.
      }
    }
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
    const shouldLoad =
      options.main ||
      viewerMode === 'grid' ||
      !options.main ||
      loadedFeedKeys.has(feed.key) ||
      feed.key === selectedFeed?.key
    const loading = loadingFeedKeys.has(feed.key)

    if (failed || !feed.path || !isAbsoluteLocalPath(feed.path) || !isVideoPath(feed.path) || feed.exists === false) {
      return renderUnavailable(feed.label, options.className, failed)
    }

    return (
      <div className={`relative overflow-hidden bg-black shadow-[inset_0_1px_0_rgba(255,255,255,0.035)] ${options.className || ''}`}>
        <video
          key={`${feed.key}-${feed.path}`}
          ref={setVideoRef(feed.key)}
          src={shouldLoad ? localFileSrc(feed.path) : undefined}
          preload={options.main ? 'metadata' : 'none'}
          playsInline
          muted={Boolean(options.muted)}
          className="h-full w-full bg-black object-contain"
          onLoadedMetadata={event => {
            markFeedLoading(feed.key, false)
            const durationValue = event.currentTarget.duration
            if (Number.isFinite(durationValue) && durationValue > 0) {
              onDurationChange(durationValue)
            }
            onTimeUpdate(event.currentTarget.currentTime)
          }}
          onDurationChange={event => {
            const durationValue = event.currentTarget.duration
            if (Number.isFinite(durationValue) && durationValue > 0) {
              onDurationChange(durationValue)
            }
          }}
          onTimeUpdate={handleVideoTimeUpdate}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onLoadStart={() => markFeedLoading(feed.key, true)}
          onLoadedData={() => markFeedLoading(feed.key, false)}
          onCanPlay={() => markFeedLoading(feed.key, false)}
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
              <button
                type="button"
                onClick={handleSharedPlayToggle}
                className="h-8 rounded-lg border border-white/[0.08] bg-white/[0.045] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.075]"
              >
                {isPlaying ? 'Pause' : 'Play'}
              </button>
              <span className="hidden rounded-full bg-black/22 px-2.5 py-1 text-[11px] text-[var(--mimir-text-subtle)] sm:inline">
                {formatTime(currentTime)} / {formatTime(duration)}
              </span>
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
              <button
                key={feed.key}
                type="button"
                onClick={() => {
                  selectCameraFeed(feed)
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
              </button>
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

      {import.meta.env.DEV && (
        <div className="absolute bottom-3 left-3 max-w-[calc(100%-24px)] rounded-md bg-black/70 px-3 py-2 text-[11px] leading-4 text-white/55">
          selected media: {canUseMultiCamera ? viewerMode : showSingleVideo ? 'video' : media.mode} / {selectedFeed?.label || videoCandidates[0]?.label || media.label}
          {(selectedFeed?.path || videoCandidates[0]?.path || media.path) && <span className="ml-2 break-all">{selectedFeed?.path || videoCandidates[0]?.path || media.path}</span>}
          <span className="ml-2">video_exists: {String(incident.video_exists ?? 'unknown')}</span>
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

function IncidentTimelineMarkers({
  markers,
  incident,
  currentTime,
  duration,
  onSeek,
}: {
  markers: MimirTimelineMarker[]
  incident: MimirIncident
  currentTime: number
  duration: number
  onSeek: (time: number) => void
}) {
  const [hoveredMarkerIndex, setHoveredMarkerIndex] = useState<number | null>(null)
  const [selectedMarkerIndex, setSelectedMarkerIndex] = useState<number | null>(null)
  const effectiveDuration = timelineDuration(markers, incident, duration)
  const canSeekRail = Number.isFinite(duration) && duration > 0
  const progressPercent =
    effectiveDuration > 0 ? Math.max(0, Math.min(100, (currentTime / effectiveDuration) * 100)) : 0
  const selectedMarker = selectedMarkerIndex === null ? null : markers[selectedMarkerIndex]
  const selectMarker = (index: number) => {
    const marker = markers[index]
    setSelectedMarkerIndex(index)

    const time = markerTime(marker)
    if (time !== null) {
      onSeek(time)
    }
  }
  const selectAdjacentMarker = (direction: 'previous' | 'next') => {
    if (markers.length === 0) {
      return
    }

    const currentIndex = selectedMarkerIndex ?? 0
    const nextIndex =
      direction === 'previous'
        ? Math.max(0, currentIndex - 1)
        : Math.min(markers.length - 1, currentIndex + 1)

    selectMarker(nextIndex)
  }

  useEffect(() => {
    setHoveredMarkerIndex(null)
    setSelectedMarkerIndex(markers.length > 0 ? 0 : null)
  }, [incident.id, markers.length])

  if (markers.length === 0) {
    return (
      <div className="rounded-[18px] border border-white/[0.06] bg-white/[0.016] p-5">
        <div className="text-[13px] text-[var(--mimir-text-muted)]">
          No timeline markers available for this incident.
        </div>
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
    const seekTime = Math.max(0, Math.min(duration, ratio * duration))

    onSeek(seekTime)
  }

  return (
    <div className="rounded-[18px] border border-white/[0.055] bg-[linear-gradient(180deg,rgba(255,255,255,0.022),rgba(255,255,255,0.01))] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-[13px] font-semibold text-[var(--mimir-text)]">Shared timeline</h2>
          <p className="mt-1 text-[12px] text-[var(--mimir-text-subtle)]">One timeline controls every loaded angle.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => selectAdjacentMarker('previous')}
            disabled={markers.length <= 1 || selectedMarkerIndex === 0}
            className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => selectAdjacentMarker('next')}
            disabled={markers.length <= 1 || selectedMarkerIndex === markers.length - 1}
            className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
          <div className="flex items-center gap-2 rounded-full bg-black/18 px-3 py-1 text-[12px] text-[var(--mimir-text-subtle)]">
            <span className="h-1.5 w-1.5 rounded-full bg-white/35" />
            {formatTime(currentTime)} / {formatTime(effectiveDuration)}
          </div>
        </div>
      </div>

      <div
        role="presentation"
        onClick={handleRailClick}
        className={`relative mx-2 h-[92px] ${canSeekRail ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <div className="absolute left-0 right-0 top-10 h-px bg-white/[0.08]" />
        <div className="absolute left-0 right-0 top-[39px] h-2 rounded-full bg-black/24 shadow-[inset_0_1px_1px_rgba(0,0,0,0.32)]" />
        <div
          className="absolute left-0 top-[39px] h-2 rounded-full bg-white/20 transition-[width]"
          style={{ width: `${progressPercent}%` }}
        />
        <div
          className="absolute top-[30px] h-10 w-px bg-white/42"
          style={{ left: `${progressPercent}%` }}
        />
        {markers.map((marker, index) => {
          const time = markerTime(marker)
          const position = markerPosition(marker, index, markers.length, effectiveDuration)
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
              className="group absolute top-[29px] -translate-x-1/2 cursor-pointer rounded-full outline-none"
              style={{ left: `${position}%` }}
              aria-label={`${markerLabel(marker, incident)} ${isTimed ? formatTime(time) : 'time unavailable'}`}
            >
              <span className="absolute left-1/2 top-7 h-5 w-px -translate-x-1/2 bg-white/[0.08]" />
              <span
                className={`relative z-[1] block rounded-full border transition duration-150 group-focus-visible:ring-2 group-focus-visible:ring-white/28 ${markerClass(marker.severity)} ${
                  isSelected
                    ? 'h-7 w-7 scale-110 ring-2 ring-white/24 brightness-110'
                    : isHovered
                      ? 'h-6 w-6 scale-110'
                      : 'h-5 w-5'
                }`}
              />
              <span className="sr-only">{markerLabel(marker, incident)}</span>

              {isHovered && (
                <span className="pointer-events-none absolute bottom-10 left-1/2 z-10 block w-56 -translate-x-1/2 rounded-lg bg-[var(--mimir-surface-soft)] p-3 text-left shadow-[0_18px_50px_rgba(0,0,0,0.42)]">
                  <span className="block text-[11px] uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">
                    {time === null ? 'time unavailable' : formatTime(time)}
                  </span>
                  <span className="mt-1 block text-[13px] font-semibold text-[var(--mimir-text)]">
                    {markerLabel(marker, incident)}
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
        <div className="absolute bottom-0 right-0 text-[11px] text-[var(--mimir-text-subtle)]">
          {formatTime(effectiveDuration)}
        </div>
      </div>

      <div className="mt-3 rounded-xl border border-white/[0.055] bg-black/16 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
        {selectedMarker ? (
          <div className="relative flex flex-wrap items-start gap-4 pl-4">
            <div className={`absolute bottom-1 left-0 top-1 w-1 rounded-full ${markerAccentClass(selectedMarker.severity)}`} />
            <div className="min-w-[74px]">
              <div className="text-[10px] uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">Marker</div>
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

function DetailsPanel({ incident }: { incident: MimirIncident }) {
  const evidence = safeTextList(incident.evidence)
  const impactReasons = safeTextList(incident.impact_reasons)
  const contactReasons = safeTextList(incident.contact_reasons)
  const filename = sourceFilename(cleanPath(incident.source_video) || cleanPath(incident.video_path) || cleanPath(incident.source_clip))
  const calmPersonNear = calmerPersonNearWording(incident)
  const supportedContact = hasSupportedContactEvidence(incident)
  const supportedImpact = hasSupportedImpactEvidence(incident)
  const showImpactReasons = impactReasons.length > 0 && (!calmPersonNear || supportedImpact)
  const showContactReasons = contactReasons.length > 0 && (!calmPersonNear || supportedContact)
  const displayEvidence = calmPersonNear ? ['No clear contact detected'] : evidence

  return (
    <aside className="rounded-[18px] border border-white/[0.055] bg-[linear-gradient(180deg,rgba(255,255,255,0.026),rgba(255,255,255,0.01))] p-5 shadow-[0_18px_44px_rgba(0,0,0,0.18)] xl:sticky xl:top-5 xl:self-start">
      <div className="mb-5 flex flex-wrap gap-2">
        <span className={`rounded-full border px-3 py-1 text-[12px] font-semibold ${severityClass(incident.severity)}`}>
          {severityCopy(incident.severity)}
        </span>
        {incident.possible_impact && (!calmPersonNear || supportedImpact) && (
          <span className="rounded-full border border-amber-300/25 bg-amber-400/10 px-3 py-1 text-[12px] font-medium text-amber-100">
            Possible impact
          </span>
        )}
      </div>

      <div className="mb-5">
        <div className="text-[12px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
          Mimir assessment
        </div>
        <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text)]">
          {assessmentCopy(incident)}
        </p>
        {importantNotAppliedNote(incident) && (
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.026] px-3 py-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
            Important was not applied because no contact or impact evidence was found.
          </div>
        )}
      </div>

      <div className="rounded-xl border border-white/[0.045] bg-black/10 px-4">
        <DetailMetric label="Source filename" value={filename || 'Not provided'} />
        <DetailMetric label="Event type" value={eventDisplayTitle(incident)} />
        <DetailMetric label="AI confidence" value={formatConfidence(incident.ai_confidence)} />
        <DetailMetric label="Impact level" value={calmPersonNear && !supportedImpact ? 'No clear impact detected' : incident.impact_level || 'Not provided'} />
        <DetailMetric label="Contact level" value={contactLevelCopy(incident)} />
        <DetailMetric label="Source event reason" value={sourceEventReason(incident) || 'Not provided'} />
      </div>

      <div className="mt-5 rounded-xl border border-white/[0.045] bg-black/12 p-4">
        <div className="mb-3 text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
          Evidence
        </div>
        {displayEvidence.length > 0 ? (
          <ul className="space-y-2">
            {displayEvidence.map((item, index) => (
              <li
                key={`${incident.id}-viewer-evidence-${index}`}
                className="flex gap-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]"
              >
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-white/35" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-[13px] text-[var(--mimir-text-muted)]">
            No evidence points were included.
          </div>
        )}
      </div>

      <div className="mt-5 rounded-xl border border-white/[0.045] bg-white/[0.014] p-4">
        <div className="mb-2 text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
          Recommended action
        </div>
        <div className="text-[14px] leading-6 text-[var(--mimir-text-muted)]">
          {incident.recommended_action || 'No recommended action was included.'}
        </div>
      </div>

      {showImpactReasons && (
        <div className="mt-5 rounded-xl border border-white/[0.045] bg-white/[0.014] p-4">
          <div className="mb-3 text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
            Impact reasons
          </div>
          <ul className="space-y-2">
            {impactReasons.map((item, index) => (
              <li
                key={`${incident.id}-viewer-impact-${index}`}
                className="flex gap-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]"
              >
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-200/45" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {showContactReasons && (
        <div className="mt-5 rounded-xl border border-white/[0.045] bg-white/[0.014] p-4">
          <div className="mb-3 text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
            Contact reasons
          </div>
          <ul className="space-y-2">
            {contactReasons.map((item, index) => (
              <li
                key={`${incident.id}-viewer-contact-${index}`}
                className="flex gap-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]"
              >
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-200/45" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <details className="mt-5 rounded-xl border border-white/[0.045] bg-white/[0.012] p-4">
        <summary className="cursor-pointer text-[13px] font-medium text-[var(--mimir-text-muted)]">
          Technical details
        </summary>
        <div className="mt-4 grid gap-3">
          <DetailMetric label="Incident ID" value={safeText(incident.id, 'Not provided')} />
          <DetailMetric label="Source video" value={safeText(incident.source_video)} />
          <DetailMetric label="AI decision" value={safeText(incident.ai_decision)} />
          <DetailMetric label="Score" value={formatNumber(incident.score)} />
          <DetailMetric label="Persons" value={formatNumber(incident.persons)} />
          <DetailMetric label="Vehicles" value={formatNumber(incident.vehicles)} />
          <DetailMetric label="Active frames" value={formatNumber(incident.active_frames)} />
          <DetailMetric label="Motion score" value={formatNumber(incident.max_motion_score)} />
          <DetailMetric label="Impact score" value={formatNumber(incident.impact_score)} />
          <DetailMetric label="Classification debug" value={safeText(incident.classification_debug, 'Not provided')} />
          <DetailMetric
            label="Source event timestamp"
            value={formatDateTime(sourceEventTimestamp(incident)) || 'Not provided'}
          />
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
    <div className="mt-5 rounded-xl border border-white/[0.045] bg-black/12 p-4">
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

function ReviewActionsPanel({
  incident,
  busyAction,
  actionMessage,
  actionError,
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
  onOpenFiles,
}: {
  incident: MimirIncident
  busyAction: IncidentAction | null
  actionMessage: string
  actionError: string
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
  onSetStatus: (status: 'IGNORE' | 'REVIEW' | 'IMPORTANT') => void
  onMoveToLibrary: () => void
  onConfirmDelete: () => void
  onOpenFiles: () => void
}) {
  const currentSeverity = normalizeSeverity(incident.severity)
  const disabled = busyAction !== null

  return (
    <section className="mb-5 rounded-[18px] border border-white/[0.055] bg-[linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.012))] p-5 shadow-[0_18px_44px_rgba(0,0,0,0.18)]">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
            Review controls
          </div>
          <p className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
            Update this event after reviewing the evidence.
          </p>
        </div>
        {incident.user_deleted && (
          <span className="rounded-full border border-red-300/20 bg-red-500/10 px-3 py-1 text-[11px] font-semibold text-red-100/85">
            In Mimir Trash
          </span>
        )}
      </div>

      <div className="mb-4 rounded-xl border border-white/[0.045] bg-black/12 px-3 py-2.5">
        <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">
          Current status
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${severityClass(currentSeverity)}`}>
            {severityCopy(currentSeverity)}
          </span>
          <span className="text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
            Shortcuts: 1 important, 2 review, 3 ignore.
          </span>
        </div>
      </div>

      <div>
        <div className="mb-2 text-[12px] font-semibold text-[var(--mimir-text)]">Status</div>
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
        <button
          onClick={onOpenFiles}
          disabled={disabled}
          className="h-10 rounded-lg border border-white/[0.075] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-55"
        >
          Files
        </button>
        <button
          onClick={onMoveToLibrary}
          disabled={disabled || incident.user_deleted}
          className="h-10 rounded-lg border border-white/[0.085] bg-white/[0.035] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.065] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'move_to_library' && <SmallSpinner />}
            Move to Mimir Library
          </span>
        </button>
        <button
          onClick={onConfirmDelete}
          disabled={disabled || incident.user_deleted}
          className="h-10 rounded-lg border border-red-300/18 bg-red-500/8 px-3 text-[12px] font-semibold text-red-100/86 transition hover:bg-red-500/12 disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'delete' && <SmallSpinner />}
            Move to Mimir Trash
          </span>
        </button>
      </div>

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

      <div className="mt-5 rounded-xl border border-white/[0.045] bg-black/12 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="text-[12px] font-semibold text-[var(--mimir-text)]">Note</div>
          <button
            onClick={onEditNote}
            disabled={disabled}
            className="grid h-8 w-8 place-items-center rounded-lg bg-white/[0.04] text-[15px] text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:cursor-wait disabled:opacity-60"
            title="Edit note"
          >
            ✎
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
          {actionMessage}
        </div>
      )}
      {actionError && (
        <div className="mt-4 rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/86">
          {actionError}
        </div>
      )}
    </section>
  )
}

export function IncidentViewerScreen({
  incident,
  onBack,
  onReloadSession,
  onIncidentUpdated,
}: IncidentViewerScreenProps) {
  const markers = validTimelineMarkers(incident.timeline_markers)
  const title = eventDisplayTitle(incident)
  const timestamp = formatDateTime(sourceEventTimestamp(incident))
  const sourceLabel =
    sourceFilename(cleanPath(incident.source_video) || cleanPath(incident.original_source_video) || cleanPath(incident.video_path)) ||
    'Source filename not provided'
  const attemptedVideoPath = attemptedVideoPathForIncident(incident)
  const viewerRef = useRef<HTMLElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [seekRequest, setSeekRequest] = useState<ViewerSeekRequest | null>(null)
  const [playbackRequest, setPlaybackRequest] = useState<ViewerPlaybackRequest | null>(null)
  const [busyAction, setBusyAction] = useState<IncidentAction | null>(null)
  const [actionMessage, setActionMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showFilesDrawer, setShowFilesDrawer] = useState(false)
  const [isEditingNote, setIsEditingNote] = useState(false)
  const [noteDraft, setNoteDraft] = useState(incident.user_note ?? '')
  const [feedbackChoice, setFeedbackChoice] = useState<AiFeedbackChoice | ''>('')
  const [feedbackNotes, setFeedbackNotes] = useState('')
  const [feedbackIncludeVideo, setFeedbackIncludeVideo] = useState(false)
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [feedbackError, setFeedbackError] = useState('')

  useEffect(() => {
    setCurrentTime(0)
    setDuration(0)
    setSeekRequest(null)
    setPlaybackRequest(null)
    setActionMessage('')
    setActionError('')
    setShowDeleteConfirm(false)
    setShowFilesDrawer(false)
    setIsEditingNote(false)
    setNoteDraft(incident.user_note ?? '')
    setFeedbackChoice('')
    setFeedbackNotes('')
    setFeedbackIncludeVideo(false)
    setFeedbackMessage('')
    setFeedbackError('')
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

  const successMessageForAction = (
    action: 'set_status' | 'move_to_library' | 'delete',
    status?: 'IGNORE' | 'REVIEW' | 'IMPORTANT',
  ) => {
    if (action === 'move_to_library') {
      return 'Clip moved to Mimir Library'
    }

    if (action === 'delete') {
      return 'Clip moved to Mimir Trash'
    }

    if (status) {
      return `Status changed to ${severityCopy(status)}`
    }

    return 'Action completed.'
  }

  const runBackendAction = async (
    action: 'set_status' | 'move_to_library' | 'delete',
    busyKey: IncidentAction,
    status?: 'IGNORE' | 'REVIEW' | 'IMPORTANT',
  ) => {
    setBusyAction(busyKey)
    setActionError('')
    setActionMessage('')

    try {
      const result = await invoke<ClipActionResult>('run_incident_action', {
        incidentId: incidentActionId(incident),
        action,
        status: status ?? null,
      })

      await refreshCurrentIncident(successMessageForAction(action, status) || result.message || 'Action completed.')
    } catch (error) {
      setActionError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const saveNote = async () => {
    setBusyAction('save_note')
    setActionError('')
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

  const saveFeedback = async () => {
    if (!feedbackChoice) {
      setFeedbackError('Choose a feedback label first.')
      return
    }

    setBusyAction('save_feedback')
    setFeedbackError('')
    setFeedbackMessage('')
    setActionError('')

    try {
      const videoPath = currentVideoPath(incident)
      const result = await invoke<IncidentFeedbackResult>('save_incident_feedback', {
        feedback: incidentFeedbackPayload(incident, feedbackChoice, feedbackNotes, feedbackIncludeVideo),
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
    if (isTextInputElement(event.target) || busyAction !== null || showDeleteConfirm || showFilesDrawer) {
      return
    }

    const key = event.key.toLowerCase()
    const currentSeverity = normalizeSeverity(incident.severity)

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
        void runBackendAction('set_status', 'set_status_IMPORTANT', 'IMPORTANT')
      }
      return
    }

    if (key === 'r' || key === '2') {
      event.preventDefault()
      if (currentSeverity !== 'REVIEW') {
        void runBackendAction('set_status', 'set_status_REVIEW', 'REVIEW')
      }
      return
    }

    if (key === 'g' || key === '3') {
      event.preventDefault()
      if (currentSeverity !== 'IGNORE') {
        void runBackendAction('set_status', 'set_status_IGNORE', 'IGNORE')
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
      className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1480px] flex-col overflow-hidden rounded-xl border border-[var(--mimir-border)] bg-[radial-gradient(circle_at_48%_0%,rgba(255,255,255,0.045),transparent_34%),var(--mimir-bg-depth)] shadow-[0_28px_90px_rgba(0,0,0,0.5)] outline-none sm:min-h-[calc(100vh-48px)]"
    >
      <header className="flex flex-wrap items-center justify-between gap-4 px-5 py-4 lg:px-7">
        <button
          onClick={onBack}
          className="h-10 rounded-lg bg-white/[0.035] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.065] hover:text-[var(--mimir-text)]"
        >
          Back to Library
        </button>

        <div className="flex items-center gap-2 rounded-full bg-white/[0.03] px-4 py-2 text-[13px] text-[var(--mimir-text-muted)]">
          <span className="h-2 w-2 rounded-full bg-[var(--mimir-status-green)]" />
          Local evidence
        </div>
      </header>

      <section className="flex-1 overflow-y-auto px-5 pb-7 pt-2 lg:px-7">
        <div className="mb-5">
          <div className="mb-2 text-[12px] font-medium uppercase tracking-[0.2em] text-[var(--mimir-text-subtle)]">
            Incident viewer
          </div>
          <h1 className="text-[32px] font-semibold leading-tight text-[var(--mimir-text)] lg:text-[38px]">
            {title}
          </h1>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[var(--mimir-text-muted)]">
            {timestamp && <span>{timestamp}</span>}
            <span className="max-w-full truncate">{sourceLabel}</span>
          </div>
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_348px]">
          <div className="min-w-0">
            <section className="rounded-[26px] border border-white/[0.07] bg-[linear-gradient(180deg,rgba(255,255,255,0.032),rgba(255,255,255,0.012))] p-3 shadow-[0_30px_86px_rgba(0,0,0,0.34)]">
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
              <div className="mt-3">
                <CrashSafeBoundary
                  title="Timeline error"
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
                  />
                </CrashSafeBoundary>
              </div>
            </section>
          </div>

          <div className="xl:sticky xl:top-5 xl:self-start">
            <CrashSafeBoundary
              title="Action panel error"
              incidentId={incidentActionId(incident)}
              attemptedVideoPath={attemptedVideoPath}
              onBack={onBack}
              onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
            >
              <ReviewActionsPanel
                incident={incident}
                busyAction={busyAction}
                actionMessage={actionMessage}
                actionError={actionError}
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
                onSetStatus={status =>
                  void runBackendAction(`set_status`, `set_status_${status}` as IncidentAction, status)
                }
                onMoveToLibrary={() => void runBackendAction('move_to_library', 'move_to_library')}
                onConfirmDelete={() => setShowDeleteConfirm(true)}
                onOpenFiles={() => setShowFilesDrawer(true)}
              />
            </CrashSafeBoundary>
            <CrashSafeBoundary
              title="Incident details error"
              incidentId={incidentActionId(incident)}
              attemptedVideoPath={attemptedVideoPath}
              onBack={onBack}
              onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
            >
              <DetailsPanel incident={incident} />
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

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[460px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[18px] font-semibold text-[var(--mimir-text)]">
              Move this clip to Mimir Trash?
            </div>
            <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              This will not permanently delete the file. It can be recovered from the Mimir Trash folder.
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
                  void runBackendAction('delete', 'delete')
                }}
                disabled={busyAction !== null}
                className="h-10 rounded-lg border border-red-300/20 bg-red-500/12 px-4 text-[13px] font-semibold text-red-100 transition hover:bg-red-500/18 disabled:cursor-wait disabled:opacity-60"
              >
                Move to Trash
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
