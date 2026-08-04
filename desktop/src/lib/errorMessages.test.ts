import { describe, expect, it } from 'vitest'
import { describeError, describeFailures, errorDetail, friendlyMessage } from './errorMessages'

describe('errorDetail', () => {
  it('reads Tauri string rejections', () => {
    expect(errorDetail('backend exited with code 1')).toBe('backend exited with code 1')
  })

  it('reads Error instances and plain message objects', () => {
    expect(errorDetail(new Error('boom'))).toBe('boom')
    expect(errorDetail({ message: 'boom' })).toBe('boom')
  })

  it('falls back to String() for anything else', () => {
    expect(errorDetail(42)).toBe('42')
    expect(errorDetail(null)).toBe('null')
  })
})

describe('friendlyMessage', () => {
  const fallback = 'It did not work.'

  it('translates the Windows error a missing session file actually produces', () => {
    const message = friendlyMessage(
      'The system cannot find the file specified. (os error 2)',
      fallback,
    )
    expect(message).toContain('could not be found')
    expect(message).not.toContain('os error')
  })

  it.each([
    ['Access is denied. (os error 5)', 'would not allow access'],
    ['The process cannot access the file because it is being used by another process. (os error 32)', 'open in another program'],
    ['There is not enough space on the disk. (os error 112)', 'free disk space'],
    ['The device is not ready. (os error 21)', 'not ready'],
  ])('translates %s', (raw, expected) => {
    expect(friendlyMessage(raw, fallback)).toContain(expected)
  })

  it('matches the English text when no os error code is present', () => {
    // Sidecars report failures without Rust's "(os error N)" suffix.
    expect(friendlyMessage('No such file or directory', fallback)).toContain('could not be found')
  })

  it('uses the fallback rather than showing an unrecognised raw string', () => {
    expect(friendlyMessage('ORA-00600 internal error', fallback)).toBe(fallback)
  })
})

describe('describeError', () => {
  it('keeps the raw text available as detail', () => {
    const described = describeError('Access is denied. (os error 5)', 'It did not work.')
    expect(described.message).toContain('would not allow access')
    expect(described.detail).toBe('Access is denied. (os error 5)')
  })

  it('drops the detail when it would only repeat the headline', () => {
    const described = describeError('It did not work.', 'It did not work.')
    expect(described.detail).toBe('')
  })
})

describe('describeFailures', () => {
  it('summarises per-item failures with raw text behind the headline', () => {
    const described = describeFailures(
      [
        { label: 'Clip A', error: 'The system cannot find the file specified. (os error 2)' },
        { label: 'Clip B', error: 'Access is denied. (os error 5)' },
      ],
      'Some clips could not be moved:',
      'The clip could not be moved.',
    )

    expect(described.message).toContain('Some clips could not be moved:')
    expect(described.message).toContain('Clip A: ')
    expect(described.message).not.toContain('os error')
    expect(described.detail).toContain('Clip A: The system cannot find the file specified. (os error 2)')
    expect(described.detail).toContain('Clip B: Access is denied. (os error 5)')
  })
})
