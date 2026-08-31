import { useState } from 'react'
import { correctionConsent, correctionsSent, setCorrectionConsent } from '../lib/verdictCorrection'

/**
 * Turn correction sending on or off, after the first time it was asked.
 *
 * The terms, the privacy notice and the README all say consent can be
 * withdrawn. Until this existed that was not true: consent was recorded once by
 * the prompt and there was no way back. A permission that cannot be taken away
 * is not really a permission, and writing that it can be while it cannot is
 * worse than not offering it.
 *
 * Renders nothing before the question has been asked, so a new user meets it in
 * the prompt rather than finding a toggle for something they have never been
 * told about.
 */
export function CorrectionSharing() {
  const [consent, setConsent] = useState(correctionConsent())
  const sent = correctionsSent()

  if (consent === 'unasked') {
    return null
  }

  const granted = consent === 'granted'
  const change = (next: 'granted' | 'declined') => {
    setCorrectionConsent(next)
    setConsent(next)
  }

  return (
    <div className="mt-3 rounded-md border border-white/[0.06] bg-black/[0.18] p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-[var(--mimir-text)]">
            Sending your corrections
          </div>
          <p className="mt-1 max-w-[520px] text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
            {granted
              ? 'When you change a verdict, that correction is sent so the detector can learn from it. No video is included.'
              : 'Corrections stay on this PC. Changing a verdict still updates it here, but nothing is sent.'}
            {sent > 0 && (
              <>
                {' '}
                <span className="text-[var(--mimir-text-muted)]">
                  You have sent {sent} {sent === 1 ? 'correction' : 'corrections'}.
                </span>
              </>
            )}
          </p>
        </div>

        <button
          type="button"
          onClick={() => change(granted ? 'declined' : 'granted')}
          className="shrink-0 rounded-md border border-white/[0.1] bg-white/[0.03] px-3 py-2 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.06]"
        >
          {granted ? 'Stop sending' : 'Start sending'}
        </button>
      </div>
    </div>
  )
}
