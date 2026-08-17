import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { ErrorNotice } from './ErrorNotice'
import { describeError, type DescribedError } from '../lib/errorMessages'
import {
  canRetry,
  formatOutboxTimestamp,
  isUnsent,
  outboxKindLabel,
  outboxState,
  outboxStateDetail,
  outboxStateLabel,
  outboxSummary,
  sortedOutboxEntries,
  type OutboxEntry,
} from '../lib/outboxDisplay'
import type { OutboxSubmitResult } from '../lib/incidentActions'

// The Outbox is a durable on-disk queue (src-tauri/src/outbox.rs): a feedback
// or contribution package is age-encrypted and written to disk *before* any
// upload is attempted, and is never deleted. Until this panel existed, both
// `list_outbox_entries` and `retry_outbox_entry` were registered Tauri
// commands with no caller, so the "Retry sending" action the docs describe
// did not exist and a user had no way to see that a submission never left.
//
// Renders nothing at all when the queue is empty, which is the normal case --
// this should not be a permanent fixture on the import screen.
export function OutboxPanel() {
  const [entries, setEntries] = useState<OutboxEntry[]>([])
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const [isRetryingAll, setIsRetryingAll] = useState(false)
  const [actionError, setActionError] = useState<DescribedError | null>(null)
  const [actionMessage, setActionMessage] = useState('')

  // A failed listing renders nothing rather than an error. Three reasons: a
  // missing Outbox folder is not an error on the Rust side (list_entries
  // returns an empty vec), so this only fires on genuine filesystem trouble;
  // there is no action the user could take from a passive listing on the
  // landing screen; and the submit path itself reports its own failures
  // loudly, with the package kept on disk regardless. Staying silent here
  // also keeps the panel out of any non-Tauri context, where there is no
  // Outbox to read in the first place.
  const refresh = async () => {
    try {
      setEntries(await invoke<OutboxEntry[]>('list_outbox_entries'))
    } catch {
      setEntries([])
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const describeResult = (result: OutboxSubmitResult) => {
    if (result.status === 'sent') {
      return 'Sent.'
    }

    // "blocked" comes back when the encrypted package is missing or its kind
    // is unrecognized -- retrying will not help, so say so rather than
    // inviting the user to press the button again.
    if (result.status === 'blocked') {
      return `This one cannot be sent: ${result.message}`
    }

    return result.message
  }

  const runAction = async (label: string, action: () => Promise<string>) => {
    setActionError(null)
    setActionMessage('')

    try {
      setActionMessage(await action())
    } catch (error) {
      setActionError(describeError(error, `${label} did not work. Your saved copy is untouched.`))
    } finally {
      await refresh()
    }
  }

  const handleRetry = async (packageId: string) => {
    setRetryingId(packageId)
    await runAction('Retrying that submission', async () =>
      describeResult(await invoke<OutboxSubmitResult>('retry_outbox_entry', { packageId })),
    )
    setRetryingId(null)
  }

  const handleRetryAll = async () => {
    setIsRetryingAll(true)
    await runAction('Retrying the waiting submissions', async () => {
      const results = await invoke<OutboxSubmitResult[]>('retry_pending_outbox')

      if (results.length === 0) {
        return 'Nothing was waiting to send.'
      }

      const sent = results.filter(result => result.status === 'sent').length

      return `${sent} of ${results.length} sent.`
    })
    setIsRetryingAll(false)
  }

  // An empty queue is the common case, and it deserves no UI.
  if (entries.length === 0) {
    return null
  }

  const sorted = sortedOutboxEntries(entries)
  const unsentCount = entries.filter(isUnsent).length
  // Only entries a retry could actually help. An oversized package is unsent
  // but retrying it just re-earns the same rejection.
  const retryableCount = entries.filter(canRetry).length
  const isBusy = isRetryingAll || retryingId !== null

  return (
    <details className="mt-4 rounded-lg border border-white/[0.045] bg-black/[0.12] p-3" open={unsentCount > 0}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-[12px] font-semibold text-[var(--mimir-text-muted)]">
        <span>Submissions</span>
        <span className="text-[11px] font-normal text-[var(--mimir-text-subtle)]">{outboxSummary(entries)}</span>
      </summary>

      <p className="mt-3 max-w-[560px] text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
        Feedback and footage you chose to send. Each one is encrypted on this device before it leaves, and your
        local copy is kept whether or not it sends.
      </p>

      {retryableCount > 1 && (
        <button
          type="button"
          onClick={handleRetryAll}
          disabled={isBusy}
          className="mt-3 h-9 rounded-lg border border-white/[0.08] bg-white/[0.045] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.075] disabled:cursor-not-allowed disabled:opacity-45"
        >
          {isRetryingAll ? 'Retrying...' : `Retry all ${retryableCount} waiting`}
        </button>
      )}

      <ul className="mt-3 grid gap-2" role="list">
        {sorted.map(item => {
          const state = outboxState(item)

          return (
            <li
              key={item.package_id}
              className="rounded-lg border border-white/[0.055] bg-black/[0.16] p-3 text-[12px] leading-5"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-semibold text-[var(--mimir-text)]">{outboxKindLabel(item.kind)}</span>
                <span className="text-[11px] text-[var(--mimir-text-subtle)]">
                  {formatOutboxTimestamp(item.created_at)}
                </span>
              </div>

              <div
                className={
                  state === 'sent'
                    ? 'mt-1 text-emerald-100/[0.78]'
                    : state === 'cannot_send'
                      ? 'mt-1 text-red-100/80'
                      : 'mt-1 text-amber-100/[0.78]'
                }
              >
                {outboxStateLabel(state)}
                {item.attempts > 0 && ` -- ${item.attempts} attempt${item.attempts === 1 ? '' : 's'}`}
              </div>
              <div className="mt-1 text-[var(--mimir-text-subtle)]">{outboxStateDetail(state)}</div>

              {item.last_error && (
                <details className="mt-2 rounded-md border border-white/[0.06] bg-black/25 p-2.5">
                  <summary className="cursor-pointer text-[11px] font-medium text-[var(--mimir-text-muted)]">
                    Why it did not send
                  </summary>
                  <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-[var(--mimir-text-subtle)]">
                    {item.last_error}
                  </pre>
                </details>
              )}

              {canRetry(item) && (
                <button
                  type="button"
                  onClick={() => handleRetry(item.package_id)}
                  disabled={isBusy}
                  className="mt-2.5 h-8 rounded-lg border border-white/[0.08] bg-white/[0.045] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.075] disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {retryingId === item.package_id
                    ? 'Retrying...'
                    : `Retry sending this ${outboxKindLabel(item.kind).toLowerCase()}`}
                </button>
              )}
            </li>
          )
        })}
      </ul>

      <div aria-live="polite" className="mt-2 empty:mt-0">
        {actionMessage && <p className="text-[12px] leading-5 text-[var(--mimir-text-muted)]">{actionMessage}</p>}
      </div>
      <ErrorNotice error={actionError} className="mt-2" />
    </details>
  )
}
