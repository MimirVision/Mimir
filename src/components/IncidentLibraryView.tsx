import { useEffect, useMemo, useState } from 'react'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { documentDir } from '@tauri-apps/api/path'
import mimirLockup from '../assets/mimir-lockup.png'
import { CrashSafeBoundary } from './CrashSafeBoundary'
import { ErrorNotice } from './ErrorNotice'
import { describeError, describeFailures, type DescribedError } from '../lib/errorMessages'
import { IncidentViewerScreen } from './IncidentViewerScreen'
import { MIMIR_VERSION } from '../config'
import { contributionFileName, readContributorIdentity, rightsBasisLabel } from '../lib/contributionIdentity'
import { formatDateTime, sourceEventTimestamp, sourceFilename } from '../lib/incidentDisplay'
import {
  incidentOverrideKey,
  normalizeSeverity,
  resolveIncidentSeverity,
  sessionIdentity,
  type ManualStatusOverrides,
  type SeverityGroup,
  type SeverityResolution,
} from '../lib/incidentStatus'
import {
  severityClass,
  severityCopy,
  severityStripeClass,
  storageBadgeClass,
} from '../lib/incidentLibraryStyles'
import {
  cameraClips,
  cameraCountLabel,
  cameraNameLabel,
  currentStorageState,
  eventLabel,
  formatTechnicalValue,
  incidentActionId,
  incidentCardImages,
  incidentCardSubtitle,
  incidentCardTitle,
  incidentFolderPath,
  incidentsForSeverity,
  isGenericBestEffortIncident,
  pluralize,
  reviewModeCopy,
  scanResultCopy,
  searchText,
  sessionFeatureFlags,
  sessionMayBeStale,
  sortIncidents,
  sourceClipsRemain,
  technicalJson,
} from '../lib/incidentLibraryHelpers'
import type {
  FrontendScanDiagnostics,
  MimirIncident,
  MimirSession,
  SessionLoadState,
} from '../types'

type LibraryFilter = 'IMPORTANT' | 'REVIEW' | 'IGNORE' | 'ALL' | 'TRASH'
type ReviewPage = 'review' | 'library'

interface IncidentLibraryViewProps {
  session: MimirSession
  loadState: SessionLoadState
  onImportNew: () => void
  onLoadLatest: () => void
  onReloadSession: () => Promise<MimirSession | null>
  scanDiagnostics?: FrontendScanDiagnostics | null
}

// ============================================================================
// Small shared presentational components
// ============================================================================

