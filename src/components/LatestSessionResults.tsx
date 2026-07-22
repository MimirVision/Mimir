import { useMemo, useState } from 'react'
import { convertFileSrc } from '@tauri-apps/api/core'
import type { MimirIncident, MimirSession, SessionLoadState } from '../types'

interface LatestSessionPanelProps {
  loadState: SessionLoadState
  session: MimirSession | null
  onLoad: () => void
}

interface IncidentTimelineProps {
  session: MimirSession | null
}

type SeverityGroup = 'IMPORTANT' | 'REVIEW' | 'IGNORE'

function normalizeSeverity(severity: string): SeverityGroup {
  const value = severity.toUpperCase()

  if (value === 'IMPORTANT' || value === 'REVIEW') {
    return value
  }

  return 'IGNORE'
}

function severityClass(severity: string) {
  const value = normalizeSeverity(severity)

  if (value === 'IMPORTANT') {
    return 'border-red-400/30 bg-red-500/10 text-red-100'
  }

  if (value === 'REVIEW') {
    return 'border-amber-300/30 bg-amber-400/10 text-amber-100'
  }

  return 'border-white/10 bg-white/[0.035] text-[var(--mimir-text-muted)]'
}

function formatEventType(value?: string) {
  if (!value) {
    return 'Unclassified event'
  }

  return value
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

function formatConfidence(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Not provided'
  }

  return `${Math.round(value * 100)}%`
}

function formatNumber(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Not available'
  }

  return Number.isInteger(value) ? `${value}` : value.toFixed(1)
}

function formatBoolean(value?: boolean) {
  if (typeof value !== 'boolean') {
    return 'Not available'
  }

  return value ? 'Yes' : 'No'
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-3">
      <div className="text-[12px] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-2 text-[18px] font-semibold text-[var(--mimir-text)]">{value}</div>
    </div>
  )
}

function IncidentImage({ incident, large = false }: { incident: MimirIncident; large?: boolean }) {
  const previewImages = [
    incident.hero_thumbnail,
    incident.thumbnail,
    incident.best_frame_image,
    incident.contact_sheet,
  ].filter(Boolean) as string[]
  const [failedIndex, setFailedIndex] = useState(0)
  const previewImage = previewImages[failedIndex]

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--mimir-border)] bg-black/35">
      {previewImage ? (
        <img
          src={convertFileSrc(previewImage)}
          alt=""
          onError={() => setFailedIndex(index => index + 1)}
          className={`w-full ${large ? 'max-h-[520px] min-h-[260px] object-contain' : 'max-h-[260px] min-h-[190px] object-cover'}`}
        />
      ) : (
        <div
          className={`flex items-center justify-center text-[12px] text-[var(--mimir-text-subtle)] ${large ? 'min-h-[320px]' : 'min-h-[210px]'}`}
        >
          Thumbnail unavailable
        </div>
      )}
    </div>
  )
}

