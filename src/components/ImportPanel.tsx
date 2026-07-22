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
  SessionHistoryEntry,
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
  onCancelScan: () => void
  isCancellingScan: boolean
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
  experimentalAiEnabled: boolean
  experimentalAiModel: string
  experimentalAiTimeoutSec: AiTimeoutSec
  onExperimentalAiEnabledChange: (enabled: boolean) => void
  onExperimentalAiModelChange: (model: string) => void
  onExperimentalAiTimeoutSecChange: (timeoutSec: AiTimeoutSec) => void
  isLocalAiSetupOpen: boolean
  onOpenLocalAiSetup: () => void
  onCloseLocalAiSetup: () => void
  onRecheckSystem: () => void | Promise<void>
  onRecheckLocalAi: () => void | Promise<void>
  onOpenLocalAiDownloadPage: () => void | Promise<void>
  onPullLocalAiModel: () => void | Promise<void>
  isPullingLocalAiModel: boolean
  localAiInstallLine: string
  localAiInstallResult: LocalAiInstallResult | null
  localAiSetupError: string
  hasLatestSession: boolean
  onReturnToLatestSession: () => void
  sessionHistory: SessionHistoryEntry[]
  onOpenSession: (sessionPath: string) => void
}

type ScanMode = 'fast' | 'balanced' | 'quality'
type AiTimeoutSec = 60 | 120 | 180

const aiModelOptions = ['qwen2.5vl:7b', 'llava:7b'] as const
const aiTimeoutOptions: AiTimeoutSec[] = [60, 120, 180]

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

function formatEta(
  progress: BackendProgress | null,
  fallbackElapsedSec: number,
) {
  const eta = progress?.eta_sec

  if (typeof eta === 'number' && !Number.isNaN(eta)) {
    return formatEtaSeconds(eta)
  }

  if (fallbackElapsedSec < 4) {
    return 'Starting...'
  }
  return 'Measuring...'
}

function displayProgressPercent(
  progress: BackendProgress | null,
  percent: number | null,
  stageIndex: number,
  isAnalyzing: boolean,
) {
  if (percent !== null) {
    return percent
  }

  if (typeof progress?.current === 'number' && typeof progress?.total === 'number' && progress.total > 0) {
    return Math.max(2, Math.min(96, (progress.current / progress.total) * 100))
  }

  if (!isAnalyzing) {
    return null
  }

  return stageIndex >= 0 ? Math.max(2, (stageIndex / scanStages.length) * 100) : 2
}

function estimatedActiveStageIndex(
  progress: BackendProgress | null,
  isAnalyzing: boolean,
) {
  const reportedIndex = activeStageIndex(progress?.stage)
  return isAnalyzing && reportedIndex < 0 ? 0 : reportedIndex
}

function stageStatus(index: number, currentIndex: number, scanState: ScanRunState) {
  if (scanState === 'running' && currentIndex < 0) {
    return index === 0 ? 'active' : 'idle'
  }

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
      <span aria-hidden="true" className="grid h-5 w-5 place-items-center rounded-full bg-white/14 text-[12px] font-semibold text-[var(--mimir-text)]">
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--mimir-accent)]" />
      </span>
    )
  }

  if (status === 'active') {
    return (
      <span aria-hidden="true" className="relative grid h-7 w-7 shrink-0 place-items-center rounded-full">
        <span className="absolute inset-0 rounded-full bg-[var(--mimir-accent-soft)] opacity-70 animate-ping" />
        <span className="absolute inset-1 rounded-full border border-white/10" />
        <span className="relative h-6 w-6 animate-spin rounded-full border-2 border-[rgba(157,183,170,0.18)] border-r-[var(--mimir-accent)] border-t-[var(--mimir-accent)] shadow-[0_0_18px_rgba(157,183,170,0.22)]" />
        <span className="absolute h-2 w-2 rounded-full bg-[var(--mimir-accent)] shadow-[0_0_12px_rgba(157,183,170,0.6)]" />
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

  return <span className="h-5 w-5 shrink-0 rounded-full border border-white/16 bg-black/20" />
}

function failedSystemChecks(systemCheck: SystemCheckResult | null) {
  return systemCheck?.items.filter(item => !item.ok && item.id !== 'enhanced_ai_review') ?? []
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
    return null
  }

  return (
    <div className="flex h-10 items-center gap-2 rounded-full border border-[rgba(173,139,85,0.24)] bg-[rgba(173,139,85,0.10)] px-4 text-[13px] text-amber-100/88">
      <span className="h-2 w-2 rounded-full bg-amber-200/80" />
      Setup needed
    </div>
  )
}

