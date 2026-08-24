import { useEffect, useState } from 'react'
import type { Update } from '@tauri-apps/plugin-updater'
import { findUpdate, formatBytes, installUpdate } from '../lib/appUpdate'

/**
 * Offers a newer Mimir, quietly.
 *
 * A bar rather than a dialog, and dismissible, because this can appear while a
 * 25-minute scan is running and an update is never more urgent than the thing
 * the user came to do. It says the size before anything downloads: on Windows
 * an update is the whole installer, and someone on a phone hotspot deserves to
 * know that before agreeing rather than after.
 */
export function UpdateNotice({ busy }: { busy: boolean }) {
  const [update, setUpdate] = useState<Update | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [downloaded, setDownloaded] = useState(0)
  const [total, setTotal] = useState(0)
  const [state, setState] = useState<'idle' | 'downloading' | 'installing' | 'failed'>('idle')

  useEffect(() => {
    let cancelled = false
    void findUpdate().then(found => {
      if (!cancelled) setUpdate(found)
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (!update || dismissed) {
    return null
  }

  const start = () => {
    setState('downloading')
    void installUpdate(
      update,
      (bytes, size) => {
        setDownloaded(bytes)
        setTotal(size)
      },
      () => setState('installing'),
    ).catch(() => setState('failed'))
  }

  const size = formatBytes(total)
  const percent = total > 0 ? Math.min(100, Math.round((downloaded / total) * 100)) : 0

  return (
    <div
      role="status"
      className="mx-auto mb-4 flex w-full max-w-[1100px] flex-wrap items-center justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.028] px-4 py-3"
    >
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-[var(--mimir-text)]">
          {state === 'installing'
            ? 'Installing Mimir ' + update.version
            : state === 'failed'
              ? 'That update could not be installed'
              : `Mimir ${update.version} is available`}
        </div>
        <p className="mt-0.5 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          {state === 'downloading' && (
            <>
              Downloading{size ? ` ${size}` : ''}
              {percent > 0 ? ` — ${percent}%` : ''}. You can keep using Mimir.
            </>
          )}
          {state === 'installing' && 'Mimir will close to finish. Reopen it when the installer is done.'}
          {state === 'failed' && 'You can download it yourself from the Mimir releases page instead.'}
          {state === 'idle' && (
            <>
              The update is the full installer, so it is a sizeable download.
              {busy ? ' Best left until the current scan has finished.' : ''}
            </>
          )}
        </p>
      </div>

      {state === 'downloading' && (
        <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full bg-[var(--mimir-accent)] transition-[width]"
            style={{ width: `${percent}%` }}
          />
        </div>
      )}

      {(state === 'idle' || state === 'failed') && (
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="rounded-md px-3 py-2 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]"
          >
            Not now
          </button>
          {state === 'idle' && (
            <button
              type="button"
              onClick={start}
              className="rounded-md bg-[var(--mimir-text)] px-3 py-2 text-[12px] font-semibold text-black transition hover:bg-white"
            >
              Update
            </button>
          )}
        </div>
      )}
    </div>
  )
}
