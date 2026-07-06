import { useEffect, useState } from 'react'
import mimirLockup from '../assets/mimir-lockup.png'
import { FULL_AI_BETA, MIMIR_VERSION, USE_MIMIR_CORE_V2 } from '../config'
import type {
  BackendProgress,
  LocalAiInstallResult,
  LocalAiStatus,
  ScanRunState,
  SessionLoadState,
  SystemCheckItem,
  SystemCheckResult,
} from '../types'

interface ScanOutput {
  stdout: string
  stderr: string
}

interface ImportPanelProps {
  selectedFolder: string
  isDraggingFolder: boolean
  onChooseFolder: () => void
  onAnalyze: () => void
  loadState: SessionLoadState
  scanState: ScanRunState
  scanError: string
  scanOutput: ScanOutput | null
  scanProgress: BackendProgress | null
  lastProgressMessage: string
  clipCount: number | null
  isCountingClips: boolean
  scanMode: ScanMode
  onScanModeChange: (mode: ScanMode) => void
  systemCheck: SystemCheckResult | null
  isCheckingSystem: boolean
  localAiStatus: LocalAiStatus | null
  isCheckingLocalAi: boolean
  selectedVisionModel: string
  isLocalAiSetupOpen: boolean
  onOpenLocalAiSetup: () => void
  onCloseLocalAiSetup: () => void
  onRecheckLocalAi: () => void | Promise<void>
  onOpenLocalAiDownloadPage: () => void | Promise<void>
  onPullLocalAiModel: () => void | Promise<void>
  isPullingLocalAiModel: boolean
  localAiInstallLine: string
  localAiInstallResult: LocalAiInstallResult | null
  localAiSetupError: string
}

type ScanMode = 'fast' | 'balanced' | 'quality'

const scanStages = [
  { label: 'Reading clips', keys: ['initializing', 'reading_clips', 'preparing_clips', 'discovering_clips'] },
  { label: 'Reading event metadata', keys: ['reading_event_metadata', 'event_metadata'] },
  { label: 'Grouping camera angles', keys: ['grouping_camera_angles', 'camera_grouping'] },
  { label: 'Detecting activity', keys: ['detecting_activity', 'scanning_video', 'scanning', 'yolo'] },
  { label: 'Reviewing suspicious moments', keys: ['reviewing_suspicious_moments', 'ai_review', 'reviewing_event'] },
  { label: 'Building incident timeline', keys: ['building_incident_timeline', 'building_timeline', 'finalizing_event'] },
  { label: 'Writing results', keys: ['writing_output', 'writing_results', 'writing_session'] },
]

const scanModeOptions: Array<{
  value: ScanMode
  title: string
  description: string
}> = [
  {
    value: 'fast',
    title: 'Fast',
    description: 'Quick review for large folders.',
  },
  {
    value: 'balanced',
    title: 'Balanced',
    description: 'Recommended for most scans.',
  },
  {
    value: 'quality',
    title: 'Thorough',
    description: 'Slower, more careful review.',
  },
]

function formatStage(value?: string) {
  if (!value) {
    return 'Starting scan...'
  }

  const normalizedStage = value.toLowerCase()

  if (normalizedStage.includes('complete')) {
    return 'Scan complete'
  }

  if (normalizedStage.includes('error')) {
    return 'Scan error'
  }

  const mappedStage = scanStages.find(item =>
    item.keys.some(key => normalizedStage.includes(key)),
  )

  if (mappedStage) {
    return mappedStage.label
  }

  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatElapsed(seconds?: number) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) {
    return null
  }

  return `Elapsed: ${formatClock(seconds)}`
}