function IncidentImage({ incident, large = false }: { incident: MimirIncident; large?: boolean }) {
  const images = incidentCardImages(incident)
  const [failedIndex, setFailedIndex] = useState(0)
  const imagePath = images[failedIndex]

  return (
    <div
      className={`relative overflow-hidden rounded-md bg-black/32 ${
        large ? 'min-h-[320px]' : 'h-[154px]'
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
        <div className="flex h-full items-center justify-center px-4 text-center text-[12px] text-[var(--mimir-text-subtle)] opacity-75">
          Thumbnail unavailable
        </div>
      )}
    </div>
  )
}

function SummaryMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl bg-white/[0.026] px-3.5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
      <div className="text-[11px] font-medium text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-1 text-[21px] font-semibold text-[var(--mimir-text)]">{value}</div>
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
      className={`h-9 rounded-full border px-3.5 text-[13px] font-medium transition ${
        active
          ? 'border-[rgba(157,183,170,0.28)] bg-[var(--mimir-accent-soft)] text-[var(--mimir-text)]'
          : 'border-white/[0.055] bg-white/[0.014] text-[var(--mimir-text-muted)] hover:bg-white/[0.045] hover:text-[var(--mimir-text)]'
      }`}
    >
      {label} <span className="ml-1 text-[var(--mimir-text-subtle)]">{count}</span>
    </button>
  )
}

// ============================================================================
// Camera/path/storage helpers for incident cards
// ============================================================================

// ============================================================================
// Main components: cards, sections, drawers. IncidentLibraryView (below)
// composes all of these into the review/library screen.
// ============================================================================

function IncidentCard({
  incident,
  severityResolution,
  selected,
  selectionMode,
  onOpen,
  onToggleSelected,
}: {
  incident: MimirIncident
  severityResolution: SeverityResolution
  selected: boolean
  selectionMode: boolean
  onOpen: (incident: MimirIncident) => void
  onToggleSelected: (incident: MimirIncident) => void
}) {
  const title = incidentCardTitle(severityResolution.displaySeverity)
  const subtitle = incidentCardSubtitle(incident)

  return (
    <article
      className={`group relative min-w-0 overflow-hidden rounded-xl border bg-[linear-gradient(180deg,rgba(255,255,255,0.032),rgba(255,255,255,0.014))] shadow-[0_16px_42px_rgba(0,0,0,0.20)] transition before:absolute before:inset-y-0 before:left-0 before:z-10 before:w-[3px] before:content-[''] hover:-translate-y-0.5 hover:border-white/[0.12] hover:bg-white/[0.038] ${severityStripeClass(
        severityResolution.displaySeverity,
      )} ${selected ? 'border-[rgba(157,183,170,0.42)] ring-1 ring-[rgba(157,183,170,0.22)]' : 'border-white/[0.05]'}`}
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
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-black/82 via-black/28 to-transparent opacity-75 transition group-hover:opacity-95" />
          {selectionMode && (
            <label
              className="absolute left-2.5 top-2.5 grid h-7 w-7 place-items-center rounded-full border border-white/16 bg-black/50 backdrop-blur"
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
              severityResolution.displaySeverity,
            )}`}
          >
            {severityCopy(severityResolution.displaySeverity)}
          </span>
        </div>

        <div className="min-w-0 px-3.5 pb-3.5 pt-3">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <div className="min-w-0 truncate text-[15px] font-semibold text-[var(--mimir-text)]">{title}</div>
          </div>

          {subtitle && (
            <div className="mt-1.5 truncate text-[12px] text-[var(--mimir-text-subtle)]">
              {subtitle}
            </div>
          )}

          <div className="mt-3 flex min-w-0 flex-wrap gap-1.5 text-[11px] leading-4 text-[var(--mimir-text-subtle)]">
            <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[var(--mimir-text-muted)]">
              {cameraCountLabel(incident)}
            </span>
            {isGenericBestEffortIncident(incident) && (
              <span
                className="rounded-full bg-white/[0.035] px-2 py-0.5 text-[var(--mimir-text-subtle)]"
                title="Generic MP4 review is best-effort. Tesla Sentry camera groups receive full support."
              >
                Generic video · best effort
              </span>
            )}
            {severityResolution.isManualOverride && (
              <span className="rounded-full bg-[var(--mimir-accent-soft)] px-2 py-0.5 text-[var(--mimir-text-muted)]">
                Changed by you
              </span>
            )}
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
  severityResolution,
  onOpen,
  onOpenFiles,
}: {
  incident: MimirIncident
  severityResolution: SeverityResolution
  onOpen: (incident: MimirIncident) => void
  onOpenFiles: (incident: MimirIncident) => void
}) {
  const timestamp = formatDateTime(sourceEventTimestamp(incident))

  return (
    <article
      className={`relative grid gap-3 overflow-hidden rounded-lg border border-white/[0.055] bg-white/[0.018] p-2.5 pl-4 transition before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:content-[''] hover:bg-white/[0.04] sm:grid-cols-[132px_minmax(0,1fr)_auto] sm:items-center ${severityStripeClass(
        severityResolution.displaySeverity,
      )}`}
    >
      <button type="button" onClick={() => onOpen(incident)} className="block overflow-hidden rounded-md text-left">
        <IncidentImage incident={incident} />
      </button>

      <button type="button" onClick={() => onOpen(incident)} className="min-w-0 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${severityClass(severityResolution.displaySeverity)}`}>
            {severityCopy(severityResolution.displaySeverity)}
          </span>
          {severityResolution.isManualOverride && (
            <span className="rounded-full bg-white/[0.045] px-2 py-0.5 text-[11px] text-[var(--mimir-text-muted)]">
              Changed by you
            </span>
          )}
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
  manualOverrides,
  identity,
  onOpen,
  onOpenFiles,
}: {
  title: string
  incidents: MimirIncident[]
  manualOverrides: ManualStatusOverrides
  identity: string
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
              severityResolution={resolveIncidentSeverity(incident, manualOverrides, identity)}
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
  error: DescribedError | null
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

        <ErrorNotice error={error} className="mt-3" />
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
  onContribute,
  canContribute,
  onClear,
}: {
  count: number
  busy: boolean
  onSetStatus: (status: SeverityGroup) => void
  onMoveToLibrary: () => void
  onMoveToTrash: () => void
  onContribute: () => void
  canContribute: boolean
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
        {canContribute && (
          <button type="button" disabled={busy || count === 0} onClick={onContribute} className="h-9 rounded-md border border-[rgba(157,183,170,0.24)] bg-[var(--mimir-accent-soft)] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-[rgba(157,183,170,0.18)] disabled:cursor-not-allowed disabled:opacity-50">Contribute selected</button>
        )}
        <button type="button" disabled={busy} onClick={onClear} className="h-9 rounded-md bg-transparent px-3 text-[12px] font-medium text-[var(--mimir-text-subtle)] transition hover:text-[var(--mimir-text)] disabled:opacity-60">Clear selection</button>
      </div>
    </div>
  )
}

