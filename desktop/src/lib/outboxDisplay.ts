// Display helpers for the Outbox submission queue. The queue itself is
// src-tauri/src/outbox.rs -- this module is only about turning its on-disk
// record into something a person can read, and is kept separate from the
// panel so the wording rules are testable without rendering anything.

export interface OutboxEntry {
  kind: string
  package_id: string
  created_at: string
  attempts: number
  last_error: string
  status: string
  /**
   * Set by the Rust side when the submission can never succeed as it stands --
   * an oversized package, or a rejection the server will simply repeat.
   * Optional because entries written before this field existed lack it.
   */
  permanent_failure?: boolean
}

// Mirrors MAX_AUTO_RETRY_ATTEMPTS in src-tauri/src/outbox.rs. Duplicated
// rather than plumbed through a command because it is one number that only
// affects wording -- but it does have to be changed in both places.
export const MAX_AUTO_RETRY_ATTEMPTS = 5

export type OutboxState = 'sent' | 'cannot_send' | 'needs_manual_retry' | 'pending'

/** `chrono_like_now()` in main.rs writes `unix:<seconds>`, not an ISO string. */
export function outboxTimestamp(value: string) {
  const match = /^unix:(\d+)$/.exec(String(value ?? '').trim())

  if (!match) {
    return null
  }

  const seconds = Number(match[1])

  return Number.isFinite(seconds) ? new Date(seconds * 1000) : null
}

export function formatOutboxTimestamp(value: string) {
  return outboxTimestamp(value)?.toLocaleString() ?? 'Unknown time'
}

export function outboxKindLabel(kind: string) {
  const value = String(kind ?? '').toLowerCase()

  if (value === 'feedback') {
    return 'Feedback'
  }

  if (value === 'contribution') {
    return 'Footage contribution'
  }

  return 'Submission'
}

export function outboxState(entry: OutboxEntry): OutboxState {
  if (entry.status === 'sent') {
    return 'sent'
  }

  // Checked before the attempt cap: an unsendable package is not something a
  // manual retry rescues either, so offering one would just waste the upload.
  if (entry.permanent_failure) {
    return 'cannot_send'
  }

  // Past the cap, auto-retry-on-launch deliberately stops touching this entry,
  // so it will sit there forever unless the user asks. Saying "will retry
  // automatically" at that point would be a lie.
  return entry.attempts > MAX_AUTO_RETRY_ATTEMPTS ? 'needs_manual_retry' : 'pending'
}

export function outboxStateLabel(state: OutboxState) {
  if (state === 'sent') {
    return 'Sent'
  }

  if (state === 'cannot_send') {
    return 'Cannot be sent'
  }

  if (state === 'needs_manual_retry') {
    return 'Needs a manual retry'
  }

  return 'Waiting to send'
}

export function outboxStateDetail(state: OutboxState) {
  if (state === 'sent') {
    return 'This one reached Mimir.'
  }

  if (state === 'cannot_send') {
    return 'The submission service refused this and would refuse it again, so Mimir has stopped trying. Your encrypted copy is still saved on this computer.'
  }

  if (state === 'needs_manual_retry') {
    return `Mimir stopped retrying this automatically after ${MAX_AUTO_RETRY_ATTEMPTS} attempts. Your copy is still saved.`
  }

  return 'Mimir will try again next time it starts. Your copy is saved either way.'
}

/** Whether offering a retry button would be honest. */
export function canRetry(entry: OutboxEntry) {
  return outboxState(entry) !== 'sent' && outboxState(entry) !== 'cannot_send'
}

export function isUnsent(entry: OutboxEntry) {
  return outboxState(entry) !== 'sent'
}

/** Newest first, with anything still unsent ahead of anything already sent. */
export function sortedOutboxEntries(entries: OutboxEntry[]) {
  return [...entries].sort((left, right) => {
    if (isUnsent(left) !== isUnsent(right)) {
      return isUnsent(left) ? -1 : 1
    }

    return (outboxTimestamp(right.created_at)?.getTime() ?? 0) - (outboxTimestamp(left.created_at)?.getTime() ?? 0)
  })
}

export function outboxSummary(entries: OutboxEntry[]) {
  if (entries.length === 0) {
    return ''
  }

  const blocked = entries.filter(entry => outboxState(entry) === 'cannot_send').length
  const waiting = entries.filter(canRetry).length

  // Blocked entries are called out separately: rolling them into "waiting to
  // send" would promise a delivery that is never coming.
  const parts: string[] = []
  if (waiting > 0) {
    parts.push(`${waiting} waiting to send`)
  }
  if (blocked > 0) {
    parts.push(`${blocked} cannot be sent`)
  }
  if (parts.length > 0) {
    return parts.join(', ')
  }

  return entries.length === 1 ? '1 sent' : `${entries.length} sent`
}