function formatClock(seconds: number) {
  const safeSeconds = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  const remainingSeconds = safeSeconds % 60

  return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`
}

function activeStageIndex(stage?: string) {
  if (!stage) {
    return -1
  }

  const normalizedStage = stage.toLowerCase()

  if (normalizedStage.includes('complete')) {
    return scanStages.length
  }

  if (normalizedStage.includes('error')) {
    return 0
  }

  const index = scanStages.findIndex(item =>
    item.keys.some(key => normalizedStage.includes(key)),
  )

  return index >= 0 ? index : 0
}

function clampPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return null
  }

  return Math.max(0, Math.min(100, value))
}

function formatScanMode(value: ScanMode) {
  if (value === 'quality') {
    return 'Thorough scan'
  }

  return `${value.charAt(0).toUpperCase()}${value.slice(1)} scan`
}

function formatEtaSeconds(seconds: number) {
  const remaining = Math.max(0, Math.round(seconds))

  if (remaining < 60) {
    return `About ${remaining} sec left`
  }

  return `About ${Math.max(1, Math.round(remaining / 60))} min left`
}

function formatEta(progress: BackendProgress | null, percent: number | null) {
  const eta = progress?.eta_sec

  if (typeof eta === 'number' && !Number.isNaN(eta)) {
    return formatEtaSeconds(eta)
  }

  const elapsed = progress?.elapsed_sec

  if (
    percent !== null &&
    percent > 3 &&
    typeof elapsed === 'number' &&
    !Number.isNaN(elapsed) &&
    elapsed > 3
  ) {
    const estimatedTotal = elapsed / (percent / 100)
    return formatEtaSeconds(estimatedTotal - elapsed)
  }

  return 'Estimating…'
}

function stageStatus(index: number, currentIndex: number, scanState: ScanRunState) {
  if (scanState === 'error' && index === Math.max(0, currentIndex)) {
    return 'error'
  }

  if (currentIndex >= scanStages.length || index < currentIndex) {
    return 'complete'
  }

  if (index === currentIndex) {
    return 'active'
  }

  return 'idle'
}

function StageIcon({ status }: { status: string }) {
  if (status === 'complete') {
    return (
      <span className="grid h-5 w-5 place-items-center rounded-full bg-white/14 text-[12px] font-semibold text-[var(--mimir-text)]">
        ✓
      </span>
    )
  }

  if (status === 'active') {
    return (
      <span className="grid h-5 w-5 place-items-center rounded-full border border-white/22">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/20 border-t-white/80" />
      </span>
    )
  }

  if (status === 'error') {
    return (
      <span className="grid h-5 w-5 place-items-center rounded-full border border-red-200/30 bg-red-500/12 text-[12px] font-semibold text-red-100">
        !
      </span>
    )
  }

  return <span className="h-5 w-5 rounded-full border border-white/12 bg-black/18" />
}

function failedSystemChecks(systemCheck: SystemCheckResult | null) {
  return systemCheck?.items.filter(item => !item.ok && item.id !== 'enhanced_ai_review') ?? []
}

function localAiLabel(localAiStatus: LocalAiStatus | null, isCheckingLocalAi: boolean) {
  if (USE_MIMIR_CORE_V2) {
    return 'Ready to scan'
  }

  if (isCheckingLocalAi) {
    return 'Checking AI review'
  }

  if (localAiStatus?.ok) {
    return 'AI review ready'
  }

  return 'Setup needed'
}

function SystemStatusPill({
  systemCheck,
  isCheckingSystem,
  localAiStatus,
  isCheckingLocalAi,
}: {
  systemCheck: SystemCheckResult | null
  isCheckingSystem: boolean
  localAiStatus: LocalAiStatus | null
  isCheckingLocalAi: boolean
}) {
  if (isCheckingSystem) {
    return (
      <div className="flex h-10 items-center gap-2 rounded-full bg-white/[0.035] px-4 text-[13px] text-[var(--mimir-text-muted)]">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/16 border-t-white/70" />
        Checking system
      </div>
    )
  }

  if (systemCheck?.ok) {
    return (
      <div className="flex h-10 items-center gap-2 rounded-full bg-white/[0.035] px-4 text-[13px] text-[var(--mimir-text-muted)]">
        <span className="h-2 w-2 rounded-full bg-[var(--mimir-status-green)]" />
        {localAiLabel(localAiStatus, isCheckingLocalAi)}
      </div>
    )
  }

  return (
    <div className="flex h-10 items-center gap-2 rounded-full border border-[rgba(173,139,85,0.24)] bg-[rgba(173,139,85,0.10)] px-4 text-[13px] text-amber-100/88">
      <span className="h-2 w-2 rounded-full bg-amber-200/80" />
      Setup needed
    </div>
  )
}

function LocalAiSetupAssistant({
  localAiStatus,
  isCheckingLocalAi,
  selectedVisionModel,
  isOpen,
  onOpen,
  onClose,
  onRecheck,
  onOpenDownloadPage,
  onPullModel,
  isPullingModel,
  installLine,
  installResult,
  setupError,
}: {
  localAiStatus: LocalAiStatus | null
  isCheckingLocalAi: boolean
  selectedVisionModel: string
  isOpen: boolean
  onOpen: () => void
  onClose: () => void
  onRecheck: () => void | Promise<void>
  onOpenDownloadPage: () => void | Promise<void>
  onPullModel: () => void | Promise<void>
  isPullingModel: boolean
  installLine: string
  installResult: LocalAiInstallResult | null
  setupError: string
}) {
  const ready = localAiStatus?.ok === true
  const engineMissing = localAiStatus && !localAiStatus.ollama_available
  const modelMissing = localAiStatus?.ollama_available && !localAiStatus.model_installed

  if (!isOpen && ready) {
    return (
      <div className="mt-4 rounded-lg border border-[rgba(120,146,122,0.22)] bg-[rgba(120,146,122,0.08)] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[13px] font-semibold text-green-100/90">AI review ready.</div>
            <p className="mt-1 text-[12px] leading-5 text-green-100/68">
              Mimir is ready to analyze footage.
            </p>
          </div>
          <button
            type="button"
            onClick={onOpen}
            className="rounded-md border border-white/10 bg-black/18 px-3 py-2 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.045]"
          >
            Details
          </button>
        </div>
      </div>
    )
  }

  if (!isOpen) {
    return (
      <div className="mt-4 rounded-lg border border-[rgba(173,139,85,0.22)] bg-[rgba(173,139,85,0.08)] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[13px] font-semibold text-amber-100/92">
              AI review is not ready.
            </div>
            <p className="mt-1 text-[12px] leading-5 text-amber-100/72">
              Mimir needs to finish setup before scanning.
            </p>
          </div>
          <button
            type="button"
            onClick={onOpen}
            className="rounded-md bg-[var(--mimir-text)] px-3 py-2 text-[12px] font-semibold text-black transition hover:bg-white"
          >
            Repair setup
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="mt-4 rounded-lg border border-[var(--mimir-border)] bg-black/20 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[14px] font-semibold text-[var(--mimir-text)]">AI review setup</div>
          <p className="mt-2 max-w-[620px] text-[13px] leading-6 text-[var(--mimir-text-muted)]">
            Mimir needs its local review model ready before scanning. Setup may use several GB of disk space.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-white/10 px-3 py-2 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.045]"
        >
          Close
        </button>
      </div>

      <div className="mt-4 rounded-lg border border-white/[0.08] bg-white/[0.025] p-4">
        {isCheckingLocalAi ? (
          <div className="flex items-center gap-3 text-[13px] text-[var(--mimir-text-muted)]">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/16 border-t-white/70" />
            Checking AI review setup...
          </div>
        ) : ready ? (
          <div>
            <div className="text-[13px] font-semibold text-green-100/90">AI review ready.</div>
            <p className="mt-2 text-[12px] leading-5 text-green-100/68">
              Mimir can analyze footage with the configured review model.
            </p>
          </div>
        ) : engineMissing ? (
          <div>
            <div className="text-[13px] font-semibold text-amber-100/92">Local AI setup needs repair</div>
            <p className="mt-2 text-[12px] leading-5 text-amber-100/72">
              Mimir needs local AI setup to review footage on your device.
            </p>
            <p className="mt-2 text-[12px] leading-5 text-amber-100/64">
              Install it, then return to Mimir and click Recheck.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={onOpenDownloadPage}
                className="rounded-md bg-[var(--mimir-text)] px-3 py-2 text-[12px] font-semibold text-black transition hover:bg-white"
              >
                Repair setup
              </button>
              <button
                type="button"
                onClick={onRecheck}
                className="rounded-md border border-white/10 bg-black/18 px-3 py-2 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.045]"
              >
                Recheck
              </button>
            </div>
          </div>
        ) : modelMissing ? (
          <div>
            <div className="text-[13px] font-semibold text-amber-100/92">Local AI setup is incomplete</div>
            <p className="mt-2 text-[12px] leading-5 text-amber-100/72">
              Mimir needs to download the local review model before scanning. This can take several GB.
            </p>
            <div className="mt-3 rounded-md border border-amber-100/12 bg-black/20 p-3 text-[12px] leading-5 text-amber-100/72">
              The model may require several GB of free disk space.
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={onPullModel}
                disabled={isPullingModel}
                className="inline-flex items-center gap-2 rounded-md bg-[var(--mimir-text)] px-3 py-2 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-wait disabled:bg-white/22 disabled:text-white/50"
              >
                {isPullingModel && (
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-black/20 border-t-black/80" />
                )}
                Repair setup
              </button>
              <button
                type="button"
                onClick={onRecheck}
                disabled={isPullingModel}
                className="rounded-md border border-white/10 bg-black/18 px-3 py-2 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.045] disabled:cursor-wait disabled:opacity-60"
              >
                Recheck
              </button>
            </div>
          </div>
        ) : (
          <div>
            <div className="text-[13px] font-semibold text-amber-100/92">
              AI review is not ready.
            </div>
            <p className="mt-2 text-[12px] leading-5 text-amber-100/72">
              Mimir needs to finish setup before scanning.
            </p>
          </div>
        )}

        {(isPullingModel || installLine || installResult || setupError) && (
          <div className="mt-4 rounded-md border border-white/[0.08] bg-black/24 p-3">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-[var(--mimir-text)]">
              {isPullingModel && (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/16 border-t-white/70" />
              )}
              {isPullingModel ? 'Downloading local vision model' : installResult?.ok ? 'Install complete' : 'Install status'}
            </div>
            {installLine && (
              <div className="mt-2 break-all text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                {installLine}
              </div>
            )}
            {installResult?.ok && (
              <div className="mt-2 text-[12px] leading-5 text-green-100/76">{installResult.message}</div>
            )}
            {(setupError || installResult?.ok === false) && (
              <div className="mt-2 text-[12px] leading-5 text-red-100/80">
                Setup could not finish. Open details for more information.
              </div>
            )}
          </div>
        )}

        <details className="mt-4 rounded-md border border-white/[0.08] bg-black/18 p-3">
          <summary className="cursor-pointer text-[12px] font-medium text-[var(--mimir-text-muted)]">
            Technical details
          </summary>
          <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-[var(--mimir-text-subtle)]">
            Selected model: {selectedVisionModel}
            {'\n'}
            {localAiStatus?.technical_details || installResult?.stdout || installResult?.stderr || 'No details available.'}
          </pre>
        </details>
      </div>
    </div>
  )
}

function SystemCheckWarning({ failures }: { failures: SystemCheckItem[] }) {
  if (failures.length === 0) {
    return null
  }

  return (
    <div className="mb-4 rounded-lg border border-[rgba(173,139,85,0.24)] bg-[rgba(173,139,85,0.09)] p-4">
      <div className="text-[14px] font-semibold text-amber-100/92">Setup warning</div>
      <p className="mt-2 text-[13px] leading-6 text-amber-100/78">
        Mimir found a local requirement that needs attention before scanning works reliably.
      </p>

      <div className="mt-3 grid gap-3">
        {failures.map(item => (
          <div key={item.id} className="rounded-lg border border-amber-100/12 bg-black/16 p-3">
            <div className="text-[13px] font-semibold text-amber-50/95">{item.message}</div>
            <div className="mt-2 text-[12px] leading-5 text-amber-100/72">{item.why_it_matters}</div>
            <div className="mt-2 text-[12px] leading-5 text-amber-100/86">{item.suggested_fix}</div>

            {item.technical_details && (
              <details className="mt-3 rounded-md border border-amber-100/12 bg-black/20 p-3">
                <summary className="cursor-pointer text-[12px] font-medium text-amber-100/80">
                  Technical details
                </summary>
                <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-amber-50/68">
                  {item.technical_details}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ReadinessPanel({
  systemCheck,
  isCheckingSystem,
  localAiStatus,
  isCheckingLocalAi,
  onOpenLocalAiSetup,
  onRecheckLocalAi,
  selectedVisionModel,
  localAiSetupError,
}: {
  systemCheck: SystemCheckResult | null
  isCheckingSystem: boolean
  localAiStatus: LocalAiStatus | null
  isCheckingLocalAi: boolean
  onOpenLocalAiSetup: () => void
  onRecheckLocalAi: () => void | Promise<void>
  selectedVisionModel: string
  localAiSetupError: string
}) {
  const [showDetails, setShowDetails] = useState(false)

  if (isCheckingSystem) {
    return (
      <div className="mb-4 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
        <div className="flex items-center gap-3">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/16 border-t-white/70" />
          <div>
            <div className="text-[14px] font-semibold text-[var(--mimir-text)]">Checking Mimir setup...</div>
            <p className="mt-1 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
              Mimir is making sure local review is ready.
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (systemCheck && !systemCheck.ok) {
    const details = systemCheck.items
      .filter(item => !item.ok && item.id !== 'enhanced_ai_review')
      .map(item => `${item.label}: ${item.technical_details || item.message}`)
      .join('\n\n')

    return (
      <div className="mb-4 rounded-lg border border-red-300/20 bg-red-500/10 p-4">
        <div className="text-[15px] font-semibold text-red-50">Mimir could not start the local scanner.</div>
        <p className="mt-2 text-[13px] leading-6 text-red-100/78">
          Try reinstalling Mimir. Technical details are available below.
        </p>
        {details && (
          <details className="mt-3 rounded-md border border-red-200/14 bg-black/22 p-3">
            <summary className="cursor-pointer text-[12px] font-medium text-red-100/85">
              Technical details
            </summary>
            <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-red-50/70">
              {details}
            </pre>
          </details>
        )}
      </div>
    )
  }

  const aiReady = localAiStatus?.ok === true
  const aiRequired = FULL_AI_BETA && !USE_MIMIR_CORE_V2

  if (!USE_MIMIR_CORE_V2 && isCheckingLocalAi) {
    return (
      <div className="mb-4 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
        <div className="flex items-center gap-3">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/16 border-t-white/70" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--mimir-text)]">Checking AI review...</div>
            <p className="mt-1 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
              Mimir is verifying setup before scanning.
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (aiRequired && !aiReady) {
    const detailText = [
      `Selected model: ${selectedVisionModel}`,
      localAiStatus?.message ? `Status: ${localAiStatus.message}` : '',
      localAiSetupError ? `Setup error: ${localAiSetupError}` : '',
      localAiStatus?.technical_details ? `Details: ${localAiStatus.technical_details}` : '',
    ]
      .filter(Boolean)
      .join('\n\n')

    return (
      <div className="mb-4 rounded-lg border border-[rgba(173,139,85,0.24)] bg-[rgba(173,139,85,0.10)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[15px] font-semibold text-amber-50">
              Mimir needs to finish setup before scanning.
            </div>
            <p className="mt-2 text-[13px] leading-6 text-amber-100/78">
              AI review is not ready.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onOpenLocalAiSetup}
              className="rounded-lg bg-[var(--mimir-text)] px-4 py-2.5 text-[13px] font-semibold text-black transition hover:bg-white"
            >
              Repair setup
            </button>
            <button
              type="button"
              onClick={onRecheckLocalAi}
              className="rounded-lg border border-white/10 bg-black/18 px-4 py-2.5 text-[13px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.045]"
            >
              Recheck
            </button>
            <button
              type="button"
              onClick={() => setShowDetails(value => !value)}
              className="rounded-lg border border-white/10 bg-black/18 px-4 py-2.5 text-[13px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.045]"
            >
              Show details
            </button>
          </div>
        </div>

        {showDetails && (
          <pre className="mt-4 max-h-44 overflow-auto whitespace-pre-wrap rounded-md border border-amber-100/12 bg-black/24 p-3 text-[11px] leading-5 text-amber-50/72">
            {detailText || 'No details available.'}
          </pre>
        )}
      </div>
    )
  }

  return null
}

export function ImportPanel({
  selectedFolder,
  isDraggingFolder,
  onChooseFolder,
  onAnalyze,
  loadState,
  scanState,
  scanError,
  scanOutput,
  scanProgress,
  lastProgressMessage,
  clipCount,
  isCountingClips,
  scanMode,
  onScanModeChange,
  systemCheck,
  isCheckingSystem,
  localAiStatus,
  isCheckingLocalAi,
  selectedVisionModel,
  isLocalAiSetupOpen,
  onOpenLocalAiSetup,
  onCloseLocalAiSetup,
  onRecheckLocalAi,
  onOpenLocalAiDownloadPage,
  onPullLocalAiModel,
  isPullingLocalAiModel,
  localAiInstallLine,
  localAiInstallResult,
  localAiSetupError,
}: ImportPanelProps) {
  const hasSelectedFolder = selectedFolder.length > 0
  const isAnalyzing = scanState === 'running'
  const isWorking = isAnalyzing || loadState === 'loading'
  const hasTechnicalDetails = Boolean(scanOutput?.stdout || scanOutput?.stderr)
  const currentStageIndex = activeStageIndex(scanProgress?.stage)
  const percent = clampPercent(scanProgress?.percent)
  const elapsedText = formatElapsed(scanProgress?.elapsed_sec)
  const etaText = formatEta(scanProgress, percent)
  const progressMessage = scanProgress
    ? scanProgress.message || 'Scanner progress received.'
    : 'Starting local scanner...'
  const currentVideo = scanProgress?.current_video
  const selectedScanMode = scanModeOptions.find(option => option.value === scanMode)
  const showScanStatus = isAnalyzing || scanState === 'error'
  const systemFailures = failedSystemChecks(systemCheck)
  const scannerReady = systemCheck?.ok === true
  const aiReviewReady = localAiStatus?.ok === true
  const aiRequired = FULL_AI_BETA && !USE_MIMIR_CORE_V2
  const canAnalyze = hasSelectedFolder && scannerReady && (!aiRequired || aiReviewReady) && !isWorking
  const [showBackendLoadHelper, setShowBackendLoadHelper] = useState(false)

  useEffect(() => {
    if (!isAnalyzing || scanProgress) {
      setShowBackendLoadHelper(false)
      return
    }

    const timer = window.setTimeout(() => {
      setShowBackendLoadHelper(true)
    }, 10_000)

    return () => {
      window.clearTimeout(timer)
    }
  }, [isAnalyzing, scanProgress])

  return (
    <main className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1440px] flex-col overflow-hidden rounded-xl border border-[var(--mimir-border)] bg-[radial-gradient(circle_at_48%_0%,rgba(255,255,255,0.045),transparent_34%),var(--mimir-bg-depth)] shadow-[0_30px_100px_rgba(0,0,0,0.56)] sm:min-h-[calc(100vh-48px)]">
      <header className="flex min-h-[76px] shrink-0 items-center justify-between px-5 sm:px-7">
        <img src={mimirLockup} alt="Mimir" className="h-8 w-auto opacity-95" />
        <SystemStatusPill
          systemCheck={systemCheck}
          isCheckingSystem={isCheckingSystem}
          localAiStatus={localAiStatus}
          isCheckingLocalAi={isCheckingLocalAi}
        />
      </header>

      <section className="grid flex-1 gap-10 px-5 pb-8 pt-5 sm:px-8 lg:grid-cols-[minmax(0,0.9fr)_minmax(420px,1.1fr)] lg:items-center lg:px-12 xl:px-16">
        <div className="max-w-[650px]">
          <div className="mb-5 text-[12px] font-medium uppercase tracking-[0.22em] text-[var(--mimir-text-subtle)]">
            Local incident review
          </div>
          <h1 className="max-w-[680px] text-[46px] font-semibold leading-[1.01] text-[var(--mimir-text)] sm:text-[60px] xl:text-[72px]">
            Find the moments worth watching.
          </h1>
          <p className="mt-5 max-w-[520px] text-[17px] leading-8 text-[var(--mimir-text-muted)]">
            Select a USB drive or footage folder. Mimir scans locally and helps you review suspicious moments.
          </p>
          <p className="mt-4 max-w-[500px] text-[14px] leading-6 text-[var(--mimir-text-subtle)]">
            Select the USB drive, TeslaCam folder, or any folder containing MP4 clips.
          </p>
          <div className="mt-8 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">
            Private Beta - {MIMIR_VERSION}
          </div>
        </div>

        <div className="flex min-h-[560px] flex-col rounded-xl border border-[var(--mimir-border)] bg-[linear-gradient(180deg,rgba(255,255,255,0.038),rgba(255,255,255,0.014))] p-4 shadow-[0_28px_80px_rgba(0,0,0,0.34)] sm:p-5">
          {!showScanStatus && (
            <>
              <ReadinessPanel
                systemCheck={systemCheck}
                isCheckingSystem={isCheckingSystem}
                localAiStatus={localAiStatus}
                isCheckingLocalAi={isCheckingLocalAi}
                onOpenLocalAiSetup={onOpenLocalAiSetup}
                onRecheckLocalAi={onRecheckLocalAi}
                selectedVisionModel={selectedVisionModel}
                localAiSetupError={localAiSetupError}
              />
              {!isCheckingSystem && scannerReady && <SystemCheckWarning failures={systemFailures} />}
              {!isCheckingSystem && scannerReady && isLocalAiSetupOpen && (
                <LocalAiSetupAssistant
                  localAiStatus={localAiStatus}
                  isCheckingLocalAi={isCheckingLocalAi}
                  selectedVisionModel={selectedVisionModel}
                  isOpen={isLocalAiSetupOpen}
                  onOpen={onOpenLocalAiSetup}
                  onClose={onCloseLocalAiSetup}
                  onRecheck={onRecheckLocalAi}
                  onOpenDownloadPage={onOpenLocalAiDownloadPage}
                  onPullModel={onPullLocalAiModel}
                  isPullingModel={isPullingLocalAiModel}
                  installLine={localAiInstallLine}
                  installResult={localAiInstallResult}
                  setupError={localAiSetupError}
                />
              )}

              <button
                onClick={onChooseFolder}
                disabled={isWorking}
                className={`group flex min-h-[300px] flex-1 items-center justify-center rounded-lg border border-dashed px-6 text-center transition disabled:cursor-wait disabled:opacity-70 ${
                  isDraggingFolder
                    ? 'border-white/38 bg-white/[0.07]'
                    : 'border-[var(--mimir-border-strong)] bg-black/22 hover:border-white/24 hover:bg-white/[0.032]'
                }`}
              >
                <div className="max-w-[520px]">
                  <div className="mx-auto mb-7 grid h-16 w-16 place-items-center rounded-full bg-white/[0.055] text-[30px] font-light text-[var(--mimir-text)] transition group-hover:bg-white/[0.08]">
                    +
                  </div>
                  <div className="text-[22px] font-semibold text-[var(--mimir-text)]">
                    Drop footage folder or USB here
                  </div>
                  <div className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
                    Select the USB drive, TeslaCam folder, or any folder containing MP4 clips.
                  </div>
                </div>
              </button>

              <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-center">
                <p className="text-[13px] leading-6 text-[var(--mimir-text-subtle)]">
                  Scanning does not move or delete clips.
                </p>

                <button
                  onClick={onChooseFolder}
                  disabled={isWorking}
                  className="h-12 rounded-lg bg-white/[0.055] px-5 text-[14px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.09] disabled:cursor-wait disabled:opacity-60"
                >
                  Choose USB drive or footage folder
                </button>
              </div>

              {hasSelectedFolder && (
                <div className="mt-4 rounded-lg bg-white/[0.032] p-4">
                  <div className="min-w-0">
                    <div className="mb-2 text-[11px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
                      Selected footage
                    </div>
                    <div className="truncate text-[14px] font-medium text-[var(--mimir-text)]">
                      {selectedFolder.split(/[\\/]/).filter(Boolean).pop() || 'Footage folder selected'}
                    </div>
                    <div className="mt-2 text-[13px] text-[var(--mimir-text-muted)]">
                      {isCountingClips
                        ? 'Reading folder...'
                        : clipCount !== null
                          ? `${clipCount} clips found`
                          : 'Clip count unavailable'}
                    </div>
                  </div>

                  <div className="mt-5 border-t border-white/[0.07] pt-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-[15px] font-semibold text-[var(--mimir-text)]">Scan mode</div>
                        <p className="mt-1 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
                          Pick how deeply Mimir should review this folder.
                        </p>
                      </div>
                      <div className="rounded-full border border-white/[0.08] bg-black/18 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--mimir-text-subtle)]">
                        Recommended: Balanced
                      </div>
                    </div>

                    <div className="mt-4 grid gap-2 sm:grid-cols-3">
                      {scanModeOptions.map(option => {
                        const active = option.value === scanMode

                        return (
                          <button
                            key={option.value}
                            type="button"
                            onClick={() => onScanModeChange(option.value)}
                            disabled={isWorking}
                            className={`rounded-lg border p-3 text-left transition disabled:cursor-wait disabled:opacity-65 ${
                              active
                                ? 'border-white/22 bg-white/[0.07]'
                                : 'border-[var(--mimir-border)] bg-black/18 hover:border-white/16 hover:bg-white/[0.035]'
                            }`}
                          >
                            <span className="flex items-center gap-2">
                              <span
                                className={`grid h-4 w-4 place-items-center rounded-full border ${
                                  active ? 'border-white/70' : 'border-white/20'
                                }`}
                              >
                                {active && <span className="h-2 w-2 rounded-full bg-white" />}
                              </span>
                              <span className="text-[13px] font-semibold text-[var(--mimir-text)]">
                                {option.title}
                              </span>
                            </span>
                            <span className="mt-2 block text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                              {option.description}
                            </span>
                          </button>
                        )
                      })}
                    </div>

                    <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-center">
                      <p className="text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
                        Selected: {selectedScanMode?.title ?? 'Balanced'} scan
                      </p>

                      <button
                        onClick={onAnalyze}
                        disabled={!canAnalyze}
                        className="h-12 rounded-lg bg-[var(--mimir-text)] px-7 text-[15px] font-semibold text-black shadow-[0_16px_38px_rgba(255,255,255,0.075)] transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/12 disabled:text-white/35 disabled:shadow-none"
                      >
                        Analyze footage
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {showScanStatus && (
            <div className="flex flex-1 flex-col justify-center rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="text-[14px] font-semibold text-[var(--mimir-text)]">
                    {scanState === 'error' ? 'Scan stopped' : 'Analyzing footage locally...'}
                  </div>
                  <div className="mt-1 text-[13px] leading-6 text-[var(--mimir-text-muted)]">
                    {scanState === 'error'
                      ? 'Mimir could not finish the local scan.'
                      : 'Processing stays on this device.'}
                  </div>
                </div>
                <div className="inline-flex rounded-full border border-[var(--mimir-border)] bg-black/18 px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
                  {formatScanMode(scanMode)}
                </div>
              </div>

              <div className="mt-5 rounded-lg bg-black/18 p-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
                      {formatStage(scanProgress?.stage)}
                    </div>
                    <div className="mt-2 text-[15px] font-medium text-[var(--mimir-text)]">
                      {progressMessage}
                    </div>
                    {showBackendLoadHelper && (
                      <div className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-subtle)]">
                        This can take a moment while the local scanner starts.
                      </div>
                    )}
                    {currentVideo && (
                      <div className="mt-2 max-w-[520px] truncate text-[13px] text-[var(--mimir-text-muted)]">
                        Current video: {currentVideo}
                      </div>
                    )}
                  </div>

                  <div className="text-right text-[12px] leading-6 text-[var(--mimir-text-muted)]">
                    {typeof scanProgress?.current === 'number' && typeof scanProgress?.total === 'number' && (
                      <div>
                        Clips: {scanProgress.current} / {scanProgress.total}
                      </div>
                    )}
                    {elapsedText && <div>{elapsedText}</div>}
                    <div>ETA: {etaText}</div>
                  </div>
                </div>

                <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/[0.08]">
                  {percent === null ? (
                    <div className="h-full w-1/3 animate-[mimir-progress-pulse_1.4s_ease-in-out_infinite] rounded-full bg-white/55" />
                  ) : (
                    <div
                      className="h-full rounded-full bg-[var(--mimir-text)] transition-all duration-300"
                      style={{ width: `${percent}%` }}
                    />
                  )}
                </div>

                <div className="mt-3 flex flex-wrap gap-2 text-[12px] text-[var(--mimir-text-subtle)]">
                  {percent !== null && <span>{Math.round(percent)}%</span>}
                  {typeof scanProgress?.incidents_created === 'number' && (
                    <span>{scanProgress.incidents_created} incidents found</span>
                  )}
                  {typeof scanProgress?.ai_calls === 'number' && <span>{scanProgress.ai_calls} AI checks</span>}
                </div>
              </div>

              <div className="mt-4 grid gap-2">
                {scanStages.map((stage, index) => {
                  const status = stageStatus(index, currentStageIndex, scanState)

                  return (
                  <div
                    key={stage.label}
                    className={`flex items-center gap-3 rounded-md border px-3 py-2 text-[13px] transition ${
                      status === 'active'
                        ? 'border-white/20 bg-white/[0.055] text-[var(--mimir-text)]'
                        : status === 'complete'
                          ? 'border-[var(--mimir-border)] bg-white/[0.025] text-[var(--mimir-text)]'
                          : status === 'error'
                            ? 'border-red-300/20 bg-red-500/10 text-red-100'
                            : 'border-[var(--mimir-border)] bg-black/18 text-[var(--mimir-text-subtle)]'
                    }`}
                  >
                    <StageIcon status={status} />
                    {stage.label}
                  </div>
                )})}
              </div>

              {scanState === 'error' && (
                <div className="mt-4 rounded-lg border border-red-400/20 bg-red-500/10 p-4">
                  <div className="text-[13px] leading-6 text-red-100">
                    {scanError || 'Could not run the local scan.'}
                  </div>
                  {lastProgressMessage && (
                    <div className="mt-2 text-[12px] leading-5 text-red-100/70">
                      Last progress update: {lastProgressMessage}
                    </div>
                  )}

                  {hasTechnicalDetails && (
                    <details className="mt-3 rounded-lg border border-red-300/15 bg-black/25 p-3">
                      <summary className="cursor-pointer text-[12px] font-medium text-red-100/85">
                        Technical details
                      </summary>
                      <div className="mt-3 grid gap-3">
                        {scanOutput?.stdout && (
                          <div>
                            <div className="mb-1 text-[11px] uppercase tracking-[0.16em] text-red-100/55">
                              stdout
                            </div>
                            <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md bg-black/35 p-3 text-[11px] leading-5 text-red-50/75">
                              {scanOutput.stdout}
                            </pre>
                          </div>
                        )}

                        {scanOutput?.stderr && (
                          <div>
                            <div className="mb-1 text-[11px] uppercase tracking-[0.16em] text-red-100/55">
                              stderr
                            </div>
                            <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md bg-black/35 p-3 text-[11px] leading-5 text-red-50/75">
                              {scanOutput.stderr}
                            </pre>
                          </div>
                        )}
                      </div>
                    </details>
                  )}
                </div>
              )}
            </div>
          )}

          {scanState === 'complete' && (
            <div className="mt-4 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4 text-[13px] leading-6 text-[var(--mimir-text-muted)]">
              Analysis complete. Mimir built the latest local session.
            </div>
          )}

          {loadState === 'loading' && !isAnalyzing && (
            <div className="mt-4 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4 text-[13px] text-[var(--mimir-text-muted)]">
              Loading latest local session...
            </div>
          )}
        </div>
      </section>
    </main>
  )
}
