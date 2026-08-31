import { describe, expect, it } from 'vitest'
import { countCrashes } from './crashReport'

describe('countCrashes', () => {
  it('says nothing happened when the log is empty', () => {
    expect(countCrashes('')).toBe(0)
    expect(countCrashes('   \n  ')).toBe(0)
  })

  it('counts records by their separator', () => {
    const log = [
      'timestamp: 2026-08-24T10:00:00Z', 'error message: boom', '---',
      'timestamp: 2026-08-24T10:05:00Z', 'error message: boom again', '---',
    ].join('\n')

    expect(countCrashes(log)).toBe(2)
  })

  it('still reports one when the tail was cut before a separator', () => {
    // read_recent_crash_log trims to a byte budget, so the oldest record in a
    // long log can arrive without its trailing '---'. Reporting 0 there would
    // hide the panel for someone who genuinely crashed.
    expect(countCrashes('error message: truncated entry with no separator')).toBe(1)
  })
})
