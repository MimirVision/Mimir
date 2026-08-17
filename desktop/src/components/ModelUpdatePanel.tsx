import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { ErrorNotice } from './ErrorNotice'
import { describeError, type DescribedError } from '../lib/errorMessages'

interface ModelStatusResult {
  installed: boolean
  source: string
  detector_id: string
  detector_version: string
  target_dir: string
}

interface ModelUpdateResult {
  ok: boolean
  detector_id: string
  detector_version: string
  installed_to: string
  message: string
}

// Lets a validated detector model update be installed without reinstalling
// the app -- points the backend at a locally downloaded model package
// (a folder with a model_manifest.json + the model file it declares) and
// installs it into a per-user override directory that the scanner checks
// ahead of its bundled model. There is no automatic "check for updates"
// here: that needs a hosting/distribution decision this app doesn't have
// yet, so today this is a manual, verified install path only.
export function ModelUpdatePanel() {
  const [status, setStatus] = useState<ModelStatusResult | null>(null)
  const [statusError, setStatusError] = useState<DescribedError | null>(null)
  const [installing, setInstalling] = useState(false)
  const [installMessage, setInstallMessage] = useState('')
  const [installError, setInstallError] = useState<DescribedError | null>(null)

  const refreshStatus = async () => {
    try {
      const result = await invoke<ModelStatusResult>('active_model_status')
      setStatus(result)
      setStatusError(null)
    } catch (error) {
      setStatusError(describeError(error, 'The current detector model could not be read.'))
    }
  }

  useEffect(() => {
    void refreshStatus()
  }, [])

  const handleInstall = async () => {
    setInstallError(null)

    // The folder picker is inside the try: a cancelled or failed dialog
    // rejects, and letting that escape surfaced as an unhandled rejection
    // rather than as anything the user could see.
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
        title: 'Choose a model update folder (contains model_manifest.json)',
      })
      if (!selected || Array.isArray(selected)) {
        return
      }

      setInstalling(true)
      setInstallMessage('')
      const result = await invoke<ModelUpdateResult>('install_model_update', { packagePath: selected })
      setInstallMessage(`${result.message} (${result.detector_id} ${result.detector_version})`)
      await refreshStatus()
    } catch (error) {
      setInstallError(
        describeError(error, 'That model update could not be installed. Check the folder contains a model_manifest.json.'),
      )
    } finally {
      setInstalling(false)
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-white/[0.055] bg-black/[0.16] p-4">
      <div className="text-[14px] font-semibold text-[var(--mimir-text)]">Detector model</div>
      <p className="mt-1 max-w-[560px] text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
        Mimir ships with a stock detection model. A validated improvement can be installed from a
        downloaded model package without reinstalling the app.
      </p>

      <div className="mt-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        {statusError ? (
          <span className="text-red-100/[0.78]">{statusError.message}</span>
        ) : status?.installed ? (
          <span>
            Using installed update: <span className="text-[var(--mimir-text)]">{status.detector_id} {status.detector_version}</span>
          </span>
        ) : (
          <span>Using the bundled stock model.</span>
        )}
      </div>

      <button
        type="button"
        onClick={handleInstall}
        disabled={installing}
        className="mt-3 h-9 rounded-lg border border-white/[0.08] bg-white/[0.045] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.075] disabled:cursor-not-allowed disabled:opacity-45"
      >
        {installing ? 'Installing...' : 'Install model update from folder...'}
      </button>

      {installMessage && (
        <p className="mt-2 text-[12px] leading-5 text-emerald-100/[0.78]">{installMessage}</p>
      )}
      <ErrorNotice error={installError} className="mt-2" />
    </div>
  )
}
