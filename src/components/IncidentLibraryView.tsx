import { useEffect, useMemo, useState } from 'react'
import { convertFileSrc } from '@tauri-apps/api/tauri'
import { invoke } from '@tauri-apps/api/tauri'
import { documentDir } from '@tauri-apps/api/path'
import mimirLockup from '../assets/mimir-lockup.png'
import { CrashSafeBoundary } from './CrashSafeBoundary'
import { IncidentViewerScreen } from './IncidentViewerScreen'
import type { MimirCameraClip, MimirIncident, MimirSession, SessionLoadState } from '../types'

type LibraryFilter = 'IMPORTANT' | 'REVIEW' | 'IGNORE' | 'ALL' | 'TRASH'
type SeverityGroup = 'IMPORTANT' | 'REVIEW' | 'IGNORE'
type ReviewPage = 'review' | 'library'

interface IncidentLibraryViewProps {
  session: MimirSession
  loadState: SessionLoadState
  onImportNew: () => void
  onLoadLatest: () => void
  onReloadSession: () => Promise<MimirSession | null>
}

const severityRank: Record<SeverityGroup, number> = {
  IMPORTANT: 0,
  REVIEW: 1,
  IGNORE: 2,
}

const defaultLibraryPath = '%USERPROFILE%\\Videos\\Mimir Library'
const defaultTrashPath = '%USERPROFILE%\\Videos\\Mimir Library\\_Mimir Trash'

