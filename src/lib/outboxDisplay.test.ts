import { describe, expect, it } from 'vitest'
import {
  MAX_AUTO_RETRY_ATTEMPTS,
  formatOutboxTimestamp,
  isUnsent,
  outboxKindLabel,
  outboxState,
  outboxStateDetail,
  outboxStateLabel,
  outboxSummary,
  outboxTimestamp,
  sortedOutboxEntries,
  type OutboxEntry,
} from './outboxDisplay'

const entry = (fields: Partial<OutboxEntry>): OutboxEntry => ({
  kind: 'feedback',
  package_id: 'a'.repeat(32),
  created_at: 'unix:1785000000',
  attempts: 0,
  last_error: '',
  status: 'pending',
  ...fields,
})

describe('outboxTimestamp', () => {
  it('parses the unix:<seconds> format the Rust side writes', () => {
    expect(outboxTimestamp('unix:1785000000')?.getTime()).toBe(1785000000000)
    expect(outboxTimestamp('  unix:0  ')?.getTime()).toBe(0)
  })

  it('returns null for anything else, including an ISO string', () => {
    // Worth pinning: an ISO date is the format a reader would assume, and
    // silently accepting one would hide a real mismatch with chrono_like_now.
    expect(outboxTimestamp('2026-08-02T10:00:00Z')).toBeNull()
    expect(outboxTimestamp('unix:')).toBeNull()
    expect(outboxTimestamp('unix:-5')).toBeNull()
    expect(outboxTimestamp('unix:abc')).toBeNull()
    expect(outboxTimestamp('')).toBeNull()
    expect(outboxTimestamp(undefined as never)).toBeNull()
  })
})

describe('formatOutboxTimestamp', () => {
  it('renders something other than the raw stamp', () => {
    const formatted = formatOutboxTimestamp('unix:1785000000')
    expect(formatted).not.toBe('unix:1785000000')
    expect(formatted.length).toBeGreaterThan(0)
  })

  it('degrades to a readable placeholder rather than showing "unix:..."', () => {
    expect(formatOutboxTimestamp('nonsense')).toBe('Unknown time')
  })
})

describe('outboxKindLabel', () => {
  it('names the two real submission kinds', () => {
    expect(outboxKindLabel('feedback')).toBe('Feedback')
    expect(outboxKindLabel('contribution')).toBe('Footage contribution')
  })

  it('falls back to a neutral noun for an unknown kind', () => {
    expect(outboxKindLabel('something_else')).toBe('Submission')
    expect(outboxKindLabel('')).toBe('Submission')
  })
})

describe('outboxState', () => {
  it('reports sent regardless of how many attempts it took', () => {
    expect(outboxState(entry({ status: 'sent', attempts: 9 }))).toBe('sent')
  })

  it('stays "pending" up to and including the auto-retry cap', () => {
    expect(outboxState(entry({ attempts: 0 }))).toBe('pending')
    expect(outboxState(entry({ attempts: MAX_AUTO_RETRY_ATTEMPTS }))).toBe('pending')
  })

  it('flips to needs_manual_retry one past the cap, matching retry_pending', () => {
    // outbox.rs skips entries where `attempts > MAX_AUTO_RETRY_ATTEMPTS`, so
    // the boundary here has to be the same or the UI promises a retry that
    // will never happen.
    expect(outboxState(entry({ attempts: MAX_AUTO_RETRY_ATTEMPTS + 1 }))).toBe('needs_manual_retry')
  })
})

describe('outboxStateLabel / outboxStateDetail', () => {
  it('never promises an automatic retry once auto-retry has given up', () => {
    expect(outboxStateLabel('needs_manual_retry')).toBe('Needs a manual retry')
    expect(outboxStateDetail('needs_manual_retry')).not.toMatch(/will try again/i)
    expect(outboxStateDetail('pending')).toMatch(/will try again/i)
  })

  it('always reassures that the local copy survives', () => {
    expect(outboxStateDetail('pending')).toMatch(/saved/i)
    expect(outboxStateDetail('needs_manual_retry')).toMatch(/saved/i)
  })
})

describe('isUnsent', () => {
  it('counts anything not yet sent, including capped entries', () => {
    expect(isUnsent(entry({ status: 'sent' }))).toBe(false)
    expect(isUnsent(entry({ status: 'pending' }))).toBe(true)
    expect(isUnsent(entry({ attempts: 99 }))).toBe(true)
  })
})

describe('sortedOutboxEntries', () => {
  it('puts unsent entries first, then orders each group newest first', () => {
    const sorted = sortedOutboxEntries([
      entry({ package_id: 'sent-old', status: 'sent', created_at: 'unix:100' }),
      entry({ package_id: 'pending-old', created_at: 'unix:200' }),
      entry({ package_id: 'sent-new', status: 'sent', created_at: 'unix:900' }),
      entry({ package_id: 'pending-new', created_at: 'unix:800' }),
    ])
    expect(sorted.map(item => item.package_id)).toEqual(['pending-new', 'pending-old', 'sent-new', 'sent-old'])
  })

  it('does not mutate the array it was given', () => {
    const entries = [entry({ package_id: 'b', status: 'sent' }), entry({ package_id: 'a' })]
    sortedOutboxEntries(entries)
    expect(entries.map(item => item.package_id)).toEqual(['b', 'a'])
  })

  it('tolerates unparseable timestamps', () => {
    expect(sortedOutboxEntries([entry({ created_at: 'nonsense' }), entry({ created_at: 'unix:5' })])).toHaveLength(2)
  })
})

describe('outboxSummary', () => {
  it('leads with the unsent count, singular and plural', () => {
    expect(outboxSummary([entry({})])).toBe('1 waiting to send')
    expect(outboxSummary([entry({}), entry({})])).toBe('2 waiting to send')
  })

  it('reports sent counts only when nothing is outstanding', () => {
    expect(outboxSummary([entry({ status: 'sent' })])).toBe('1 sent')
    expect(outboxSummary([entry({ status: 'sent' }), entry({})])).toBe('1 waiting to send')
  })

  it('says nothing at all for an empty queue', () => {
    expect(outboxSummary([])).toBe('')
  })
})
