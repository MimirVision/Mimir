import { useEffect, useState } from 'react'
import type { BackendProgress, ScanMode, ScanRunState } from '../types'

export interface ScanOutput {
  stdout: string
  stderr: string
}

interface ActiveScanStatusProps {
  scanState: ScanRunState
  scanMode: ScanMode
  scanProgress: BackendProgress | null
  scanError: string
  scanOutput: ScanOutput | null
  lastProgressMessage: string
  isCancellingScan: boolean
  onCancelScan: () => void
}

export const scanStages = [
  { label: 'Reading clips', keys: ['initializing', 'reading_clips', 'preparing_clips', 'discovering_clips'] },
  { label: 'Reading event metadata', keys: ['reading_event_metadata', 'event_metadata'] },
  { label: 'Grouping camera angles', keys: ['grouping_camera_angles', 'camera_grouping'] },
  // Only reported when the scan is importing. It interleaves with detection
  // rather than preceding it -- each group is copied, then scanned, then
  // cleared -- so the two stages alternate for the whole run and the bar
  // moves through both.
  { label: 'Copying to this PC', keys: ['copying_footage'] },
  { label: 'Detecting activity', keys: ['detecting_activity', 'scanning_video', 'scanning', 'yolo'] },
  { label: 'Reviewing suspicious moments', keys: ['reviewing_suspicious_moments', 'ai_review', 'reviewing_event', 'ai_enrichment'] },
  { label: 'Building incident timeline', keys: ['building_incident_timeline', 'building_timeline', 'finalizing_event'] },
  { label: 'Writing results', keys: ['writing_output', 'writing_results', 'writing_session'] },
]

