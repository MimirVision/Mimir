import type { Incident } from '../types'

interface AssessmentPanelProps {
  incident: Incident
}

function severityTone(severity: Incident['severity']) {
  if (severity === 'high') return 'border-[var(--mimir-status-red)]/35 bg-[var(--mimir-status-red)]/10 text-red-100'
  if (severity === 'review') return 'border-[var(--mimir-status-amber)]/35 bg-[var(--mimir-status-amber)]/10 text-amber-100'
  return 'border-[var(--mimir-status-green)]/30 bg-[var(--mimir-status-green)]/10 text-emerald-100'
}

export function AssessmentPanel({ incident }: AssessmentPanelProps) {
  return (
    <aside className="min-h-0 overflow-y-auto border-l border-[var(--mimir-border)] bg-black/20 px-5 py-6">
      <section className="rounded-lg border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-5 shadow-[0_18px_50px_rgba(0,0,0,0.28)]">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="text-[13px] font-semibold text-[var(--mimir-text)]">Mimir's Assessment</div>
          <div className="rounded-full border border-[var(--mimir-border)] px-2.5 py-1 text-[11px] text-[var(--mimir-text-subtle)]">
            Sample
          </div>
        </div>
        <h2 className="text-[23px] font-semibold leading-7 text-[var(--mimir-text)]">{incident.title}</h2>
        <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">{incident.summary}</p>
        <div className={`mt-5 rounded-lg border px-4 py-3 text-[13px] font-medium ${severityTone(incident.severity)}`}>
          {incident.assessment}
        </div>
      </section>

      <section className="mt-4 grid grid-cols-2 gap-3">
        {[
          ['Duration', incident.duration],
          ['Objects', incident.objects.join(', ')],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
            <div className="text-[12px] text-[var(--mimir-text-subtle)]">{label}</div>
            <div className="mt-2 text-[14px] font-medium text-[var(--mimir-text)]">{value}</div>
          </div>
        ))}
      </section>

      <section className="mt-4 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-5">
        <h3 className="text-[13px] font-semibold text-[var(--mimir-text)]">Evidence used in sample</h3>
        <div className="mt-3 space-y-2">
          {incident.evidenceNotes.map(note => (
            <div key={note} className="rounded-lg bg-black/24 px-3 py-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
              {note}
            </div>
          ))}
        </div>
      </section>
    </aside>
  )
}