function EmptyLibraryState({ filter, incidentCount }: { filter: LibraryFilter; incidentCount: number }) {
  const copy = (() => {
    if (filter === 'ALL' && incidentCount === 0) {
      return {
        title: 'Nothing needs review.',
        body: 'Mimir scanned the clips and did not find concerning moments.',
      }
    }

    if (filter === 'IMPORTANT') {
      return {
        title: 'No important incidents.',
        body: 'Moments worth a closer look may still appear under Review.',
      }
    }

    if (filter === 'REVIEW') {
      return {
        title: 'No review items.',
        body: 'Important moments and ignored clips are available in the other filters.',
      }
    }

    if (filter === 'ALL') {
      return {
        title: 'Nothing needs review.',
        body: 'Mimir scanned the clips and did not find concerning moments.',
      }
    }

    if (filter === 'TRASH') {
      return {
        title: 'Mimir Trash is empty.',
        body: 'Clips you move to trash will appear here.',
      }
    }

    if (filter === 'IGNORE') {
      return {
        title: 'No ignored clips to show.',
        body: 'Ignored activity will appear here when a scan includes it.',
      }
    }

    return {
      title: 'No incidents found.',
      body: 'Try another filter or load a newer scan.',
    }
  })()

  return (
    <div className="grid min-h-[340px] place-items-center rounded-xl bg-white/[0.018] p-10 text-center">
      <div>
        <div className="mx-auto mb-5 h-1 w-14 rounded-full bg-white/20" />
        <h3 className="text-[22px] font-semibold text-[var(--mimir-text)]">{copy.title}</h3>
        {copy.body && <p className="mt-3 text-[14px] text-[var(--mimir-text-muted)]">{copy.body}</p>}
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
  scanDiagnostics,
}: IncidentLibraryViewProps) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<LibraryFilter>('ALL')
  const [page, setPage] = useState<ReviewPage>('review')
  const [selectedIncident, setSelectedIncident] = useState<MimirIncident | null>(null)
  const [selectionMode, setSelectionMode] = useState(false)
  // Batch contribute only appears once consent details exist, so the first
  // contribution always goes through the full form in the incident viewer.
  const hasSavedContributorIdentity = Boolean(readContributorIdentity())
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [filesIncident, setFilesIncident] = useState<MimirIncident | null>(null)
  const [showFreeUpModal, setShowFreeUpModal] = useState(false)
  const [showAboutModal, setShowAboutModal] = useState(false)
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkMessage, setBulkMessage] = useState('')
  const [storageOpenError, setStorageOpenError] = useState<DescribedError | null>(null)
  const [manualOverrides, setManualOverrides] = useState<ManualStatusOverrides>({})
  const isLoading = loadState === 'loading'
  const identity = useMemo(() => sessionIdentity(session, scanDiagnostics), [scanDiagnostics, session])

  useEffect(() => {
    setManualOverrides({})
    setSelectedIds(new Set())
    setBulkMessage('')
    setStorageOpenError(null)
  }, [identity])

  const applyManualStatusLocally = (incident: MimirIncident, status: SeverityGroup) => {
    const key = incidentOverrideKey(incident, identity)
    const mimirSeverity = normalizeSeverity(incident.final_severity || incident.severity)

    setManualOverrides(current => {
      const next = { ...current }

      if (status === mimirSeverity) {
        delete next[key]
      } else {
        next[key] = status
      }

      return next
    })
  }

  // Awaitable so bulk actions can persist one at a time. Firing these
  // concurrently used to lose writes: each call is a read-modify-write of the
  // session file, so parallel callers all read the same pre-state and the last
  // one to finish silently discarded the rest.
  const persistManualStatus = async (incident: MimirIncident, status: SeverityGroup) => {
    const id = incidentActionId(incident)
    if (!id) {
      return
    }

    await invoke('save_manual_status', {
      sessionPath: session?.session_archive_path || session?.output_path || null,
      incidentId: id,
      status,
    })
  }

  const setManualIncidentStatus = (incident: MimirIncident, status: SeverityGroup) => {
    applyManualStatusLocally(incident, status)
    void persistManualStatus(incident, status).catch(error => {
      setStorageOpenError(describeError(error, 'That status change could not be saved to the session file.'))
    })
  }

  const allSortedIncidents = useMemo(
    () => sortIncidents(session.incidents ?? [], manualOverrides, identity),
    [identity, manualOverrides, session.incidents],
  )
  const sortedIncidents = useMemo(
    () => allSortedIncidents.filter(incident => !incident.user_deleted),
    [allSortedIncidents],
  )
  const deletedIncidents = useMemo(
    () => allSortedIncidents.filter(incident => incident.user_deleted),
    [allSortedIncidents],
  )
  const incidentCount = sortedIncidents.length
  const counts = useMemo(() => {
    const severityCounts = sortedIncidents.reduce(
      (accumulator, incident) => {
        const severity = resolveIncidentSeverity(incident, manualOverrides, identity).displaySeverity
        accumulator[severity.toLowerCase() as 'important' | 'review' | 'ignore'] += 1
        return accumulator
      },
      { important: 0, review: 0, ignore: 0 },
    )

    return {
      ...severityCounts,
      all: incidentCount,
      trash: deletedIncidents.length,
    }
  }, [deletedIncidents.length, identity, incidentCount, manualOverrides, sortedIncidents])

  const visibleIncidents = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    const baseIncidents = filter === 'TRASH' ? deletedIncidents : sortedIncidents

    return baseIncidents.filter(incident => {
      const severityMatches =
        filter === 'ALL' ||
        filter === 'TRASH' ||
        resolveIncidentSeverity(incident, manualOverrides, identity).displaySeverity === filter
      const queryMatches = !normalizedQuery || searchText(incident).includes(normalizedQuery)

      return severityMatches && queryMatches
    })
  }, [deletedIncidents, filter, identity, manualOverrides, query, sortedIncidents])
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
      important: incidentsForSeverity(sortedIncidents, 'IMPORTANT', manualOverrides, identity),
      review: incidentsForSeverity(sortedIncidents, 'REVIEW', manualOverrides, identity),
      ignore: incidentsForSeverity(sortedIncidents, 'IGNORE', manualOverrides, identity),
      trash: deletedIncidents,
    }),
    [deletedIncidents, identity, manualOverrides, sortedIncidents],
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
    setStorageOpenError(null)

    const failures: { label: string; error: unknown }[] = []

    if (action === 'set_status' && status) {
      let saved = 0
      for (const incident of actionable) {
        applyManualStatusLocally(incident, status)
        try {
          await persistManualStatus(incident, status)
          saved += 1
        } catch (error) {
          failures.push({ label: eventLabel(incident), error })
        }
      }

      if (failures.length > 0) {
        setBulkMessage(`${saved} marked ${severityCopy(status)}, ${failures.length} failed.`)
        setStorageOpenError(
          describeFailures(failures, 'Some status changes could not be saved:', 'The session file could not be updated.'),
        )
      } else {
        setBulkMessage(`${saved} ${pluralize(saved, 'incident')} marked ${severityCopy(status)}.`)
        setSelectedIds(new Set())
      }

      setShowFreeUpModal(false)
      setBulkBusy(false)
      return
    }

    for (const incident of actionable) {
      try {
        await invoke('run_core_v2_storage_action', {
          sessionPath: session?.session_archive_path || session?.output_path || null,
          incidentId: incidentActionId(incident),
          action: action === 'delete' ? 'move_to_trash' : 'move_to_library',
        })
      } catch (error) {
        failures.push({ label: eventLabel(incident), error })
      }
    }

    try {
      await onReloadSession()
    } catch (error) {
      failures.push({ label: 'Refreshing the library', error })
    }

    if (failures.length > 0) {
      setBulkMessage(`${actionable.length - failures.length} completed, ${failures.length} failed.`)
      setStorageOpenError(describeFailures(failures, 'Some clips could not be moved:', 'The clip could not be moved.'))
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

  // Batch contribute: one confirmation for the whole selection instead of
  // walking the consent form once per incident. Uses the consent details the
  // user already saved; if none exist, the viewer's full form is still the
  // way in, so first-time consent is never silently skipped.
  const contributeSelected = async () => {
    const identity = readContributorIdentity()
    if (!identity) {
      setBulkMessage('Open one incident and contribute it once first, so your consent details are on file.')
      return
    }

    const actionable = selectedIncidents
    if (actionable.length === 0) {
      return
    }

    const confirmed = window.confirm(
      `Contribute ${actionable.length} ${pluralize(actionable.length, 'incident')} for model training?\n\n` +
        `Rights basis: ${rightsBasisLabel(identity.rightsBasis)} (${identity.recordedBy})\n\n` +
        'Each one is encrypted into your local Contributions folder. Nothing uploads automatically.',
    )
    if (!confirmed) {
      return
    }

    setBulkBusy(true)
    setBulkMessage('')
    setStorageOpenError(null)

    let folder = ''
    try {
      folder = await invoke<string>('default_contribution_folder')
    } catch (error) {
      setStorageOpenError(describeError(error, 'The Contributions folder could not be located.'))
      setBulkBusy(false)
      return
    }

    const separator = folder.includes('/') && !folder.includes('\\') ? '/' : '\\'
    const failures: { label: string; error: unknown }[] = []
    let exported = 0

    for (const incident of actionable) {
      const incidentId = incidentActionId(incident)
      try {
        await invoke('export_training_contribution', {
          sessionPath: session?.session_archive_path || session?.output_path || null,
          incidentId,
          outputPath: `${folder}${separator}${contributionFileName(incidentId, String(incident.source_stem || ''))}`,
          recordedBy: identity.recordedBy,
          rightsBasis: identity.rightsBasis,
          permissionReference: identity.permissionReference,
          independentPermissionRecord: null,
        })
        exported += 1
      } catch (error) {
        failures.push({ label: incidentId, error })
      }
    }

    if (failures.length > 0) {
      setBulkMessage(`${exported} contributed, ${failures.length} failed.`)
      setStorageOpenError(
        describeFailures(failures, 'Some incidents could not be contributed:', 'The contribution package could not be written.'),
      )
    } else {
      setBulkMessage(`${exported} ${pluralize(exported, 'incident')} contributed to ${folder}.`)
      setSelectedIds(new Set())
    }

    setBulkBusy(false)
  }

  const openMimirStorageFolder = async (kind: 'library' | 'trash') => {
    setStorageOpenError(null)

    try {
      await invoke<void>('open_mimir_storage_folder', { kind })
    } catch (error) {
      setStorageOpenError(describeError(error, 'That Mimir folder could not be opened.'))
    }
  }

  const openContainingFolder = async (path: string) => {
    setStorageOpenError(null)

    try {
      await invoke<void>('open_containing_folder', { path })
    } catch (error) {
      setStorageOpenError(describeError(error, 'That folder could not be opened.'))
    }
  }

  const openFeedbackFolder = async () => {
    setStorageOpenError(null)

    try {
      const documentsPath = await documentDir()
      await invoke<void>('open_containing_folder', { path: `${documentsPath}Mimir Feedback` })
    } catch (error) {
      setStorageOpenError(
        describeError(error, 'The Mimir Feedback folder could not be opened. It is created the first time you send feedback.'),
      )
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
            session={session}
            severityResolution={resolveIncidentSeverity(selectedIncident, manualOverrides, identity)}
            onBack={() => setSelectedIncident(null)}
            onReloadSession={onReloadSession}
            onManualStatusChange={status => setManualIncidentStatus(selectedIncident, status)}
            onIncidentUpdated={updatedIncident => {
              setSelectedIncident(updatedIncident)
            }}
        />
      </CrashSafeBoundary>
    )
  }

  return (
    <CrashSafeBoundary title="Incident library error" onBack={onImportNew}>
    <main className="flex min-h-screen w-full flex-col overflow-hidden bg-[radial-gradient(circle_at_50%_-10%,rgba(157,183,170,0.085),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.012)),var(--mimir-bg-depth)]">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/[0.04] px-5 py-4 lg:px-7">
        <div className="flex min-w-0 items-center gap-4">
          <img src={mimirLockup} alt="Mimir" className="h-7 w-auto shrink-0 opacity-95" />
          <div className="min-w-0">
            <div className="text-[13px] font-semibold text-[var(--mimir-text)]">
              {page === 'library' ? 'Mimir Library' : 'Current scan'}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-[var(--mimir-text-subtle)]">
              <span className="rounded-full bg-[rgba(196,119,114,0.08)] px-2 py-0.5">{counts.important} Important</span>
              <span className="rounded-full bg-[rgba(195,160,98,0.08)] px-2 py-0.5">{counts.review} Review</span>
              <span className="rounded-full bg-white/[0.024] px-2 py-0.5">{counts.ignore} Ignore</span>
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
              className={`h-9 rounded-lg border px-3.5 text-[12px] font-semibold transition ${
                selectionMode
                  ? 'border-white/18 bg-white/[0.075] text-[var(--mimir-text)] hover:bg-white/[0.1]'
                  : 'border-white/[0.065] bg-white/[0.022] text-[var(--mimir-text-muted)] hover:bg-white/[0.055] hover:text-[var(--mimir-text)]'
              }`}
            >
              {selectionMode ? 'Cancel' : 'Select'}
            </button>
          )}
          <div className="relative">
            <button
              type="button"
              onClick={() => setMoreOpen(open => !open)}
              className="h-9 rounded-lg border border-transparent bg-transparent px-3 text-[12px] font-medium text-[var(--mimir-text-subtle)] transition hover:border-white/[0.055] hover:bg-white/[0.035] hover:text-[var(--mimir-text)]"
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
                    setShowAboutModal(true)
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  About Mimir
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false)
                    setShowTechnicalDetails(true)
                  }}
                  className="block h-9 w-full rounded-lg px-3 text-left text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                >
                  Technical details
                </button>
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

      <section className="mx-auto w-full max-w-[1600px] flex-1 overflow-y-auto px-5 pb-7 pt-4 lg:px-7">
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

            <ErrorNotice error={storageOpenError} info={bulkMessage} className="mb-4" />

            <div className="grid gap-6 xl:grid-cols-2">
              <LibrarySection
                title="Important"
                incidents={librarySections.important}
                manualOverrides={manualOverrides}
                identity={identity}
                onOpen={setSelectedIncident}
                onOpenFiles={setFilesIncident}
              />
              <LibrarySection
                title="Review"
                incidents={librarySections.review}
                manualOverrides={manualOverrides}
                identity={identity}
                onOpen={setSelectedIncident}
                onOpenFiles={setFilesIncident}
              />
              <LibrarySection
                title="Ignore"
                incidents={librarySections.ignore}
                manualOverrides={manualOverrides}
                identity={identity}
                onOpen={setSelectedIncident}
                onOpenFiles={setFilesIncident}
              />
              <LibrarySection
                title="Trash"
                incidents={librarySections.trash}
                manualOverrides={manualOverrides}
                identity={identity}
                onOpen={setSelectedIncident}
                onOpenFiles={setFilesIncident}
              />
            </div>
          </>
        ) : (
          <>
        <div className="mb-5 rounded-2xl border border-white/[0.045] bg-white/[0.018] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
          <div className="flex flex-wrap items-end justify-between gap-5">
            <div className="min-w-[260px] flex-1">
              <div className="text-[12px] font-medium text-[var(--mimir-text-subtle)]">
                Current scan
              </div>
              <h1 className="mt-1 text-[30px] font-semibold text-[var(--mimir-text)]">Review incidents</h1>
              <p className="mt-2 max-w-[760px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
                {scanResultCopy(session)}
              </p>
              <div className="mt-3 inline-flex rounded-full border border-[rgba(157,183,170,0.16)] bg-[var(--mimir-accent-soft)] px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
                {reviewModeCopy(session)}
              </div>
            </div>
            <div className="grid w-full grid-cols-3 gap-2 sm:w-auto sm:min-w-[330px]">
              <SummaryMetric label="Important" value={counts.important} />
              <SummaryMetric label="Review" value={counts.review} />
              <SummaryMetric label="Ignore" value={counts.ignore} />
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-amber-200/14 bg-amber-300/8 px-3.5 py-2.5 text-[12px] leading-5 text-amber-50/82">
            Detection accuracy hasn't been measured against a real evaluation set yet. Review every flagged incident yourself, especially anything marked Ignore.
          </div>

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.045] pt-4">
            <div className="flex flex-wrap gap-2 rounded-full bg-black/16 p-1">
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
                className="h-10 w-full rounded-full border border-white/[0.055] bg-black/16 px-4 text-[13px] text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:border-[rgba(157,183,170,0.28)] focus:bg-white/[0.045]"
              />
            </label>
          </div>
        </div>

        {selectionMode && (
          <SelectionToolbar
            count={selectedIds.size}
            busy={bulkBusy}
            onSetStatus={status => void runBulkAction(selectedIncidents, 'set_status', status)}
            onMoveToLibrary={() => void runBulkAction(selectedIncidents, 'move_to_library')}
            onMoveToTrash={() => void runBulkAction(selectedIncidents, 'delete')}
            onContribute={() => void contributeSelected()}
            canContribute={hasSavedContributorIdentity}
            onClear={() => setSelectedIds(new Set())}
          />
        )}

        <ErrorNotice error={storageOpenError} info={bulkMessage} className="mb-4" />

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-[17px] font-semibold text-[var(--mimir-text)]">
            {filter === 'TRASH' ? 'Mimir Trash' : `${filter[0]}${filter.slice(1).toLowerCase()} incidents`}
          </h2>
          <p className="text-[13px] text-[var(--mimir-text-subtle)]">{filterDescription}</p>
        </div>

        {visibleIncidents.length === 0 ? (
          <EmptyLibraryState filter={filter} incidentCount={incidentCount} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(238px,1fr))] gap-4">
            {visibleIncidents.map(incident => (
              <IncidentCard
                key={incidentActionId(incident)}
                incident={incident}
                severityResolution={resolveIncidentSeverity(incident, manualOverrides, identity)}
                selected={selectedIds.has(incidentActionId(incident))}
                selectionMode={selectionMode}
                onOpen={setSelectedIncident}
                onToggleSelected={toggleIncidentSelected}
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
                    sortedIncidents.filter(incident =>
                      ['IMPORTANT', 'REVIEW'].includes(
                        resolveIncidentSeverity(incident, manualOverrides, identity).displaySeverity,
                      ),
                    ),
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

      {showAboutModal && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[460px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
              Free Beta
            </div>
            <h2 className="mt-2 text-[28px] font-semibold text-[var(--mimir-text)]">Mimir</h2>
            <div className="mt-2 text-[13px] font-medium text-[var(--mimir-text-muted)]">
              Version {MIMIR_VERSION}
            </div>
            <p className="mt-5 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir scans locally and helps review vehicle/security footage.
            </p>
            <p className="mt-3 text-[13px] leading-6 text-[var(--mimir-text-subtle)]">
              Scanning does not move or delete clips. File movement only happens when you choose Move to Library or Move to Mimir Trash.
            </p>
            <div className="mt-6 flex justify-end">
              <button
                type="button"
                onClick={() => setShowAboutModal(false)}
                className="h-10 rounded-lg bg-[var(--mimir-text)] px-4 text-[13px] font-semibold text-black transition hover:bg-white"
              >
                Close
              </button>
            </div>
          </section>
        </div>
      )}

      {showTechnicalDetails && (
        <TechnicalDetailsModal
          session={session}
          diagnostics={scanDiagnostics}
          onClose={() => setShowTechnicalDetails(false)}
        />
      )}
    </main>
    </CrashSafeBoundary>
  )
}

function TechnicalDetailsModal({
  session,
  diagnostics,
  onClose,
}: {
  session: MimirSession
  diagnostics?: FrontendScanDiagnostics | null
  onClose: () => void
}) {
  const featureFlags = {
    ...(sessionFeatureFlags(session)),
    schema_version: session.schema_version,
    scanner_version: session.scanner_version,
    core_version: session.core_version,
    core_build_id: session.core_build_id,
    generated_by: session.generated_by,
    backend_runtime: session.backend_runtime,
    scan_command_version: session.scan_command_version,
    evidence_version: session.evidence_version,
    thumbnail_version: session.thumbnail_version,
    key_moment_version: session.key_moment_version,
    ai_enabled: session.ai_enabled,
    ai_model: session.ai_model,
  }
  const staleWarning = sessionMayBeStale(session)

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
      <section className="max-h-[86vh] w-full max-w-[760px] overflow-hidden rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
        <div className="border-b border-white/[0.06] p-5">
          <div className="text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
            Diagnostics
          </div>
          <h2 className="mt-2 text-[24px] font-semibold text-[var(--mimir-text)]">Technical details</h2>
          {staleWarning && (
            <div className="mt-4 rounded-lg border border-amber-300/18 bg-amber-400/10 p-3 text-[12px] leading-5 text-amber-100/90">
              Session may be stale or generated by an older backend.
            </div>
          )}
        </div>

        <div className="max-h-[calc(86vh-132px)] overflow-y-auto p-5">
          <div className="grid gap-2 sm:grid-cols-2">
            <TechnicalDetailRow label="backend_runner" value={diagnostics?.backend_runner} />
            <TechnicalDetailRow label="backend_mode" value={diagnostics?.backend_mode} />
            <TechnicalDetailRow label="output argument used" value={diagnostics?.output_argument_used} />
            <TechnicalDetailRow label="active_output_dir" value={diagnostics?.active_output_dir} />
            <TechnicalDetailRow label="latest_session_path" value={diagnostics?.latest_session_path} />
            <TechnicalDetailRow
              label="latest_session modified time"
              value={diagnostics?.latest_session_modified_time}
            />
            <TechnicalDetailRow label="core_version" value={session.core_version} />
            <TechnicalDetailRow label="core_build_id" value={session.core_build_id} />
            <TechnicalDetailRow label="backend_runtime" value={session.backend_runtime} />
            <TechnicalDetailRow label="session_created_at" value={session.session_created_at} />
            <TechnicalDetailRow label="output_path" value={session.output_path} />
            <TechnicalDetailRow label="generated_by" value={session.generated_by} />
            <TechnicalDetailRow label="scan_command_version" value={session.scan_command_version} />
          </div>

          <TechnicalBlock label="backend_command" value={diagnostics?.backend_command || 'Not reported'} />
          <TechnicalBlock label="scan_summary" value={technicalJson(session.scan_summary ?? null)} />
          <TechnicalBlock label="feature flags" value={technicalJson(featureFlags)} />
        </div>

        <div className="flex justify-end border-t border-white/[0.06] p-4">
          <button
            type="button"
            onClick={onClose}
            className="h-10 rounded-lg bg-[var(--mimir-text)] px-4 text-[13px] font-semibold text-black transition hover:bg-white"
          >
            Close
          </button>
        </div>
      </section>
    </div>
  )
}

function TechnicalDetailRow({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="min-w-0 rounded-lg border border-white/[0.06] bg-white/[0.025] p-3">
      <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-[var(--mimir-text-subtle)]">
        {label}
      </div>
      <div className="mt-1 break-words text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        {formatTechnicalValue(value)}
      </div>
    </div>
  )
}

function TechnicalBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="mt-4 rounded-lg border border-white/[0.06] bg-black/20 p-3">
      <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-[var(--mimir-text-subtle)]">
        {label}
      </div>
      <pre className="mt-2 max-h-[180px] overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-[var(--mimir-text-muted)]">
        {value}
      </pre>
    </div>
  )
}