function errorMessage(error: unknown) {
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

function normalizeSeverity(severity?: string): SeverityGroup {
  const value = String(severity ?? '').toUpperCase()

  if (value === 'IMPORTANT' || value === 'REVIEW') {
    return value
  }

  return 'IGNORE'
}

function severityCopy(severity: string) {
  const normalized = normalizeSeverity(severity)

  if (normalized === 'IMPORTANT') {
    return 'Important'
  }

  if (normalized === 'REVIEW') {
    return 'Review'
  }

  return 'Ignored'
}

function severityClass(severity: string) {
  const normalized = normalizeSeverity(severity)

  if (normalized === 'IMPORTANT') {
    return 'border-[rgba(185,101,97,0.26)] bg-[rgba(185,101,97,0.12)] text-red-100/90'
  }

  if (normalized === 'REVIEW') {
    return 'border-[rgba(173,139,85,0.28)] bg-[rgba(173,139,85,0.12)] text-amber-100/90'
  }

  return 'border-[rgba(127,133,136,0.22)] bg-[rgba(127,133,136,0.10)] text-[var(--mimir-text-muted)]'
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

function formatDuration(incident: MimirIncident) {
  if (typeof incident.duration === 'number') {
    return `${incident.duration}s`
  }

  if (typeof incident.duration === 'string' && incident.duration.trim()) {
    return incident.duration
  }

  return ''
}

function sourceFilename(value?: string) {
  if (!value) {
    return ''
  }

  const parts = value.split(/[\\/]/)
  return parts[parts.length - 1] || value
}

function formatRuntime(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return ''
  }

  if (value < 60) {
    return `${value.toFixed(value < 10 ? 1 : 0)} sec`
  }

  const minutes = Math.floor(value / 60)
  const seconds = Math.round(value % 60)

  return `${minutes}m ${seconds}s`
}

function formatScanMode(value?: string) {
  if (!value) {
    return ''
  }

  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`
}

function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return count === 1 ? singular : plural
}

function scanResultCopy(session: MimirSession) {
  const incidentCount = (session.incidents ?? []).filter(incident => !incident.user_deleted).length
  const clipCount = session.clips_processed ?? 0

  if (incidentCount === 0) {
    return 'No reviewable incidents found.'
  }

  return `${incidentCount} ${pluralize(incidentCount, 'incident')} found from ${clipCount} ${pluralize(
    clipCount,
    'clip',
  )} scanned.`
}

function scanAccountingCopy(session: MimirSession) {
  const incidentCount = (session.incidents ?? []).filter(incident => !incident.user_deleted).length
  const ignoredCount = session.ignore ?? 0
  const importantCount = session.important ?? 0
  const reviewCount = session.review ?? 0

  if (incidentCount === 0) {
    return `${ignoredCount} ${pluralize(ignoredCount, 'clip')} were marked ignored by the scan summary.`
  }

  return `${importantCount} important, ${reviewCount} review, and ${ignoredCount} ignored ${pluralize(
    ignoredCount,
    'clip',
  )} in this scan. Only Mimir-created incident cards are shown below.`
}

function sourceEventReason(incident: MimirIncident) {
  return incident.source_event_reason || incident.tesla_event_reason || ''
}

function sourceEventTimestamp(incident: MimirIncident) {
  return incident.source_event_timestamp || incident.tesla_event_timestamp || incident.created_at
}

function searchText(incident: MimirIncident) {
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

function sessionCounts(session: MimirSession) {
  const runtime = formatRuntime(session.performance?.total_runtime_sec)
  const scanMode = formatScanMode(session.scan_mode)
  const activeIncidentCount = (session.incidents ?? []).filter(incident => !incident.user_deleted).length
  return [
    { label: 'Clips scanned', value: session.clips_processed },
    { label: 'Incidents found', value: activeIncidentCount },
    { label: 'Important', value: session.important },
    { label: 'Review', value: session.review },
    { label: 'Ignored', value: session.ignore },
    ...(scanMode ? [{ label: 'Scan mode', value: scanMode }] : []),
    ...(runtime ? [{ label: 'Runtime', value: runtime }] : []),
  ]
}

function sourceActionCopy(value?: string) {
  if (value === 'analyze_only') {
    return 'Review only'
  }

  if (value === 'copy_all') {
    return 'Copy clips to Mimir Library'
  }

  if (value === 'move_all') {
    return 'Move clips to Mimir Library'
  }

  if (value === 'copy_review') {
    return 'Copy Important/Review only'
  }

  if (value === 'move_review') {
    return 'Move Important/Review only'
  }

  return value || 'Not reported'
}

function formatSourceType(value?: string) {
  if (!value) {
    return 'Not reported'
  }

  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

function listCopy(values?: string[]) {
  if (!values || values.length === 0) {
    return 'None reported'
  }

  return values.join(', ')
}

function sourceReportMetrics(session: MimirSession) {
  const report = session.source_report

  return [
    {
      label: 'Source type',
      value: formatSourceType(report?.detected_source_type || session.detected_source_type || undefined),
    },
    {
      label: 'Categories',
      value: listCopy(report?.categories_found || session.source_categories_found),
    },
    {
      label: 'MP4 files',
      value: report?.mp4_files_found ?? session.clips_processed ?? 0,
    },
    {
      label: 'Event groups',
      value: report?.event_groups_found ?? session.event_groups_found ?? 0,
    },
  ]
}

function storageMetrics(session: MimirSession) {
  return [
    { label: 'Source action', value: sourceActionCopy(session.source_action) },
    { label: 'Files copied', value: session.files_copied ?? 0 },
    { label: 'Files moved', value: session.files_moved ?? 0 },
    { label: 'Files failed', value: session.files_failed ?? 0 },
    { label: 'Source files removed', value: session.source_files_removed ?? 0 },
  ]
}

function sortIncidents(incidents: MimirIncident[]) {
  return [...incidents].sort((left, right) => {
    const severityDelta =
      severityRank[normalizeSeverity(left.severity)] - severityRank[normalizeSeverity(right.severity)]

    if (severityDelta !== 0) {
      return severityDelta
    }

    return String(left.created_at || '').localeCompare(String(right.created_at || ''))
  })
}

function incidentCardImages(incident: MimirIncident) {
  return [
    incident.hero_thumbnail,
    incident.thumbnail,
    incident.best_frame_image,
    incident.contact_sheet,
  ].filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
}

function IncidentImage({ incident, large = false }: { incident: MimirIncident; large?: boolean }) {
  const images = incidentCardImages(incident)
  const [failedIndex, setFailedIndex] = useState(0)
  const imagePath = images[failedIndex]

  return (
    <div
      className={`relative overflow-hidden rounded-lg bg-black/42 ${
        large ? 'min-h-[320px]' : 'h-[128px]'
      }`}
    >
      {imagePath ? (
        <img
          src={convertFileSrc(imagePath, 'asset')}
          alt=""
          onError={() => setFailedIndex(index => index + 1)}
          className="h-full w-full object-cover"
        />
      ) : (
        <div className="flex h-full items-center justify-center px-4 text-center text-[12px] text-[var(--mimir-text-subtle)]">
          Thumbnail unavailable
        </div>
      )}
    </div>
  )
}

function signalBadges(incident: MimirIncident) {
  const badges: Array<{ label: string; className: string }> = []

  if (incident.impact_level && incident.impact_level !== 'NONE') {
    badges.push({
      label: `Impact ${incident.impact_level}`,
      className: 'border-[rgba(185,101,97,0.22)] bg-[rgba(185,101,97,0.11)] text-red-100/82',
    })
  }

  if (incident.contact_level && incident.contact_level !== 'NONE') {
    badges.push({
      label: `Contact ${incident.contact_level}`,
      className: 'border-[rgba(173,139,85,0.24)] bg-[rgba(173,139,85,0.11)] text-amber-100/82',
    })
  }

  if (badges.length === 0 && incident.possible_impact) {
    badges.push({
      label: 'Possible impact',
      className: 'border-[rgba(185,101,97,0.22)] bg-[rgba(185,101,97,0.11)] text-red-100/82',
    })
  }

  if (badges.length === 0 && incident.possible_contact) {
    badges.push({
      label: 'Possible contact',
      className: 'border-[rgba(173,139,85,0.24)] bg-[rgba(173,139,85,0.11)] text-amber-100/82',
    })
  }

  return badges
}

function SummaryMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-white/[0.055] bg-white/[0.022] px-4 py-3">
      <div className="text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-2 text-[24px] font-semibold text-[var(--mimir-text)]">{value}</div>
    </div>
  )
}

function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`h-10 rounded-full border px-4 text-[13px] font-medium transition ${
        active
          ? 'border-white/24 bg-white/[0.095] text-[var(--mimir-text)]'
          : 'border-[var(--mimir-border)] bg-white/[0.02] text-[var(--mimir-text-muted)] hover:bg-white/[0.05] hover:text-[var(--mimir-text)]'
      }`}
    >
      {label} <span className="ml-1 text-[var(--mimir-text-subtle)]">{count}</span>
    </button>
  )
}

function incidentActionId(incident: MimirIncident) {
  return incident.id || String(incident.event_id ?? '')
}

function cameraClips(incident: MimirIncident) {
  const raw = incident.camera_clips
  const clips: MimirCameraClip[] = []

  if (Array.isArray(raw)) {
    clips.push(...raw.filter((clip): clip is MimirCameraClip => Boolean(clip && typeof clip === 'object')))
  } else if (raw && typeof raw === 'object') {
    clips.push(
      ...Object.entries(raw)
        .map(([camera, value]) => {
        if (!value) {
          return null
        }

        if (typeof value === 'string') {
          return { camera, path: value }
        }

        return { camera: value.camera || camera, ...value }
      })
        .filter((value): value is MimirCameraClip => Boolean(value)),
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

function cameraCount(incident: MimirIncident) {
  const explicitCount = typeof incident.camera_count === 'number' ? incident.camera_count : 0
  return Math.max(explicitCount, cameraClips(incident).length, 1)
}

function cameraCountLabel(incident: MimirIncident) {
  const count = cameraCount(incident)
  return `${count} ${count === 1 ? 'angle' : 'angles'}`
}

function cameraNameLabel(value?: string | null) {
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

function primaryCameraLabel(incident: MimirIncident) {
  return cameraNameLabel(incident.primary_camera)
}

function eventLabel(incident: MimirIncident) {
  const summary = typeof incident.summary === 'string' ? incident.summary.trim() : ''
  if (summary) {
    return summary.length > 82 ? `${summary.slice(0, 79)}...` : summary
  }

  if (incident.event_type) {
    return formatEventType(incident.event_type)
  }

  return 'Review moment'
}

function currentStorageState(incident: MimirIncident) {
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

function storageBadgeClass(state: string) {
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

function incidentFolderPath(incident: MimirIncident) {
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

function pathFolder(value?: string | null) {
  if (!value) {
    return ''
  }

  const normalized = value.replace(/[/\\]+$/, '')
  const index = Math.max(normalized.lastIndexOf('\\'), normalized.lastIndexOf('/'))
  return index > 0 ? normalized.slice(0, index) : ''
}

function sourceFolderLabel(incident: MimirIncident) {
  const folder = incident.event_folder || pathFolder(incident.source_video || incident.original_source_video || '')
  return folder || 'Not available'
}

function sourceClipsRemain(incidents: MimirIncident[]) {
  return incidents.some(incident => {
    if (incident.user_deleted || incident.moved_to_library || incident.storage_state === 'trash' || incident.storage_state === 'library') {
      return false
    }

    return Boolean(incident.source_video || incident.original_source_video || cameraClips(incident).length > 0)
  })
}

function incidentsForSeverity(incidents: MimirIncident[], severity: SeverityGroup) {
  return incidents.filter(incident => normalizeSeverity(incident.severity) === severity)
}

function IncidentCard({
  incident,
  selected,
  selectionMode,
  onOpen,
  onToggleSelected,
  onOpenFiles,
}: {
  incident: MimirIncident
  selected: boolean
  selectionMode: boolean
  onOpen: (incident: MimirIncident) => void
  onToggleSelected: (incident: MimirIncident) => void
  onOpenFiles: (incident: MimirIncident) => void
}) {
  const timestamp = formatDateTime(sourceEventTimestamp(incident))
  const title = eventLabel(incident)
  const primaryCamera = primaryCameraLabel(incident)

  return (
    <article
      className={`group min-w-0 overflow-hidden rounded-lg border bg-[linear-gradient(180deg,rgba(255,255,255,0.032),rgba(255,255,255,0.012))] shadow-[0_14px_38px_rgba(0,0,0,0.2)] transition hover:-translate-y-0.5 hover:bg-white/[0.045] ${
        selected ? 'border-white/28 ring-1 ring-white/20' : 'border-white/[0.055]'
      }`}
    >
      <button
        type="button"
        onClick={() => {
          if (selectionMode) {
            onToggleSelected(incident)
          } else {
            onOpen(incident)
          }
        }}
        className="block w-full text-left"
      >
        <div className="relative">
        <IncidentImage incident={incident} />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-black/72 to-transparent opacity-70 transition group-hover:opacity-88" />
        {selectionMode && (
          <label
            className="absolute left-2.5 top-2.5 grid h-7 w-7 place-items-center rounded-full border border-white/18 bg-black/55 backdrop-blur"
            onClick={event => event.stopPropagation()}
          >
            <span className="sr-only">Select incident</span>
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggleSelected(incident)}
              className="h-4 w-4 accent-white"
            />
          </label>
        )}
        <span
          className={`absolute right-2.5 top-2.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold backdrop-blur-md ${severityClass(
            incident.severity,
          )}`}
        >
          {severityCopy(incident.severity)}
        </span>
        </div>

        <div className="min-w-0 px-3 pb-2 pt-3">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0 truncate text-[13px] font-semibold text-[var(--mimir-text)]">{title}</div>
          <button
            type="button"
            onClick={event => {
              event.stopPropagation()
              onOpenFiles(incident)
            }}
            className="shrink-0 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)]"
          >
            Files
          </button>
        </div>

        <div className="mt-2 flex min-w-0 flex-wrap gap-1.5 text-[11px] leading-4 text-[var(--mimir-text-subtle)]">
          <span className="rounded-full bg-white/[0.045] px-2 py-0.5 text-[var(--mimir-text-muted)]">
            {cameraCountLabel(incident)}
          </span>
          {primaryCamera && cameraCount(incident) > 1 && (
            <span className="rounded-full bg-white/[0.035] px-2 py-0.5 text-[var(--mimir-text-subtle)]">
              Best: {primaryCamera}
            </span>
          )}
          {timestamp && <span>{timestamp}</span>}
        </div>
      </div>
      </button>
    </article>
  )
}

function StorageStateBadge({ incident }: { incident: MimirIncident }) {
  const state = currentStorageState(incident)

  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${storageBadgeClass(state)}`}>
      {state}
    </span>
  )
}

