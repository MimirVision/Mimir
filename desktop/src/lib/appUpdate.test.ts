import { describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/plugin-updater', () => ({ check: vi.fn() }))
import { check } from '@tauri-apps/plugin-updater'
import { findUpdate, formatBytes } from './appUpdate'

describe('findUpdate', () => {
  it('is silent when the check fails', async () => {
    // The normal state for an offline app, and for every build until a release
    // is published. A thrown error here must never reach the user.
    vi.mocked(check).mockRejectedValueOnce(new Error('network unreachable'))

    await expect(findUpdate()).resolves.toBeNull()
  })

  it('is silent when there is nothing newer', async () => {
    vi.mocked(check).mockResolvedValueOnce(null as never)

    await expect(findUpdate()).resolves.toBeNull()
  })

  it('returns the update when one exists', async () => {
    vi.mocked(check).mockResolvedValueOnce({ version: '0.6.0' } as never)

    await expect(findUpdate()).resolves.toMatchObject({ version: '0.6.0' })
  })
})

describe('formatBytes', () => {
  it('reports a real download in the units someone judges a download by', () => {
    expect(formatBytes(206_394_090)).toBe('197 MB')
  })

  it('switches to GB when megabytes stop being readable', () => {
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe('3.0 GB')
  })

  it('says nothing when the size is unknown', () => {
    // The server does not always send a length. Better an honest blank than
    // "0 MB", which reads as a broken download.
    expect(formatBytes(0)).toBe('')
    expect(formatBytes(Number.NaN)).toBe('')
  })
})