export function formatStage(value?: string) {
  if (!value) return 'Starting scan...'
  const normalizedStage = value.toLowerCase()
  if (normalizedStage.includes('local_results_ready')) return 'Scan complete'
  if (normalizedStage.includes('complete')) return 'Scan complete'
  if (normalizedStage.includes('error')) return 'Scan error'
  const mappedStage = scanStages.find(item => item.keys.some(key => normalizedStage.includes(key)))
  if (mappedStage) return mappedStage.label
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatClock(seconds: number) {
  const safeSeconds = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  const remainingSeconds = safeSeconds % 60
  return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`
}

export function activeStageIndex(stage?: string) {
  if (!stage) return -1
  const normalizedStage = stage.toLowerCase()
  if (normalizedStage.includes('local_results_ready')) return scanStages.length
  if (normalizedStage.includes('complete')) return scanStages.length
  if (normalizedStage.includes('error')) return 0
  const index = scanStages.findIndex(item => item.keys.some(key => normalizedStage.includes(key)))
  return index >= 0 ? index : 0
}

export function stageStatus(index: number, currentIndex: number, scanState: ScanRunState) {
  if (scanState === 'running' && currentIndex < 0) return index === 0 ? 'active' : 'idle'
  if (scanState === 'error' && index === Math.max(0, currentIndex)) return 'error'
  if (currentIndex >= scanStages.length || index < currentIndex) return 'complete'
  if (index === currentIndex) return 'active'
  return 'idle'
}

function formatScanMode(value: ScanMode) {
  if (value === 'quality') return 'Thorough scan'
  return `${value.charAt(0).toUpperCase()}${value.slice(1)} scan`
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
        <span className="absolute inset-0 animate-ping rounded-full bg-[var(--mimir-accent-soft)] opacity-70" />
        <span className="absolute inset-1 rounded-full border border-white/10" />
        <span className="relative h-6 w-6 animate-spin rounded-full border-2 border-[rgba(157,183,170,0.18)] border-r-[var(--mimir-accent)] border-t-[var(--mimir-accent)] shadow-[0_0_18px_rgba(157,183,170,0.22)]" />
        <span className="absolute h-2 w-2 rounded-full bg-[var(--mimir-accent)] shadow-[0_0_12px_rgba(157,183,170,0.6)]" />
      </span>
    )
  }
  if (status === 'error') {
    return <span className="grid h-5 w-5 place-items-center rounded-full border border-red-200/30 bg-red-500/12 text-[12px] font-semibold text-red-100">!</span>
  }
  return <span className="h-5 w-5 shrink-0 rounded-full border border-white/16 bg-black/20" />
}

export function ActiveScanStatus({
  scanState,
  scanMode,
  scanProgress,
  scanError,
  scanOutput,
  lastProgressMessage,
  isCancellingScan,
  onCancelScan,
}: ActiveScanStatusProps) {
  const isAnalyzing = scanState === 'running'
  const [fallbackElapsedSec, setFallbackElapsedSec] = useState(0)
  const [showBackendLoadHelper, setShowBackendLoadHelper] = useState(false)

  useEffect(() => {
    if (!isAnalyzing) {
      setFallbackElapsedSec(0)
      return
    }
    const startedAt = Date.now()
    const timer = window.setInterval(() => {
      setFallbackElapsedSec(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isAnalyzing])

  useEffect(() => {
    if (!isAnalyzing || scanProgress) {
      setShowBackendLoadHelper(false)
      return
    }
    const timer = window.setTimeout(() => setShowBackendLoadHelper(true), 10_000)
    return () => window.clearTimeout(timer)
  }, [isAnalyzing, scanProgress])

  const currentStageIndex = isAnalyzing && activeStageIndex(scanProgress?.stage) < 0
    ? 0
    : activeStageIndex(scanProgress?.stage)
  const reportedPercent = typeof scanProgress?.percent === 'number' && !Number.isNaN(scanProgress.percent)
    ? Math.max(0, Math.min(100, scanProgress.percent))
    : null
  const displayPercent = reportedPercent ?? (
    typeof scanProgress?.current === 'number' && typeof scanProgress?.total === 'number' && scanProgress.total > 0
      ? Math.max(2, Math.min(96, (scanProgress.current / scanProgress.total) * 100))
      : isAnalyzing
        ? Math.max(2, (Math.max(0, currentStageIndex) / scanStages.length) * 100)
        : null
  )
  const elapsedSec = scanProgress?.elapsed_sec ?? fallbackElapsedSec
  const elapsedText = Number.isFinite(elapsedSec) ? `Elapsed: ${formatClock(elapsedSec)}` : null
  const etaText = scanState === 'error'
    ? 'Stopped'
    : typeof scanProgress?.eta_sec === 'number' && Number.isFinite(scanProgress.eta_sec)
      ? scanProgress.eta_sec < 60
        ? `About ${Math.max(0, Math.round(scanProgress.eta_sec))} sec left`
        : `About ${Math.max(1, Math.round(scanProgress.eta_sec / 60))} min left`
      : null
  const progressMessage = scanProgress?.message || 'Starting local scanner...'
  const hasTechnicalDetails = Boolean(scanOutput?.stdout || scanOutput?.stderr)

  return (
    <div className="flex flex-1 flex-col justify-center rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-[14px] font-semibold text-[var(--mimir-text)]">
            {scanState === 'error' ? 'Scan stopped' : 'Analyzing footage locally...'}
          </div>
          <div className="mt-1 text-[13px] leading-6 text-[var(--mimir-text-muted)]">
            {scanState === 'error' ? 'Mimir could not finish the local scan.' : 'Processing stays on this device.'}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-full border border-[var(--mimir-border)] bg-black/18 px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
            {formatScanMode(scanMode)}
          </div>
          {isAnalyzing && (
            <button type="button" onClick={onCancelScan} disabled={isCancellingScan} className="h-8 rounded-full border border-white/[0.08] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] disabled:opacity-50">
              {isCancellingScan ? 'Stopping...' : 'Cancel scan'}
            </button>
          )}
        </div>
      </div>

      <div className="mt-5 rounded-lg bg-black/18 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">{formatStage(scanProgress?.stage)}</div>
            <div aria-live="polite" className="mt-2 text-[15px] font-medium text-[var(--mimir-text)]">{progressMessage}</div>
            {showBackendLoadHelper && <div className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-subtle)]">This can take a moment while the local scanner starts.</div>}
            {scanProgress?.current_video && <div className="mt-2 max-w-[520px] truncate text-[13px] text-[var(--mimir-text-muted)]">Current video: {scanProgress.current_video}</div>}
          </div>
          <div className="text-right text-[12px] leading-6 text-[var(--mimir-text-muted)]">
            {typeof scanProgress?.current === 'number' && typeof scanProgress?.total === 'number' && <div>Clips: {scanProgress.current} / {scanProgress.total}</div>}
            {elapsedText && <div>{elapsedText}</div>}
            {etaText && <div>ETA: {etaText}</div>}
          </div>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/[0.08]">
          {displayPercent === null ? (
            <div className="h-full w-1/3 animate-[mimir-progress-pulse_1.4s_ease-in-out_infinite] rounded-full bg-white/55" />
          ) : (
            <div className="h-full rounded-full bg-[var(--mimir-text)] transition-all duration-300" style={{ width: `${displayPercent}%` }} />
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[12px] text-[var(--mimir-text-subtle)]">
          {displayPercent !== null && <span>{Math.round(displayPercent)}%</span>}
          {typeof scanProgress?.incidents_created === 'number' && <span>{scanProgress.incidents_created} incidents found</span>}
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
              className={`flex items-center gap-3 rounded-md border px-3 py-2 text-[13px] transition ${status === 'active' ? 'border-[rgba(157,183,170,0.24)] bg-[var(--mimir-accent-soft)] text-[var(--mimir-text)] shadow-[0_0_28px_rgba(157,183,170,0.06)]' : status === 'complete' ? 'border-[var(--mimir-border)] bg-white/[0.025] text-[var(--mimir-text)]' : status === 'error' ? 'border-red-300/20 bg-red-500/10 text-red-100' : 'border-[var(--mimir-border)] bg-black/18 text-[var(--mimir-text-subtle)]'}`}
            >
              <StageIcon status={status} />
              <span>{stage.label}</span>
              <span className="sr-only">{status === 'active' ? 'in progress' : status === 'complete' ? 'complete' : status === 'error' ? 'failed' : 'pending'}</span>
            </div>
          )
        })}
      </div>

      {scanState === 'error' && (
        <div className="mt-4 rounded-lg border border-red-400/20 bg-red-500/10 p-4">
          <div className="text-[13px] leading-6 text-red-100">{scanError || 'Could not run the local scan.'}</div>
          {lastProgressMessage && <div className="mt-2 text-[12px] leading-5 text-red-100/70">Last progress update: {lastProgressMessage}</div>}
          {hasTechnicalDetails && (
            <details className="mt-3 rounded-lg border border-red-300/15 bg-black/25 p-3">
              <summary className="cursor-pointer text-[12px] font-medium text-red-100/85">Technical details</summary>
              <div className="mt-3 grid gap-3">
                {scanOutput?.stdout && <div><div className="mb-1 text-[11px] uppercase tracking-[0.16em] text-red-100/55">stdout</div><pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md bg-black/35 p-3 text-[11px] leading-5 text-red-50/75">{scanOutput.stdout}</pre></div>}
                {scanOutput?.stderr && <div><div className="mb-1 text-[11px] uppercase tracking-[0.16em] text-red-100/55">stderr</div><pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md bg-black/35 p-3 text-[11px] leading-5 text-red-50/75">{scanOutput.stderr}</pre></div>}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