function ExperimentalAiSettings({
  enabled,
  model,
  timeoutSec,
  onEnabledChange,
  onModelChange,
  onTimeoutSecChange,
}: {
  enabled: boolean
  model: string
  timeoutSec: AiTimeoutSec
  onEnabledChange: (enabled: boolean) => void
  onModelChange: (model: string) => void
  onTimeoutSecChange: (timeoutSec: AiTimeoutSec) => void
}) {
  const modelPreset = aiModelOptions.includes(model as typeof aiModelOptions[number]) ? model : 'custom'

  return (
    <div className="mt-4 rounded-lg border border-white/[0.055] bg-black/16 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[14px] font-semibold text-[var(--mimir-text)]">
            Use local AI second opinion
          </div>
          <p className="mt-1 max-w-[560px] text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
            Optional. Uses a local vision model to review selected clips. Mimir still keeps the final safety rules.
          </p>
        </div>

        <label className="inline-flex cursor-pointer items-center gap-3 rounded-full border border-white/[0.08] bg-black/20 px-3 py-2 text-[12px] font-medium text-[var(--mimir-text-muted)]">
          <span>{enabled ? 'On' : 'Off'}</span>
          <input
            type="checkbox"
            className="sr-only"
            checked={enabled}
            onChange={event => onEnabledChange(event.currentTarget.checked)}
          />
          <span
            className={`relative h-5 w-9 rounded-full transition ${
              enabled ? 'bg-sky-300/45' : 'bg-white/[0.14]'
            }`}
          >
            <span
              className={`absolute top-1 h-3 w-3 rounded-full bg-white transition ${
                enabled ? 'left-5' : 'left-1'
              }`}
            />
          </span>
        </label>
      </div>

      {enabled && (
        <div className="mt-4 grid gap-3">
          <div className="rounded-lg border border-amber-200/14 bg-amber-300/8 p-3 text-[12px] leading-5 text-amber-50/78">
            Experimental: current local AI models may misread impact/contact clips. AI does not override Mimir's safety rules.
          </div>

          <details className="rounded-lg border border-white/[0.055] bg-white/[0.014] p-3">
            <summary className="cursor-pointer text-[12px] font-semibold text-[var(--mimir-text-muted)]">
              Advanced
            </summary>

            <div className="mt-4 grid gap-4">
            <label className="grid gap-2">
              <span className="text-[12px] font-medium text-[var(--mimir-text)]">Model</span>
              <select
                value={modelPreset}
                onChange={event => {
                  const value = event.currentTarget.value
                  onModelChange(value === 'custom' ? '' : value)
                }}
                className="h-10 rounded-lg border border-white/[0.08] bg-black/28 px-3 text-[13px] text-[var(--mimir-text)] outline-none transition focus:border-white/24"
              >
                {aiModelOptions.map(option => (
                  <option key={option} value={option}>{option}</option>
                ))}
                <option value="custom">Custom...</option>
              </select>
            </label>

            {modelPreset === 'custom' && (
              <label className="grid gap-2">
                <span className="text-[12px] font-medium text-[var(--mimir-text)]">Custom model name</span>
                <input
                  value={model}
                  onChange={event => onModelChange(event.currentTarget.value)}
                  placeholder="example: qwen2.5vl:7b"
                  className="h-10 rounded-lg border border-white/[0.08] bg-black/28 px-3 text-[13px] text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:border-white/24"
                />
              </label>
            )}

            <label className="grid gap-2">
              <span className="text-[12px] font-medium text-[var(--mimir-text)]">Timeout</span>
              <select
                value={timeoutSec}
                onChange={event => onTimeoutSecChange(Number(event.currentTarget.value) as AiTimeoutSec)}
                className="h-10 rounded-lg border border-white/[0.08] bg-black/28 px-3 text-[13px] text-[var(--mimir-text)] outline-none transition focus:border-white/24"
              >
                {aiTimeoutOptions.map(option => (
                  <option key={option} value={option}>{option} seconds</option>
                ))}
              </select>
            </label>
            </div>
          </details>
        </div>
      )}
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
  onRecheckSystem,
  onRecheckLocalAi,
  selectedVisionModel,
  localAiSetupError,
}: {
  systemCheck: SystemCheckResult | null
  isCheckingSystem: boolean
  localAiStatus: LocalAiStatus | null
  isCheckingLocalAi: boolean
  onOpenLocalAiSetup: () => void
  onRecheckSystem: () => void | Promise<void>
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
    const failedItems = systemCheck.items.filter(item => !item.ok && item.id !== 'enhanced_ai_review')
    const backendMissing = failedItems.some(item => item.id === 'local_scanner' || item.id === 'local_actions')
    const libraryIssue = failedItems.some(item => item.id === 'mimir_library_folder')
    const outputIssue = failedItems.some(item => item.id === 'core_v2_output_folder')
    const title = libraryIssue && !backendMissing && !outputIssue
      ? 'Mimir Library is not ready.'
      : 'Mimir setup is incomplete.'
    const description = backendMissing
      ? 'Mimir cannot find the local scanner.'
      : libraryIssue
        ? 'Mimir cannot access the local library folder.'
        : outputIssue
          ? 'Mimir cannot prepare the scan output folder.'
          : 'Mimir needs setup attention before scanning.'
    const details = failedItems
      .filter(item => !item.ok && item.id !== 'enhanced_ai_review')
      .map(item => `${item.label}: ${item.technical_details || item.message}`)
      .join('\n\n')

    return (
      <div className="mb-4 rounded-lg border border-red-300/20 bg-red-500/10 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[15px] font-semibold text-red-50">{title}</div>
            <p className="mt-2 text-[13px] leading-6 text-red-100/78">
              {description}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onRecheckSystem}
              className="rounded-lg border border-red-100/16 bg-black/18 px-4 py-2.5 text-[13px] font-semibold text-red-50 transition hover:bg-white/[0.045]"
            >
              Recheck
            </button>
            {details && (
              <button
                type="button"
                onClick={() => setShowDetails(value => !value)}
                className="rounded-lg border border-red-100/16 bg-black/18 px-4 py-2.5 text-[13px] font-semibold text-red-100/80 transition hover:bg-white/[0.045]"
              >
                Show details
              </button>
            )}
          </div>
        </div>
        {details && showDetails && (
          <div className="mt-3 rounded-md border border-red-200/14 bg-black/22 p-3">
            <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-red-50/70">
              {details}
            </pre>
          </div>
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
  onCancelScan,
  isCancellingScan,
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
  experimentalAiEnabled,
  experimentalAiModel,
  experimentalAiTimeoutSec,
  onExperimentalAiEnabledChange,
  onExperimentalAiModelChange,
  onExperimentalAiTimeoutSecChange,
  isLocalAiSetupOpen,
  onOpenLocalAiSetup,
  onCloseLocalAiSetup,
  onRecheckSystem,
  onRecheckLocalAi,
  onOpenLocalAiDownloadPage,
  onPullLocalAiModel,
  isPullingLocalAiModel,
  localAiInstallLine,
  localAiInstallResult,
  localAiSetupError,
  hasLatestSession,
  onReturnToLatestSession,
  sessionHistory,
  onOpenSession,
}: ImportPanelProps) {
  const hasSelectedFolder = selectedFolder.length > 0
  const isAnalyzing = scanState === 'running'
  const isWorking = isAnalyzing || loadState === 'loading'
  const hasTechnicalDetails = Boolean(scanOutput?.stdout || scanOutput?.stderr)
  const [fallbackElapsedSec, setFallbackElapsedSec] = useState(0)
  const currentStageIndex = estimatedActiveStageIndex(scanProgress, isAnalyzing)
  const percent = clampPercent(scanProgress?.percent)
  const displayPercent = displayProgressPercent(
    scanProgress,
    percent,
    currentStageIndex,
    isAnalyzing,
  )
  const elapsedText = formatElapsed(scanProgress?.elapsed_sec ?? fallbackElapsedSec)
  const etaText = scanState === 'error'
    ? 'Stopped'
    : formatEta(scanProgress, fallbackElapsedSec)
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
    if (!isAnalyzing) {
      setFallbackElapsedSec(0)
      return
    }

    const startedAt = Date.now()
    setFallbackElapsedSec(0)

    const timer = window.setInterval(() => {
      setFallbackElapsedSec(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    }, 1000)

    return () => {
      window.clearInterval(timer)
    }
  }, [isAnalyzing])

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
    <main className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1480px] flex-col overflow-hidden rounded-2xl border border-white/[0.065] bg-[radial-gradient(circle_at_48%_-10%,rgba(157,183,170,0.10),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.038),rgba(255,255,255,0.012)),var(--mimir-bg-depth)] shadow-[0_34px_110px_rgba(0,0,0,0.56)] sm:min-h-[calc(100vh-48px)]">
      <header className="flex min-h-[76px] shrink-0 items-center justify-between px-5 sm:px-7">
        <img src={mimirLockup} alt="Mimir" className="h-8 w-auto opacity-95" />
        <SystemStatusPill
          systemCheck={systemCheck}
          isCheckingSystem={isCheckingSystem}
          localAiStatus={localAiStatus}
          isCheckingLocalAi={isCheckingLocalAi}
        />
      </header>

      <section className="grid flex-1 gap-8 px-5 pb-8 pt-5 sm:px-8 lg:grid-cols-[minmax(0,0.86fr)_minmax(430px,1.14fr)] lg:items-center lg:px-12 xl:px-16">
        <div className="max-w-[650px]">
          <div className="mb-5 inline-flex rounded-full border border-[rgba(157,183,170,0.15)] bg-[var(--mimir-accent-soft)] px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
            Local incident review
          </div>
          <h1 className="max-w-[680px] text-[44px] font-semibold leading-[1.02] text-[var(--mimir-text)] sm:text-[58px] xl:text-[68px]">
            Find the moments worth watching.
          </h1>
          <p className="mt-5 max-w-[520px] text-[17px] leading-8 text-[var(--mimir-text-muted)]">
            Drop in a TeslaCam folder or footage folder. Mimir scans locally and helps you review suspicious moments.
          </p>
          <div className="mt-8 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">
            Private Beta - {MIMIR_VERSION}
          </div>
        </div>

        <div className="flex min-h-[540px] flex-col rounded-2xl border border-white/[0.055] bg-[linear-gradient(180deg,rgba(255,255,255,0.042),rgba(255,255,255,0.016))] p-4 shadow-[0_28px_86px_rgba(0,0,0,0.36)] sm:p-5">
          {!showScanStatus && (
            <>
              {hasLatestSession && (
                <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-white/[0.055] bg-black/18 px-4 py-3">
                  <div>
                    <div className="text-[13px] font-semibold text-[var(--mimir-text)]">Latest session is still open</div>
                    <div className="mt-1 text-[12px] text-[var(--mimir-text-subtle)]">
                      Return to your current review if you opened import by accident.
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={onReturnToLatestSession}
                    className="h-9 shrink-0 rounded-full border border-white/[0.08] bg-white/[0.045] px-4 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.08]"
                  >
                    Back to latest session
                  </button>
                </div>
              )}

              {sessionHistory.length > 1 && (
                <details className="mb-4 rounded-xl border border-white/[0.05] bg-black/12 px-4 py-3">
                  <summary className="cursor-pointer text-[12px] font-semibold text-[var(--mimir-text-muted)]">
                    Recent sessions
                  </summary>
                  <div className="mt-3 grid gap-2">
                    {sessionHistory.slice(0, 5).map(session => (
                      <button
                        key={session.session_path}
                        type="button"
                        onClick={() => onOpenSession(session.session_path)}
                        className="flex min-h-10 items-center justify-between gap-3 rounded-lg px-3 text-left transition hover:bg-white/[0.045]"
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-[12px] font-medium text-[var(--mimir-text)]">
                            {session.source_name || 'Previous scan'}
                          </span>
                          <span className="mt-0.5 block text-[11px] text-[var(--mimir-text-subtle)]">
                            {session.incidents} incidents
                          </span>
                        </span>
                        <span className="shrink-0 text-[11px] text-[var(--mimir-text-subtle)]">
                          {session.created_at ? new Date(session.created_at).toLocaleString() : ''}
                        </span>
                      </button>
                    ))}
                  </div>
                </details>
              )}

              <ReadinessPanel
                systemCheck={systemCheck}
                isCheckingSystem={isCheckingSystem}
                localAiStatus={localAiStatus}
                isCheckingLocalAi={isCheckingLocalAi}
                onOpenLocalAiSetup={onOpenLocalAiSetup}
                onRecheckSystem={onRecheckSystem}
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
                className={`group flex min-h-[268px] flex-1 items-center justify-center rounded-2xl border border-dashed px-6 text-center transition disabled:cursor-wait disabled:opacity-70 ${
                  isDraggingFolder
                    ? 'border-[rgba(157,183,170,0.46)] bg-[var(--mimir-accent-soft)]'
                    : 'border-[var(--mimir-border-strong)] bg-black/22 hover:border-[rgba(157,183,170,0.28)] hover:bg-white/[0.032]'
                }`}
              >
                <div className="max-w-[520px]">
                  <div className="mx-auto mb-7 grid h-16 w-16 place-items-center rounded-full border border-[rgba(157,183,170,0.16)] bg-[var(--mimir-accent-soft)] text-[30px] font-light text-[var(--mimir-text)] transition group-hover:bg-[rgba(157,183,170,0.18)]">
                    +
                  </div>
                  <div className="text-[22px] font-semibold text-[var(--mimir-text)]">
                    Drop footage folder or USB here
                  </div>
                  <div className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
                    TeslaCam folders and regular MP4 folders both work.
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
                  className="h-11 rounded-full border border-white/[0.065] bg-white/[0.05] px-5 text-[13px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.085] disabled:cursor-wait disabled:opacity-60"
                >
                  Choose footage folder
                </button>
              </div>

              {hasSelectedFolder && (
                <div className="mt-4 rounded-2xl bg-white/[0.032] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
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
                        className="h-12 rounded-full bg-[var(--mimir-text)] px-7 text-[15px] font-semibold text-black shadow-[0_16px_38px_rgba(255,255,255,0.075)] transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/12 disabled:text-white/35 disabled:shadow-none"
                      >
                        Analyze footage
                      </button>
                    </div>
                  </div>
                </div>
              )}

              <ExperimentalAiSettings
                enabled={experimentalAiEnabled}
                model={experimentalAiModel}
                timeoutSec={experimentalAiTimeoutSec}
                onEnabledChange={onExperimentalAiEnabledChange}
                onModelChange={onExperimentalAiModelChange}
                onTimeoutSecChange={onExperimentalAiTimeoutSecChange}
              />
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
                <div className="flex items-center gap-2">
                  <div className="inline-flex rounded-full border border-[var(--mimir-border)] bg-black/18 px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
                    {formatScanMode(scanMode)}
                  </div>
                  {isAnalyzing && (
                    <button
                      type="button"
                      onClick={onCancelScan}
                      disabled={isCancellingScan}
                      className="h-8 rounded-full border border-white/[0.08] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] disabled:opacity-50"
                    >
                      {isCancellingScan ? 'Stopping...' : 'Cancel scan'}
                    </button>
                  )}
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
                  {displayPercent === null ? (
                    <div className="h-full w-1/3 animate-[mimir-progress-pulse_1.4s_ease-in-out_infinite] rounded-full bg-white/55" />
                  ) : (
                    <div
                      className="h-full rounded-full bg-[var(--mimir-text)] transition-all duration-300"
                      style={{ width: `${displayPercent}%` }}
                    />
                  )}
                </div>

                <div className="mt-3 flex flex-wrap gap-2 text-[12px] text-[var(--mimir-text-subtle)]">
                  {displayPercent !== null && <span>{Math.round(displayPercent)}%</span>}
                  {typeof scanProgress?.incidents_created === 'number' && (
                    <span>{scanProgress.incidents_created} incidents found</span>
                  )}
                  {typeof scanProgress?.ai_calls === 'number' && <span>{scanProgress.ai_calls} AI checks</span>}
                </div>
              </div>

              <div className="mt-4 grid gap-2" role="list" aria-label="Scan progress">
                {scanStages.map((stage, index) => {
                  const status = stageStatus(index, currentStageIndex, scanState)

                  return (
                  <div
                    key={stage.label}
                    role="listitem"
                    aria-current={status === 'active' ? 'step' : undefined}
                    className={`flex items-center gap-3 rounded-md border px-3 py-2 text-[13px] transition ${
                      status === 'active'
                        ? 'border-[rgba(157,183,170,0.24)] bg-[var(--mimir-accent-soft)] text-[var(--mimir-text)] shadow-[0_0_28px_rgba(157,183,170,0.06)]'
                        : status === 'complete'
                          ? 'border-[var(--mimir-border)] bg-white/[0.025] text-[var(--mimir-text)]'
                          : status === 'error'
                            ? 'border-red-300/20 bg-red-500/10 text-red-100'
                            : 'border-[var(--mimir-border)] bg-black/18 text-[var(--mimir-text-subtle)]'
                    }`}
                  >
                    <StageIcon status={status} />
                    <span>{stage.label}</span>
                    <span className="sr-only">
                      {status === 'active' ? 'in progress' : status === 'complete' ? 'complete' : status === 'error' ? 'failed' : 'pending'}
                    </span>
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