function IncidentCard({
  incident,
  onOpen,
}: {
  incident: MimirIncident
  onOpen: (incident: MimirIncident) => void
}) {
  const evidence = incident.evidence ?? []

  return (
    <button
      onClick={() => onOpen(incident)}
      className="group grid w-full min-w-0 gap-4 rounded-xl border border-[var(--mimir-border)] bg-[linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.012))] p-4 text-left shadow-[0_18px_45px_rgba(0,0,0,0.18)] transition hover:border-white/20 hover:bg-white/[0.035] lg:grid-cols-[minmax(280px,0.55fr)_minmax(0,1fr)]"
    >
      <IncidentImage incident={incident} />

      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[12px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
              {incident.id}
            </div>
            <h3 className="mt-2 text-[22px] font-semibold leading-tight text-[var(--mimir-text)]">
              {formatEventType(incident.event_type)}
            </h3>
            <div className="mt-1 truncate text-[13px] text-[var(--mimir-text-muted)]">
              {incident.source_video}
            </div>
          </div>
          <div
            className={`rounded-full border px-3 py-1 text-[11px] font-semibold ${severityClass(
              incident.severity,
            )}`}
          >
            {normalizeSeverity(incident.severity)}
          </div>
        </div>

        <p className="line-clamp-3 max-w-[760px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
          {incident.summary || 'No AI summary was included for this incident.'}
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {incident.possible_impact && (
            <span className="rounded-full border border-amber-300/25 bg-amber-400/10 px-3 py-1 text-[12px] font-medium text-amber-100">
              Possible impact
            </span>
          )}
          <span className="rounded-full border border-[var(--mimir-border)] bg-white/[0.025] px-3 py-1 text-[12px] text-[var(--mimir-text-muted)]">
            AI confidence {formatConfidence(incident.ai_confidence)}
          </span>
          {typeof incident.max_motion_score === 'number' && (
            <span className="rounded-full border border-[var(--mimir-border)] bg-white/[0.025] px-3 py-1 text-[12px] text-[var(--mimir-text-muted)]">
              Motion {formatNumber(incident.max_motion_score)}
            </span>
          )}
          {incident.recommended_action && (
            <span className="rounded-full border border-[var(--mimir-border)] bg-white/[0.025] px-3 py-1 text-[12px] text-[var(--mimir-text-muted)]">
              {incident.recommended_action}
            </span>
          )}
        </div>

        {evidence.length > 0 && (
          <div className="mt-4 rounded-lg border border-[var(--mimir-border)] bg-black/15 p-3">
            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">
              Evidence
            </div>
            <ul className="space-y-1.5">
              {evidence.slice(0, 2).map((item, index) => (
                <li
                  key={`${incident.id}-evidence-${index}`}
                  className="flex gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]"
                >
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-white/35" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 grid gap-3 sm:grid-cols-4">
          <Metric label="Score" value={formatNumber(incident.score)} />
          <Metric label="Persons" value={incident.persons} />
          <Metric label="Vehicles" value={incident.vehicles} />
          <Metric label="Possible impact" value={formatBoolean(incident.possible_impact)} />
        </div>
      </div>
    </button>
  )
}

function DetailMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-3">
      <div className="text-[12px] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-2 text-[15px] font-semibold text-[var(--mimir-text)]">{value}</div>
    </div>
  )
}

function IncidentDetail({
  incident,
  onClose,
}: {
  incident: MimirIncident
  onClose: () => void
}) {
  const evidence = incident.evidence ?? []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/72 p-4 backdrop-blur-sm">
      <section className="max-h-[92vh] w-full max-w-[1180px] overflow-y-auto rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-bg-depth)] shadow-[0_32px_120px_rgba(0,0,0,0.72)]">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-[var(--mimir-border)] bg-[var(--mimir-bg-depth)]/95 p-5 backdrop-blur">
          <div>
            <div className="mb-2 text-[12px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
              Incident detail
            </div>
            <h3 className="text-[26px] font-semibold leading-tight text-[var(--mimir-text)]">
              {formatEventType(incident.event_type)}
            </h3>
            <p className="mt-1 text-[13px] text-[var(--mimir-text-muted)]">{incident.source_video}</p>
          </div>
          <button
            onClick={onClose}
            className="h-10 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
          >
            Close
          </button>
        </div>

        <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
          <IncidentImage incident={incident} large />

          <div>
            <div className="mb-4 flex flex-wrap gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-[12px] font-semibold ${severityClass(
                  incident.severity,
                )}`}
              >
                {normalizeSeverity(incident.severity)}
              </span>
              {incident.possible_impact && (
                <span className="rounded-full border border-amber-300/25 bg-amber-400/10 px-3 py-1 text-[12px] font-medium text-amber-100">
                  Possible impact
                </span>
              )}
            </div>

            <p className="text-[15px] leading-7 text-[var(--mimir-text-muted)]">
              {incident.summary || 'No AI summary was included for this incident.'}
            </p>

            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <DetailMetric label="AI confidence" value={formatConfidence(incident.ai_confidence)} />
              <DetailMetric label="AI decision" value={incident.ai_decision} />
              <DetailMetric label="Score" value={formatNumber(incident.score)} />
              <DetailMetric label="Motion" value={formatNumber(incident.max_motion_score)} />
              <DetailMetric label="Possible impact" value={formatBoolean(incident.possible_impact)} />
              <DetailMetric label="Persons" value={incident.persons} />
              <DetailMetric label="Vehicles" value={incident.vehicles} />
              <DetailMetric label="Source video" value={incident.source_video} />
            </div>

            <div className="mt-5 rounded-lg border border-[var(--mimir-border)] bg-black/20 p-4">
              <div className="mb-3 text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
                Evidence
              </div>
              {evidence.length > 0 ? (
                <ul className="space-y-2">
                  {evidence.map((item, index) => (
                    <li
                      key={`${incident.id}-detail-evidence-${index}`}
                      className="flex gap-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]"
                    >
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-white/35" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-[13px] text-[var(--mimir-text-muted)]">
                  No evidence points were included for this incident.
                </div>
              )}
            </div>

            <div className="mt-5 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
              <div className="mb-2 text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
                Recommended action
              </div>
              <div className="text-[14px] leading-6 text-[var(--mimir-text-muted)]">
                {incident.recommended_action || 'No recommended action was included.'}
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-[12px] text-[var(--mimir-text-subtle)]">
              <span>Event {incident.event_id}</span>
              <span>{incident.active_frames} active frames</span>
              <span>{incident.created_at}</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}

export function LatestSessionPanel({
  loadState,
  session,
  onLoad,
}: LatestSessionPanelProps) {
  const isBusy = loadState === 'loading'

  return (
    <section className="flex h-full min-h-[520px] flex-col rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-5 shadow-[0_24px_70px_rgba(0,0,0,0.24)]">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="mb-3 text-[12px] font-medium uppercase tracking-[0.2em] text-[var(--mimir-text-subtle)]">
            Latest session
          </div>
          <h2 className="text-[24px] font-semibold text-[var(--mimir-text)]">Local scan results</h2>
        </div>
        <button
          onClick={onLoad}
          disabled={isBusy}
          className="h-10 shrink-0 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-wait disabled:opacity-70"
        >
          {isBusy ? 'Loading...' : 'Load Latest Session'}
        </button>
      </div>

      {!session && loadState === 'idle' && (
        <div className="flex flex-1 items-center rounded-lg border border-[var(--mimir-border)] bg-white/[0.02] p-6">
          <div>
            <h3 className="text-[18px] font-semibold text-[var(--mimir-text)]">
              No session loaded yet.
            </h3>
            <p className="mt-2 max-w-[420px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Run a scan or load the latest local session.
            </p>
          </div>
        </div>
      )}

      {!session && loadState === 'loading' && (
        <div className="flex flex-1 items-center rounded-lg border border-[var(--mimir-border)] bg-white/[0.02] p-6">
          <div>
            <h3 className="text-[18px] font-semibold text-[var(--mimir-text)]">
              Analyzing footage...
            </h3>
            <p className="mt-2 max-w-[420px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir is running the local scanner and will load the latest session when it finishes.
            </p>
          </div>
        </div>
      )}

      {loadState === 'missing' && (
        <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-5 text-[14px] text-[var(--mimir-text-muted)]">
          No scan results found yet.
        </div>
      )}

      {loadState === 'error' && (
        <div className="rounded-lg border border-red-400/20 bg-red-500/10 p-5 text-[14px] text-red-100">
          Could not read Mimir session output.
        </div>
      )}

      {session && loadState === 'loaded' && (
        <div className="flex flex-1 flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Metric label="Clips processed" value={session.clips_processed} />
            <Metric label="Important" value={session.important} />
            <Metric label="Review" value={session.review} />
            <Metric label="Ignore" value={session.ignore} />
          </div>

          <div className="mt-auto rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
            <div className="grid gap-3 text-[13px] text-[var(--mimir-text-muted)]">
              <div>
                <span className="text-[var(--mimir-text-subtle)]">Status:</span> {session.status}
              </div>
              <div>
                <span className="text-[var(--mimir-text-subtle)]">Started:</span>{' '}
                {session.started_at}
              </div>
              <div>
                <span className="text-[var(--mimir-text-subtle)]">Finished:</span>{' '}
                {session.finished_at ?? 'Not finished'}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function groupIncidents(incidents: MimirIncident[]) {
  const byCreatedAt = (left: MimirIncident, right: MimirIncident) =>
    String(left.created_at || '').localeCompare(String(right.created_at || ''))
  const important = incidents
    .filter(incident => normalizeSeverity(incident.severity) === 'IMPORTANT')
    .sort(byCreatedAt)
  const review = incidents
    .filter(incident => normalizeSeverity(incident.severity) === 'REVIEW')
    .sort(byCreatedAt)
  const ignore = incidents
    .filter(incident => normalizeSeverity(incident.severity) === 'IGNORE')
    .sort(byCreatedAt)

  return { important, review, ignore }
}

function SessionSummaryStrip({ session }: { session: MimirSession }) {
  return (
    <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Metric label="Clips processed" value={session.clips_processed} />
      <Metric label="Important" value={session.important} />
      <Metric label="Review" value={session.review} />
      <Metric label="Ignore" value={session.ignore} />
    </div>
  )
}

function IncidentGroup({
  title,
  incidents,
  onOpen,
  muted = false,
}: {
  title: string
  incidents: MimirIncident[]
  onOpen: (incident: MimirIncident) => void
  muted?: boolean
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[15px] font-semibold text-[var(--mimir-text)]">{title}</h3>
        <span className="text-[13px] text-[var(--mimir-text-muted)]">{incidents.length}</span>
      </div>

      {incidents.length === 0 ? (
        <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.02] p-5 text-[14px] text-[var(--mimir-text-muted)]">
          No {title.toLowerCase()} moments.
        </div>
      ) : (
        <div className={`grid gap-4 ${muted ? 'opacity-85' : ''}`}>
          {incidents.map(incident => (
            <IncidentCard key={incident.id} incident={incident} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  )
}

export function IncidentTimeline({ session }: IncidentTimelineProps) {
  const [showIgnore, setShowIgnore] = useState(false)
  const [selectedIncident, setSelectedIncident] = useState<MimirIncident | null>(null)
  const incidents = session?.incidents ?? []
  const grouped = useMemo(() => groupIncidents(incidents), [incidents])
  const visibleCount = grouped.important.length + grouped.review.length

  return (
    <section className="rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-5 shadow-[0_24px_70px_rgba(0,0,0,0.2)]">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="mb-3 text-[12px] font-medium uppercase tracking-[0.2em] text-[var(--mimir-text-subtle)]">
            Review
          </div>
          <h2 className="text-[24px] font-semibold text-[var(--mimir-text)]">Incident Timeline</h2>
        </div>
        <div className="text-[13px] text-[var(--mimir-text-muted)]">
          {visibleCount} visible, {grouped.ignore.length} ignored
        </div>
      </div>

      {session && <SessionSummaryStrip session={session} />}

      {incidents.length === 0 ? (
        <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.02] p-8 text-[14px] text-[var(--mimir-text-muted)]">
          No incidents to review yet.
        </div>
      ) : (
        <div className="space-y-7">
          <IncidentGroup title="Important" incidents={grouped.important} onOpen={setSelectedIncident} />
          <IncidentGroup title="Review" incidents={grouped.review} onOpen={setSelectedIncident} />

          <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.015] p-4">
            <button
              onClick={() => setShowIgnore(value => !value)}
              className="flex w-full items-center justify-between text-left"
            >
              <div>
                <h3 className="text-[15px] font-semibold text-[var(--mimir-text)]">Ignore</h3>
                <p className="mt-1 text-[13px] text-[var(--mimir-text-muted)]">
                  Hidden by default to keep the timeline focused.
                </p>
              </div>
              <span className="rounded-full border border-[var(--mimir-border)] bg-white/[0.025] px-3 py-1 text-[12px] text-[var(--mimir-text-muted)]">
                {showIgnore ? 'Hide' : `Show ${grouped.ignore.length}`}
              </span>
            </button>

            {showIgnore && (
              <div className="mt-4">
                <IncidentGroup title="Ignored" incidents={grouped.ignore} onOpen={setSelectedIncident} muted />
              </div>
            )}
          </div>
        </div>
      )}

      {selectedIncident && (
        <IncidentDetail incident={selectedIncident} onClose={() => setSelectedIncident(null)} />
      )}
    </section>
  )
}
