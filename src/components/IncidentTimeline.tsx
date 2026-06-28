import type { Incident } from '../types'

interface IncidentTimelineProps {
  incidents: Incident[]
  selectedIncidentId: string
  onSelectIncident: (id: string) => void
}

function markerTone(tone: string) {
  if (tone === 'high') return 'bg-[var(--mimir-status-red)]'
  if (tone === 'review') return 'bg-[var(--mimir-status-amber)]'
  return 'bg-white/30'
}

function severityTone(severity: Incident['severity']) {
  if (severity === 'high') return 'border-red-400/25 bg-red-500/10 text-red-100'
  if (severity === 'review') return 'border-amber-300/25 bg-amber-400/10 text-amber-100'
  return 'border-white/10 bg-white/[0.035] text-[var(--mimir-text-muted)]'
}

function severityCopy(severity: Incident['severity']) {
  if (severity === 'high') return 'Possible incident'
  if (severity === 'review') return 'Relevant moment'
  return 'Quiet'
}

export function IncidentTimeline({ incidents, selectedIncidentId, onSelectIncident }: IncidentTimelineProps) {
  const selectedIncident = incidents.find(incident => incident.id === selectedIncidentId) ?? incidents[0]

  return (
    <section className="mt-5 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-[14px] font-semibold text-[var(--mimir-text)]">Sample moment timeline</h2>
        <span className="text-[13px] text-[var(--mimir-text-muted)]">{selectedIncident.duration}</span>
      </div>

      <div className="mb-4 grid gap-3 lg:grid-cols-2">
        {incidents.map(incident => (
          <button
            key={incident.id}
            onClick={() => onSelectIncident(incident.id)}
            className={`rounded-lg border p-4 text-left transition ${
              incident.id === selectedIncidentId
                ? 'border-white/28 bg-white/[0.06]'
                : 'border-[var(--mimir-border)] bg-black/18 hover:border-white/18'
            }`}
          >
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="text-[14px] font-medium text-[var(--mimir-text)]">{incident.title}</span>
              <span className="text-[12px] text-[var(--mimir-text-muted)]">{incident.duration}</span>
            </div>
            <div className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-medium ${severityTone(incident.severity)}`}>
              {severityCopy(incident.severity)}
            </div>
          </button>
        ))}
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        {selectedIncident.moments.map(moment => (
          <div key={moment.id} className="rounded-lg border border-[var(--mimir-border)] bg-black/22 p-4">
            <div className="mb-3 flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${markerTone(moment.tone)}`} />
              <span className="text-[12px] text-[var(--mimir-text-subtle)]">{moment.time}</span>
            </div>
            <div className="text-[14px] font-medium text-[var(--mimir-text)]">{moment.title}</div>
            <div className="mt-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">{moment.description}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
