import type { DescribedError } from '../lib/errorMessages'

// Shared inline error block: an actionable sentence, with the raw text folded
// away behind a disclosure. Matches the pattern already used for scan failures
// in ActiveScanStatus so a failure looks the same wherever it surfaces.

interface ErrorNoticeProps {
  error: DescribedError | null
  /** Neutral message shown when there is no error but there is something to say. */
  info?: string
  className?: string
}

export function ErrorNotice({ error, info, className = '' }: ErrorNoticeProps) {
  if (!error && !info) {
    return null
  }

  if (!error) {
    return (
      <div
        className={`whitespace-pre-wrap rounded-lg border border-white/[0.08] bg-white/[0.025] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)] ${className}`}
      >
        {info}
      </div>
    )
  }

  return (
    <div
      role="alert"
      className={`rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/[0.86] ${className}`}
    >
      <div className="whitespace-pre-wrap">{error.message}</div>
      {error.detail && (
        <details className="mt-2.5 rounded-md border border-red-300/15 bg-black/25 p-2.5">
          <summary className="cursor-pointer text-[11px] font-medium text-red-100/85">Technical details</summary>
          <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-red-50/75">
            {error.detail}
          </pre>
        </details>
      )}
    </div>
  )
}