function LibraryItem({
  incident,
  onOpen,
  onOpenFiles,
}: {
  incident: MimirIncident
  onOpen: (incident: MimirIncident) => void
  onOpenFiles: (incident: MimirIncident) => void
}) {
  const timestamp = formatDateTime(sourceEventTimestamp(incident))

  return (
    <article className="grid gap-3 rounded-lg border border-white/[0.055] bg-white/[0.018] p-2.5 transition hover:bg-white/[0.04] sm:grid-cols-[132px_minmax(0,1fr)_auto] sm:items-center">
      <button type="button" onClick={() => onOpen(incident)} className="block overflow-hidden rounded-md text-left">
        <IncidentImage incident={incident} />
      </button>

      <button type="button" onClick={() => onOpen(incident)} className="min-w-0 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${severityClass(incident.severity)}`}>
            {severityCopy(incident.severity)}
          </span>
          <StorageStateBadge incident={incident} />
          <span className="rounded-full bg-white/[0.045] px-2 py-0.5 text-[11px] text-[var(--mimir-text-muted)]">
            {cameraCountLabel(incident)}
          </span>
        </div>
        <div className="mt-2 truncate text-[14px] font-semibold text-[var(--mimir-text)]">{eventLabel(incident)}</div>
        <div className="mt-1 text-[12px] text-[var(--mimir-text-subtle)]">{timestamp || 'Timestamp unavailable'}</div>
      </button>

      <div className="flex flex-wrap gap-2 sm:justify-end">
        <button
          type="button"
          onClick={() => onOpen(incident)}
          className="h-9 rounded-md bg-white/[0.04] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)]"
        >
          Open incident
        </button>
        <button
          type="button"
          onClick={() => onOpenFiles(incident)}
          className="h-9 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)]"
        >
          Files
        </button>
      </div>
    </article>
  )
}

