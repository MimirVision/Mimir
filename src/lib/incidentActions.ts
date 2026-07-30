import { attemptedVideoPathForIncident, cleanPath } from './incidentVideoPaths'
import { sourceFilename } from './incidentDisplay'
import { aiModelName, aiReviewed } from './incidentEvidence'
import { normalizeSeverity } from './incidentStatus'
import type { MimirIncident, MimirSession } from '../types'

// Storage state, incident action helpers, and the request/result types they
// operate on. Extracted from IncidentViewerScreen.tsx.

export interface StorageActionResult {
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

export interface StorageActionReport {
  ok?: boolean
  action?: string
  moved_files?: unknown[]
  failed_files?: unknown[]
  skipped_files?: unknown[]
  failures?: unknown[]
}

export type IncidentAction =
  | 'set_status_IGNORE'
  | 'set_status_REVIEW'
  | 'set_status_IMPORTANT'
  | 'move_to_library'
  | 'move_to_trash'
  | 'restore_from_trash'
  | 'delete'
  | 'save_note'
  | 'save_feedback'
  | 'save_key_moment'
  | 'export_report'

export type AiFeedbackChoice =
  | 'Correct'
  | 'Should be Important'
  | 'Should be Review'
  | 'Should be Ignore'
  | 'Weird AI flag'
  | 'Missed obvious event'

export interface IncidentFeedbackResult {
  ok: boolean
  feedback_folder: string
  feedback_file: string
  video_copied: boolean
  message: string
}

export interface TrainingContributionResult {
  ok: boolean
  output_path: string
  backend_runner: string
  backend_command: string
  message: string
}

export const feedbackChoices: AiFeedbackChoice[] = [
  'Correct',
  'Should be Important',
  'Should be Review',
  'Should be Ignore',
  'Weird AI flag',
  'Missed obvious event',
]

export function readLocalSetting(key: string) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function originalVideoPath(incident: MimirIncident) {
  return cleanPath(incident.original_source_video) || cleanPath(incident.source_video) || cleanPath(incident.source_clip)
}

export function currentVideoPath(incident: MimirIncident) {
  if (incident.user_deleted && incident.trash_video_path) {
    return incident.trash_video_path
  }

  return attemptedVideoPathForIncident(incident) || cleanPath(incident.source_clip)
}

export function incidentFeedbackPayload(
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

export function storageState(incident: MimirIncident) {
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

export function incidentActionId(incident: MimirIncident) {
  return incident.id || String(incident.event_id ?? '')
}

export function findIncident(session: MimirSession, incident: MimirIncident) {
  const wantedId = incident.id
  const wantedEventId = incident.event_id

  return session.incidents.find(candidate => {
    if (wantedId && candidate.id === wantedId) {
      return true
    }

    return String(candidate.event_id ?? '') === String(wantedEventId ?? '')
  })
}

export function actionButtonTone(status: string, active: boolean) {
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



export function parseStorageActionReport(value: string): StorageActionReport {
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

export function storageActionDetails(reportJson: string, result?: StorageActionResult) {
  const report = parseStorageActionReport(reportJson)
  const details = {
    report,
    backend_runner: result?.backend_runner || '',
    stdout: result?.stdout || '',
    stderr: result?.stderr || '',
  }

  return JSON.stringify(details, null, 2)
}

export function storageActionStatus(report: StorageActionReport) {
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
