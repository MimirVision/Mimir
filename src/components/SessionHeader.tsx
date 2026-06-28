import type { SessionSummary } from '../types'

interface SessionHeaderProps {
  session: SessionSummary
}

export function SessionHeader({ session }: SessionHeaderProps) {
  return (
    <header className="flex items-start justify-between gap-5 border-b border-[var(--mimir-border)] px-7 py-6">
      <div>
        <button className="mb-4 text-[13px] text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]">
          Back to sessions
        </button>
        <div className="flex items-end gap-3">
          <h1 className="text-[32px] font-semibold text-[var(--mimir-text)]">{session.date}</h1>
          <span className="pb-1.5 text-[13px] text-[var(--mimir-text-muted)]">{session.time}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[13px] text-[var(--mimir-text-muted)]">
          <span>{session.clipCount} clips</span>
          <span>{session.incidentCount} sample moments</span>
          <span>{session.source}</span>
        </div>
      </div>

      <div className="mt-8 rounded-full border border-[var(--mimir-border)] bg-white/[0.035] px-3 py-1.5 text-[12px] font-medium text-[var(--mimir-text-muted)]">
        Demo data
      </div>
    </header>
  )
}
