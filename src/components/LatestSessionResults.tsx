import { convertFileSrc } from '@tauri-apps/api/tauri'
import type { MimirIncident, MimirSession, SessionLoadState } from '../types'

interface LatestSessionResultsProps {
  loadState: SessionLoadState
  session: MimirSession | null
  onLoad: () => void
}

function severityClass(severity: string) {
  const value = severity.toUpperCase()

  if (value === 'IMPORTANT') {
    return 'border-red-400/30 bg-red-500/10 text-red-100'
  }

  if (value === 'REVIEW') {
    return 'border-amber-300/30 bg-amber-400/10 text-amber-100'
  }

  return 'border-white/10 bg-white/[0.035] text-[var(--mimir-text-muted)]'
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
      <div className="text-[12px] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-2 text-[24px] font-semibold text-[var(--mimir-text)]">{value}</div>
    </div>
  )
}

function IncidentCard({ incident }: { incident: MimirIncident }) {
  return (
    <article className="grid gap-4 rounded-lg border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-4 shadow-[0_18px_45px_rgba(0,0,0,0.22)] lg:grid-cols-[168px_minmax(0,1fr)]">
      <div className="aspect-video overflow-hidden rounded-lg border border-[var(--mimir-border)] bg-black/35">
        {incident.thumbnail ? (
          <img
            src={convertFileSrc(incident.thumbnail)}
            alt=""
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[12px] text-[var(--mimir-text-subtle)]">
            No thumbnail
          </div>
        )}
      </div>

      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[15px] font-semibold text-[var(--mimir-text)]">{incident.id}</div>
            <div className="mt-1 truncate text-[13px] text-[var(--mimir-text-muted)]">
              {incident.source_video}
            </div>
          </div>
          <div className={`rounded-full border px-3 py-1 text-[11px] font-semibold ${severityClass(incident.severity)}`}>
            {incident.severity}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric label="AI decision" value={incident.ai_decision} />
          <Metric label="Score" value={incident.score} />
          <Metric label="Persons" value={incident.persons} />
          <Metric label="Vehicles" value={incident.vehicles} />
        </div>

        <div className="mt-3 flex flex-wrap gap-3 text-[12px] text-[var(--mimir-text-muted)]">
          <span>Event {incident.event_id}</span>
          <span>{incident.active_frames} active frames</span>
          <span>{incident.created_at}</span>
        </div>
      </div>
    </article>
  )
}

export function LatestSessionResults({
  loadState,
  session,
  onLoad,
}: LatestSessionResultsProps) {
  const isBusy = loadState === 'loading'

  return (
    <div className="h-full overflow-y-auto px-7 py-6">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-[13px] text-[var(--mimir-text-muted)]">Backend session output</div>
          <h1 className="mt-2 text-[32px] font-semibold text-[var(--mimir-text)]">Latest Session</h1>
          <p className="mt-2 max-w-[640px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
            Reads the latest local scan output from C:\Mimir_Backend\MimirOutput\latest_session.json.
          </p>
        </div>

        <button
          onClick={onLoad}
          disabled={isBusy}
          className="h-11 rounded-lg bg-[var(--mimir-text)] px-5 text-[14px] font-semibold text-black transition hover:bg-white disabled:cursor-wait disabled:opacity-70"
        >
          {loadState === 'loading' ? 'Loading...' : 'Load Latest Session'}
        </button>
      </header>

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

      {loadState === 'idle' && (
        <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-5 text-[14px] text-[var(--mimir-text-muted)]">
          Load the latest local backend session to view real scan results here.
        </div>
      )}

      {session && loadState === 'loaded' && (
        <div>
          <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Metric label="Status" value={session.status} />
            <Metric label="Clips processed" value={session.clips_processed} />
            <Metric label="Important" value={session.important} />
            <Metric label="Review" value={session.review} />
            <Metric label="Ignore" value={session.ignore} />
          </section>

          <section className="mb-5 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
            <div className="grid gap-3 text-[13px] text-[var(--mimir-text-muted)] sm:grid-cols-2">
              <div>
                <span className="text-[var(--mimir-text-subtle)]">Started:</span> {session.started_at}
              </div>
              <div>
                <span className="text-[var(--mimir-text-subtle)]">Finished:</span> {session.finished_at ?? 'Not finished'}
              </div>
            </div>
          </section>

          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[16px] font-semibold text-[var(--mimir-text)]">Incidents</h2>
              <span className="text-[13px] text-[var(--mimir-text-muted)]">
                {session.incidents.length} found
              </span>
            </div>

            {session.incidents.length === 0 ? (
              <div className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-5 text-[14px] text-[var(--mimir-text-muted)]">
                No incidents were written for this session.
              </div>
            ) : (
              <div className="space-y-3">
                {session.incidents.map(incident => (
                  <IncidentCard key={incident.id} incident={incident} />
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
