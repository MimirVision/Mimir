import { useEffect, useState } from 'react'
import { countCrashes, readCrashLog, sendCrashReport } from '../lib/crashReport'

/**
 * Offers to send crash reports, and shows what would be sent.
 *
 * Renders nothing when the log is empty, which for most people is always, so a
 * working install never mentions crashes at all.
 *
 * The log is shown before sending rather than described. It contains stack
 * traces and file paths, and someone deciding whether to hand that over is
 * entitled to read it first -- particularly since paths from a dashcam library
 * can carry dates and place names.
 */
export function CrashReportPanel() {
  const [log, setLog] = useState('')
  const [note, setNote] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'failed'>('idle')
  const [message, setMessage] = useState('')

  useEffect(() => {
    void readCrashLog().then(setLog)
  }, [])

  if (!log.trim()) {
    return null
  }

  const crashes = countCrashes(log)

  const send = () => {
    setState('sending')
    void sendCrashReport(log, note)
      .then(result => {
        setState('sent')
        setMessage(result.message || 'Sent. Thank you -- this is genuinely useful.')
      })
      .catch(() => {
        setState('failed')
        setMessage('That could not be sent. It is saved locally and will retry on its own.')
      })
  }

  return (
    <div className="mt-3 rounded-md border border-white/[0.06] bg-black/[0.18] p-3">
      <div className="text-[12.5px] font-semibold text-[var(--mimir-text)]">
        {crashes === 1 ? 'Mimir recorded a problem' : `Mimir recorded ${crashes} problems`}
      </div>
      <p className="mt-1 max-w-[520px] text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
        {state === 'sent'
          ? message
          : 'Sending this is the only way anyone finds out it happened. It contains the error and where it came from -- no video.'}
      </p>

      {state !== 'sent' && (
        <>
          <details className="mt-2">
            <summary className="cursor-pointer text-[12px] text-[var(--mimir-text-muted)] hover:text-[var(--mimir-text)]">
              See exactly what would be sent
            </summary>
            <pre className="mt-2 max-h-48 overflow-auto rounded bg-black/40 p-2 text-[11px] leading-4 text-[var(--mimir-text-subtle)]">
              {log}
            </pre>
          </details>

          <input
            value={note}
            onChange={event => setNote(event.target.value)}
            placeholder="What were you doing? (optional)"
            className="mt-2 w-full rounded-md border border-white/[0.08] bg-black/25 px-2.5 py-2 text-[12px] text-[var(--mimir-text)] outline-none focus:border-[rgba(157,183,170,0.28)]"
          />

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={send}
              disabled={state === 'sending'}
              className="rounded-md bg-[var(--mimir-text)] px-3 py-2 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-wait disabled:bg-white/[0.22] disabled:text-white/50"
            >
              {state === 'sending' ? 'Sending…' : 'Send report'}
            </button>
            {state === 'failed' && (
              <span className="text-[12px] text-amber-100/[0.78]">{message}</span>
            )}
          </div>
        </>
      )}
    </div>
  )
}
