import { check, type Update } from '@tauri-apps/plugin-updater'

/**
 * Checking for, and installing, a newer Mimir.
 *
 * Every piece of this shipped months ago -- the plugin, a pinned public key, an
 * endpoint, signed artifacts, a `latest.json` written by CI -- and nothing ever
 * called it, so no installed copy has ever updated itself. This is the call
 * that was missing.
 *
 * Two things it deliberately does not do. It does not nag: Mimir is an offline
 * app and a failed check is the normal state on a machine with no connection,
 * so a failure is swallowed rather than shown. And it does not download
 * anything on its own -- Tauri's Windows updater fetches the whole installer,
 * which is around 197 MB, and starting that behind someone's back on a metered
 * connection would be rude.
 */

/** Look for a newer version. Returns null when there is nothing, or on any failure. */
export async function findUpdate(): Promise<Update | null> {
  try {
    return await check()
  } catch {
    // No connection, no published release, an unreachable manifest: all of
    // these are ordinary for an offline-first app, and none is worth a dialog.
    return null
  }
}

/**
 * Download and install, reporting progress.
 *
 * No relaunch: on Windows the NSIS installer takes over the process, so
 * whether anything after this runs is not ours to decide. Adding
 * @tauri-apps/plugin-process to force it would be a dependency whose behaviour
 * could not be checked without a published release to update from, so the
 * caller tells the user to reopen Mimir instead and that is true either way.
 *
 * `onProgress` receives bytes so the caller can show something truthful about a
 * download this size rather than an indeterminate spinner.
 */
/**
 * Turn whatever downloadAndInstall rejected with into something readable.
 *
 * The first failure in the wild produced an empty line under "That update
 * could not be installed", because the rejection was neither an Error nor a
 * string and `String(value)` gave "[object Object]" or "". Diagnosing it meant
 * reading the registry by hand. A failed update has to say what failed, or the
 * next report is another screenshot of a blank space.
 */
export function describeUpdateFailure(value: unknown): string {
  if (value instanceof Error && value.message.trim()) {
    return value.message.trim()
  }
  if (typeof value === 'string' && value.trim()) {
    return value.trim()
  }
  if (value && typeof value === 'object') {
    // Tauri rejects with the serialized Rust error, whose shape varies by
    // plugin version; take whichever field carries text rather than assuming.
    const record = value as Record<string, unknown>
    for (const key of ['message', 'error', 'reason', 'description']) {
      const field = record[key]
      if (typeof field === 'string' && field.trim()) {
        return field.trim()
      }
    }
    try {
      const json = JSON.stringify(value)
      if (json && json !== '{}') {
        return json
      }
    } catch {
      // Circular or otherwise unserializable; fall through.
    }
  }
  return 'The installer exited without reporting a reason. This is usually a previous install the updater could not replace -- installing over it by hand fixes it.'
}

export async function installUpdate(
  update: Update,
  onProgress: (downloadedBytes: number, totalBytes: number) => void,
  onInstalling: () => void,
): Promise<void> {
  let downloaded = 0
  let total = 0

  await update.downloadAndInstall(event => {
    if (event.event === 'Started') {
      total = event.data.contentLength ?? 0
      onProgress(0, total)
      return
    }
    if (event.event === 'Progress') {
      downloaded += event.data.chunkLength
      onProgress(downloaded, total)
      return
    }
    if (event.event === 'Finished') {
      onInstalling()
    }
  })
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return ''
  }
  const megabytes = bytes / (1024 * 1024)
  return megabytes >= 1024 ? `${(megabytes / 1024).toFixed(1)} GB` : `${Math.round(megabytes)} MB`
}