function LibrarySection({
  title,
  incidents,
  onOpen,
  onOpenFiles,
}: {
  title: string
  incidents: MimirIncident[]
  onOpen: (incident: MimirIncident) => void
  onOpenFiles: (incident: MimirIncident) => void
}) {
  return (
    <section className="mb-6">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[17px] font-semibold text-[var(--mimir-text)]">{title}</h2>
        <span className="text-[12px] text-[var(--mimir-text-subtle)]">{incidents.length}</span>
      </div>

      {incidents.length === 0 ? (
        <div className="rounded-lg border border-white/[0.045] bg-white/[0.014] px-4 py-5 text-[13px] text-[var(--mimir-text-subtle)]">
          No clips in this section.
        </div>
      ) : (
        <div className="grid gap-2.5">
          {incidents.map(incident => (
            <LibraryItem
              key={incidentActionId(incident)}
              incident={incident}
              onOpen={onOpen}
              onOpenFiles={onOpenFiles}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function FilesDrawer({
  incident,
  error,
  onClose,
  onOpenFolder,
}: {
  incident: MimirIncident
  error: string
  onClose: () => void
  onOpenFolder: (path: string) => void
}) {
  const clips = cameraClips(incident)
  const folderPath = incidentFolderPath(incident)

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/55 backdrop-blur-sm" onClick={onClose}>
      <aside
        className="h-full w-full max-w-[420px] overflow-y-auto border-l border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.55)]"
        onClick={event => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[12px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">Files</div>
            <h2 className="mt-1 text-[20px] font-semibold text-[var(--mimir-text)]">{eventLabel(incident)}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="h-9 rounded-md bg-white/[0.04] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)]"
          >
            Close
          </button>
        </div>

        <div className="mt-5 rounded-lg border border-white/[0.06] bg-white/[0.018] p-4">
          <div className="text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">Storage state</div>
          <div className="mt-2 text-[14px] font-semibold text-[var(--mimir-text)]">{currentStorageState(incident)}</div>
        </div>

        <div className="mt-4 rounded-lg border border-white/[0.06] bg-white/[0.018] p-4">
          <div className="mb-3 text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">Camera clips</div>
          {clips.length === 0 ? (
            <div className="text-[13px] text-[var(--mimir-text-muted)]">No camera clip list was included.</div>
          ) : (
            <div className="space-y-2">
              {clips.map((clip, index) => {
                const name = clip.filename || sourceFilename(clip.path || clip.video_path || clip.source_video || clip.source_clip || '')
                const clipState = clip.trash_path ? 'Trash' : clip.library_path ? 'Library' : clip.exists === false ? 'Missing' : 'Source'

                return (
                  <div key={`${clip.camera || 'camera'}-${name || index}`} className="rounded-md bg-black/16 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className="min-w-0 truncate text-[13px] font-medium text-[var(--mimir-text)]">
                        {cameraNameLabel(clip.camera) || `Camera ${index + 1}`}
                      </span>
                      <span className="shrink-0 text-[11px] text-[var(--mimir-text-subtle)]">{clipState}</span>
                    </div>
                    <div className="mt-1 truncate text-[12px] text-[var(--mimir-text-subtle)]">{name || 'Filename not available'}</div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={() => onOpenFolder(folderPath)}
            disabled={!folderPath}
            className="h-10 rounded-lg border border-white/[0.08] bg-white/[0.035] px-4 text-[13px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.065] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Open in Explorer
          </button>
        </div>

        {error && (
          <div className="mt-3 rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/86">
            {error}
          </div>
        )}
      </aside>
    </div>
  )
}

function SelectionToolbar({
  count,
  busy,
  onSetStatus,
  onMoveToLibrary,
  onMoveToTrash,
  onClear,
}: {
  count: number
  busy: boolean
  onSetStatus: (status: SeverityGroup) => void
  onMoveToLibrary: () => void
  onMoveToTrash: () => void
  onClear: () => void
}) {
  return (
    <div className="sticky top-0 z-20 mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/[0.08] bg-[rgba(23,24,24,0.94)] px-4 py-3 shadow-[0_18px_48px_rgba(0,0,0,0.38)] backdrop-blur">
      <div className="text-[13px] font-medium text-[var(--mimir-text)]">{count} selected</div>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy || count === 0} onClick={() => onSetStatus('IMPORTANT')} className="h-9 rounded-md bg-white/[0.04] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-50">Mark Important</button>
        <button type="button" disabled={busy || count === 0} onClick={() => onSetStatus('REVIEW')} className="h-9 rounded-md bg-white/[0.04] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-50">Mark Review</button>
        <button type="button" disabled={busy || count === 0} onClick={() => onSetStatus('IGNORE')} className="h-9 rounded-md bg-white/[0.04] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-50">Mark Ignore</button>
        <button type="button" disabled={busy || count === 0} onClick={onMoveToLibrary} className="h-9 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-50">Move to Library</button>
        <button type="button" disabled={busy || count === 0} onClick={onMoveToTrash} className="h-9 rounded-md border border-red-300/18 bg-red-500/10 px-3 text-[12px] font-semibold text-red-100/88 transition hover:bg-red-500/16 disabled:cursor-not-allowed disabled:opacity-50">Move to Mimir Trash</button>
        <button type="button" disabled={busy} onClick={onClear} className="h-9 rounded-md bg-transparent px-3 text-[12px] font-medium text-[var(--mimir-text-subtle)] transition hover:text-[var(--mimir-text)] disabled:opacity-60">Clear selection</button>
      </div>
    </div>
  )
}

function EmptyLibraryState({ filter, incidentCount }: { filter: LibraryFilter; incidentCount: number }) {
  const copy = (() => {
    if (filter === 'ALL' && incidentCount === 0) {
      return {
        title: 'No reviewable incidents found. Your clips were scanned and nothing suspicious was detected.',
        body: '',
      }
    }

    if (filter === 'IMPORTANT') {
      return {
        title: 'No important incidents found.',
        body: 'Review and All may still include moments worth checking.',
      }
    }

    if (filter === 'REVIEW') {
      return {
        title: 'No review incidents found.',
        body: 'Important and All may still include detected moments.',
      }
    }

    if (filter === 'ALL') {
      return {
        title: 'No reviewable incidents found.',
        body: 'Your clips were scanned and nothing suspicious was detected.',
      }
    }

    if (filter === 'TRASH') {
      return {
        title: 'No clips in Mimir Trash.',
        body: 'Clips moved to Mimir Trash will appear here.',
      }
    }

    if (filter === 'IGNORE') {
      return {
        title: 'No ignored incident cards.',
        body: 'Ignored clips are counted in the scan summary. Mimir does not create fake cards for ignored clips.',
      }
    }

    return {
      title: 'No incident cards found.',
      body: 'Try another filter or load a newer scan.',
    }
  })()

  return (
    <div className="grid min-h-[340px] place-items-center rounded-xl bg-white/[0.018] p-10 text-center">
      <div>
        <div className="mx-auto mb-5 h-1 w-14 rounded-full bg-white/20" />
        <h3 className="text-[24px] font-semibold text-[var(--mimir-text)]">{copy.title}</h3>
        <p className="mt-3 text-[15px] text-[var(--mimir-text-muted)]">{copy.body}</p>
      </div>
    </div>
  )
}

export function IncidentLibraryView({
  session,
  loadState,
  onImportNew,
  onLoadLatest,
  onReloadSession,
}: IncidentLibraryViewProps) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<LibraryFilter>('ALL')
  const [page, setPage] = useState<ReviewPage>('review')
  const [selectedIncident, setSelectedIncident] = useState<MimirIncident | null>(null)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [filesIncident, setFilesIncident] = useState<MimirIncident | null>(null)
  const [showFreeUpModal, setShowFreeUpModal] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkMessage, setBulkMessage] = useState('')
  const [storageOpenError, setStorageOpenError] = useState('')
  const isLoading = loadState === 'loading'

  const allSortedIncidents = useMemo(() => sortIncidents(session.incidents ?? []), [session.incidents])
  const sortedIncidents = useMemo(
    () => allSortedIncidents.filter(incident => !incident.user_deleted),
    [allSortedIncidents],
  )
  const deletedIncidents = useMemo(
    () => allSortedIncidents.filter(incident => incident.user_deleted),
    [allSortedIncidents],
  )
  const incidentCount = sortedIncidents.length
  const counts = useMemo(
    () => ({
      important: session.important ?? 0,
      review: session.review ?? 0,
      ignore: session.ignore ?? 0,
      all: incidentCount,
      trash: deletedIncidents.length,
    }),
    [deletedIncidents.length, incidentCount, session.ignore, session.important, session.review],
  )

  const visibleIncidents = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    const baseIncidents = filter === 'TRASH' ? deletedIncidents : sortedIncidents

    return baseIncidents.filter(incident => {
      const severityMatches =
        filter === 'ALL' || filter === 'TRASH' || normalizeSeverity(incident.severity) === filter
      const queryMatches = !normalizedQuery || searchText(incident).includes(normalizedQuery)

      return severityMatches && queryMatches
    })
  }, [deletedIncidents, filter, query, sortedIncidents])
  const filterDescription =
    filter === 'ALL'
      ? `Showing ${visibleIncidents.length} ${pluralize(visibleIncidents.length, 'incident card')}.`
      : filter === 'TRASH'
        ? `Showing ${visibleIncidents.length} trashed ${pluralize(visibleIncidents.length, 'incident card')}.`
      : `Showing ${visibleIncidents.length} ${filter.toLowerCase()} ${pluralize(
          visibleIncidents.length,
          'incident card',
        )}.`
  const selectedIncidents = useMemo(
    () => allSortedIncidents.filter(incident => selectedIds.has(incidentActionId(incident))),
    [allSortedIncidents, selectedIds],
  )
  const hasSourceClips = useMemo(() => sourceClipsRemain(sortedIncidents), [sortedIncidents])
  const librarySections = useMemo(
    () => ({
      important: incidentsForSeverity(sortedIncidents, 'IMPORTANT'),
      review: incidentsForSeverity(sortedIncidents, 'REVIEW'),
      ignore: incidentsForSeverity(sortedIncidents, 'IGNORE'),
      trash: deletedIncidents,
    }),
    [deletedIncidents, sortedIncidents],
  )

  useEffect(() => {
    if (filter === 'TRASH' && counts.trash === 0) {
      setFilter('ALL')
    }
  }, [counts.trash, filter])

  useEffect(() => {
    if (page !== 'review' && selectionMode) {
      cancelSelectionMode()
    }
  }, [page, selectionMode])

  const toggleIncidentSelected = (incident: MimirIncident) => {
    const id = incidentActionId(incident)
    if (!id) {
      return
    }

    setSelectedIds(current => {
      const next = new Set(current)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const cancelSelectionMode = () => {
    setSelectionMode(false)
    setSelectedIds(new Set())
  }

  const runBulkAction = async (
    incidents: MimirIncident[],
    action: 'set_status' | 'move_to_library' | 'delete',
    status?: SeverityGroup,
  ) => {
    const actionable = incidents.filter(incident => incidentActionId(incident))
    if (actionable.length === 0) {
      setBulkMessage('No incidents were selected.')
      return
    }

    setBulkBusy(true)
    setBulkMessage('')
    setStorageOpenError('')

    const failures: string[] = []

    for (const incident of actionable) {
      try {
        await invoke('run_incident_action', {
          incidentId: incidentActionId(incident),
          action,
          status: status ?? null,
        })
      } catch (error) {
        failures.push(`${eventLabel(incident)}: ${errorMessage(error)}`)
      }
    }

    try {
      await onReloadSession()
    } catch (error) {
      failures.push(`Refresh failed: ${errorMessage(error)}`)
    }

    if (failures.length > 0) {
      setStorageOpenError(failures.join('\n'))
      setBulkMessage(`${actionable.length - failures.length} completed, ${failures.length} failed.`)
    } else {
      const actionCopy =
        action === 'delete'
          ? 'moved to Mimir Trash'
          : action === 'move_to_library'
            ? 'moved to Mimir Library'
            : `marked ${severityCopy(status)}`
      setBulkMessage(`${actionable.length} ${pluralize(actionable.length, 'incident')} ${actionCopy}.`)
      setSelectedIds(new Set())
      setShowFreeUpModal(false)
    }

    setBulkBusy(false)
  }

  const openMimirStorageFolder = async (kind: 'library' | 'trash') => {
    setStorageOpenError('')

    try {
      await invoke<void>('open_mimir_storage_folder', { kind })
    } catch (error) {
      setStorageOpenError(errorMessage(error))
    }
  }

  const openContainingFolder = async (path: string) => {
    setStorageOpenError('')

    try {
      await invoke<void>('open_containing_folder', { path })
    } catch (error) {
      setStorageOpenError(errorMessage(error))
    }
  }

  const openFeedbackFolder = async () => {
    setStorageOpenError('')

    try {
      const documentsPath = await documentDir()
      await invoke<void>('open_containing_folder', { path: `${documentsPath}Mimir Feedback` })
    } catch (error) {
      setStorageOpenError(errorMessage(error))
    }
  }

  if (selectedIncident) {
    const attemptedVideoPath =
      selectedIncident.video_path ||
      selectedIncident.library_video_path ||
      selectedIncident.source_video ||
      selectedIncident.original_source_video ||
      ''

    return (
      <CrashSafeBoundary
        title="Incident viewer error"
        incidentId={selectedIncident.id || String(selectedIncident.event_id ?? '')}
        attemptedVideoPath={attemptedVideoPath}
        onBack={() => setSelectedIncident(null)}
        onOpenFolder={path => void openContainingFolder(path)}
      >
        <IncidentViewerScreen
          incident={selectedIncident}
          onBack={() => setSelectedIncident(null)}
          onReloadSession={onReloadSession}
          onIncidentUpdated={updatedIncident => {
            setSelectedIncident(updatedIncident)
          }}
        />
      </CrashSafeBoundary>
    )
  }

  return (
    <CrashSafeBoundary title="Incident library error" onBack={onImportNew}>
    <main className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1440px] flex-col overflow-hidden rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-bg-depth)] shadow-[0_28px_90px_rgba(0,0,0,0.5)] sm:min-h-[calc(100vh-48px)]">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/[0.055] px-5 py-5 lg:px-7">
        <div className="flex min-w-0 items-center gap-4">
          <img src={mimirLockup} alt="Mimir" className="h-8 w-auto shrink-0 opacity-95" />
          <div className="min-w-0">
            <div className="text-[14px] font-semibold text-[var(--mimir-text)]">
              {page === 'library' ? 'Mimir Library' : 'Current scan'}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-[var(--mimir-text-subtle)]">
              <span>{counts.important} Important</span>
              <span>{counts.review} Review</span>
              <span>{counts.ignore} Ignore</span>
              <span>{counts.all} total</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {page === 'review' && (
            <button
              type="button"
              onClick={() => {
                if (selectionMode) {
                  cancelSelectionMode()
                } else {
                  setSelectionMode(true)
                }
              }}
              className={`h-10 rounded-lg px-4 text-[13px] font-semibold transition ${
                selectionMode
                  ? 'border border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)] hover:bg-white/[0.065] hover:text-[var(--mimir-text)]'
                  : 'bg-[var(--mimir-text)] text-black hover:bg-white'
              }`}
            >
              {selectionMode ? 'Cancel' : 'Select'}
            </button>
          )}
          <div className="relative">
            <button
              type="button"
              onClick={() => setMoreOpen(open => !open)}
              className="h-10 rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.065] hover:text-[var(--mimir-text)]"
            >
              More
            </button>
            {moreOpen && (
              <div className="absolute right-0 top-12 z-30 w-56 overflow-hidden rounded-xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-1.5 shadow-[0_18px_60px_rgba(0,0,0,0.48)]">
                <button
                  type="button"
                  onClick={() => {
                    setPage(page === 'review' ? 'library' : 'review')
                    cancelSelectionMode()
                    setMoreOpen(false)
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  {page === 'review' ? 'Browse Library' : 'Back to Review'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false)
                    void openMimirStorageFolder('library')
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  Open Mimir Library
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false)
                    void openMimirStorageFolder('trash')
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  Open Mimir Trash
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false)
                    void openFeedbackFolder()
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  Export feedback
                </button>
                {hasSourceClips && (
                  <button
                    type="button"
                    onClick={() => {
                      setMoreOpen(false)
                      setShowFreeUpModal(true)
                    }}
                    className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                  >
                    Storage cleanup
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false)
                    void onLoadLatest()
                  }}
                  disabled={isLoading}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-wait disabled:opacity-60"
                >
                  {isLoading ? 'Loading...' : 'Refresh'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false)
                    onImportNew()
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  Import New Footage
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <section className="flex-1 overflow-y-auto px-5 pb-7 pt-3 lg:px-7">
        {page === 'library' ? (
          <>
            <div className="mb-5">
              <div>
                <div className="text-[12px] font-medium uppercase tracking-[0.2em] text-[var(--mimir-text-subtle)]">
                  Mimir Library
                </div>
                <h1 className="mt-1 text-[30px] font-semibold text-[var(--mimir-text)]">Browse saved clips</h1>
                <p className="mt-2 max-w-[780px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
                  Browse incidents from the latest session, including items still on the original source, clips moved to Mimir Library, and clips in Mimir Trash.
                </p>
              </div>
            </div>

            {(storageOpenError || bulkMessage) && (
              <div
                className={`mb-4 whitespace-pre-wrap rounded-lg border p-3 text-[12px] leading-5 ${
                  storageOpenError
                    ? 'border-red-300/20 bg-red-500/10 text-red-100/86'
                    : 'border-white/[0.08] bg-white/[0.025] text-[var(--mimir-text-muted)]'
                }`}
              >
                {storageOpenError || bulkMessage}
              </div>
            )}

            <div className="grid gap-6 xl:grid-cols-2">
              <LibrarySection title="Important" incidents={librarySections.important} onOpen={setSelectedIncident} onOpenFiles={setFilesIncident} />
              <LibrarySection title="Review" incidents={librarySections.review} onOpen={setSelectedIncident} onOpenFiles={setFilesIncident} />
              <LibrarySection title="Ignore" incidents={librarySections.ignore} onOpen={setSelectedIncident} onOpenFiles={setFilesIncident} />
              <LibrarySection title="Trash" incidents={librarySections.trash} onOpen={setSelectedIncident} onOpenFiles={setFilesIncident} />
            </div>
          </>
        ) : (
          <>
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-[12px] font-medium uppercase tracking-[0.2em] text-[var(--mimir-text-subtle)]">
              Scan summary
            </div>
            <h1 className="mt-1 text-[30px] font-semibold text-[var(--mimir-text)]">Review incidents</h1>
            <p className="mt-2 max-w-[780px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              {scanResultCopy(session)}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <SummaryMetric label="Important" value={counts.important} />
            <SummaryMetric label="Review" value={counts.review} />
            <SummaryMetric label="Ignore" value={counts.ignore} />
          </div>
        </div>

        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            <FilterChip label="Important" count={counts.important} active={filter === 'IMPORTANT'} onClick={() => setFilter('IMPORTANT')} />
            <FilterChip label="Review" count={counts.review} active={filter === 'REVIEW'} onClick={() => setFilter('REVIEW')} />
            <FilterChip label="Ignore" count={counts.ignore} active={filter === 'IGNORE'} onClick={() => setFilter('IGNORE')} />
            <FilterChip label="All" count={counts.all} active={filter === 'ALL'} onClick={() => setFilter('ALL')} />
            {counts.trash > 0 && (
              <FilterChip label="Trash" count={counts.trash} active={filter === 'TRASH'} onClick={() => setFilter('TRASH')} />
            )}
          </div>
          <label className="relative block w-full max-w-[360px]">
            <span className="sr-only">Search incidents</span>
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Search incidents"
              className="h-10 w-full rounded-lg bg-white/[0.03] px-3 text-[13px] text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:bg-white/[0.055]"
            />
          </label>
        </div>

        {selectionMode && (
          <SelectionToolbar
            count={selectedIds.size}
            busy={bulkBusy}
            onSetStatus={status => void runBulkAction(selectedIncidents, 'set_status', status)}
            onMoveToLibrary={() => void runBulkAction(selectedIncidents, 'move_to_library')}
            onMoveToTrash={() => void runBulkAction(selectedIncidents, 'delete')}
            onClear={() => setSelectedIds(new Set())}
          />
        )}

        {(storageOpenError || bulkMessage) && (
          <div
            className={`mb-4 whitespace-pre-wrap rounded-lg border p-3 text-[12px] leading-5 ${
              storageOpenError
                ? 'border-red-300/20 bg-red-500/10 text-red-100/86'
                : 'border-white/[0.08] bg-white/[0.025] text-[var(--mimir-text-muted)]'
            }`}
          >
            {storageOpenError || bulkMessage}
          </div>
        )}

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-[17px] font-semibold text-[var(--mimir-text)]">
            {filter === 'TRASH' ? 'Mimir Trash' : `${filter[0]}${filter.slice(1).toLowerCase()} incidents`}
          </h2>
          <p className="text-[13px] text-[var(--mimir-text-subtle)]">{filterDescription}</p>
        </div>

        {visibleIncidents.length === 0 ? (
          <EmptyLibraryState filter={filter} incidentCount={incidentCount} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-3.5">
            {visibleIncidents.map(incident => (
              <IncidentCard
                key={incidentActionId(incident)}
                incident={incident}
                selected={selectedIds.has(incidentActionId(incident))}
                selectionMode={selectionMode}
                onOpen={setSelectedIncident}
                onToggleSelected={toggleIncidentSelected}
                onOpenFiles={setFilesIncident}
              />
            ))}
          </div>
        )}
          </>
        )}
      </section>

      {filesIncident && (
        <FilesDrawer
          incident={filesIncident}
          error={storageOpenError}
          onClose={() => setFilesIncident(null)}
          onOpenFolder={path => void openContainingFolder(path)}
        />
      )}

      {showFreeUpModal && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[520px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[19px] font-semibold text-[var(--mimir-text)]">Move reviewed clips to Mimir Library?</div>
            <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir only removes files from the USB after they are safely moved.
            </p>
            <div className="mt-5 grid gap-2">
              <button
                type="button"
                disabled={bulkBusy || selectedIncidents.length === 0}
                onClick={() => void runBulkAction(selectedIncidents, 'move_to_library')}
                className="h-11 rounded-lg bg-white/[0.04] px-4 text-left text-[13px] font-medium text-[var(--mimir-text)] transition hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Move selected
              </button>
              <button
                type="button"
                disabled={bulkBusy}
                onClick={() =>
                  void runBulkAction(
                    sortedIncidents.filter(incident => ['IMPORTANT', 'REVIEW'].includes(normalizeSeverity(incident.severity))),
                    'move_to_library',
                  )
                }
                className="h-11 rounded-lg bg-white/[0.04] px-4 text-left text-[13px] font-medium text-[var(--mimir-text)] transition hover:bg-white/[0.07] disabled:cursor-wait disabled:opacity-60"
              >
                Move all Important and Review
              </button>
              <button
                type="button"
                disabled={bulkBusy}
                onClick={() => void runBulkAction(sortedIncidents, 'move_to_library')}
                className="h-11 rounded-lg bg-white/[0.04] px-4 text-left text-[13px] font-medium text-[var(--mimir-text)] transition hover:bg-white/[0.07] disabled:cursor-wait disabled:opacity-60"
              >
                Move all from this scan, including Ignore
              </button>
              <button
                type="button"
                disabled={bulkBusy}
                onClick={() => setShowFreeUpModal(false)}
                className="h-10 rounded-lg bg-transparent px-4 text-[13px] font-medium text-[var(--mimir-text-subtle)] transition hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
    </CrashSafeBoundary>
  )
}
