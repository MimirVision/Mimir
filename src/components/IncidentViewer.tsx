import type { Incident } from '../types'

interface IncidentViewerProps {
  incident: Incident
}

function severityLabel(severity: Incident['severity']) {
  if (severity === 'high') return 'High'
  if (severity === 'review') return 'Review'
  return 'Low'
}

function severityTone(severity: Incident['severity']) {
  if (severity === 'high') return 'border-red-400/30 bg-red-500/10 text-red-100'
  if (severity === 'review') return 'border-amber-300/30 bg-amber-400/10 text-amber-100'
  return 'border-white/10 bg-white/[0.05] text-white/52'
}

export function IncidentViewer({ incident }: IncidentViewerProps) {
  return (
    <section className="min-w-0">
      <div className="relative overflow-hidden rounded-lg border border-[var(--mimir-border)] bg-black shadow-[0_24px_70px_rgba(0,0,0,0.42)]">
        <div className="absolute left-4 top-4 z-10 flex gap-2">
          <div className="rounded-full border border-[var(--mimir-border)] bg-black/70 px-3 py-2 text-[12px] text-[var(--mimir-text)]">
            {incident.camera}
          </div>
          <div className="rounded-full border border-[var(--mimir-border)] bg-black/70 px-3 py-2 text-[12px] text-[var(--mimir-text-subtle)]">
            Sample preview
          </div>
        </div>
        <div className={`absolute right-4 top-4 z-10 rounded-full border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] ${severityTone(incident.severity)}`}>
          {severityLabel(incident.severity)}
        </div>

        <div className="relative aspect-video min-h-[360px] overflow-hidden">
          <div className="absolute inset-0 bg-[linear-gradient(180deg,#151515_0%,#080808_48%,#030303_100%)]" />
          <div className="absolute inset-x-0 bottom-0 h-[44%] bg-[linear-gradient(180deg,transparent,rgba(0,0,0,0.88))]" />
          <div className="absolute left-[8%] right-[8%] top-[18%] h-px bg-white/7" />
          <div className="absolute left-[12%] right-[12%] top-[44%] h-px bg-white/10" />
          <div className="absolute bottom-[24%] left-[14%] right-[14%] h-[2px] bg-white/12" />
          <div className="absolute bottom-[12%] left-[24%] right-[24%] h-[2px] bg-white/10" />
          <div className="absolute bottom-[18%] left-[48%] h-[26%] w-px bg-white/10" />
          <div className="absolute bottom-[18%] right-[28%] h-[22%] w-[18%] rounded-t-[40px] border border-white/16 bg-white/[0.045]" />
          <div className="absolute bottom-[20%] right-[34%] h-3 w-3 rounded-full bg-[var(--mimir-status-red)]/80" />
          <div className="absolute bottom-[22%] right-[23%] h-[30%] w-[8%] rounded-full border border-white/16 bg-white/[0.035]" />
          <div className="absolute bottom-[31%] right-[24.5%] h-8 w-4 rounded-full bg-white/16" />

          <div className="absolute bottom-5 left-5 right-5">
            <div className="mb-3 flex items-center justify-between text-[12px] text-[var(--mimir-text-muted)]">
              <span>{incident.clipLabel}</span>
              <span>{incident.duration}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/12">
              <div className="h-full w-[42%] rounded-full bg-[var(--mimir-text)]" />
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {['Front', 'Left Repeater', 'Right Repeater', 'Rear'].map(camera => (
          <button
            key={camera}
            className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
              camera === incident.camera
                ? 'border-white/30 bg-white/[0.075] text-[var(--mimir-text)]'
                : 'border-[var(--mimir-border)] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:text-[var(--mimir-text)]'
            }`}
          >
            <span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-current align-middle opacity-70" />
            {camera}
          </button>
        ))}
      </div>
    </section>
  )
}
