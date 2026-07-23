import { useCallback, useEffect, useRef, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { listen } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { BetaNoticeFooter, BetaPrivacyNotice } from './components/BetaPrivacyNotice'
import { CrashSafeBoundary } from './components/CrashSafeBoundary'
import { ImportPanel } from './components/ImportPanel'
import { IncidentLibraryView } from './components/IncidentLibraryView'
import { OnboardingFlow } from './components/OnboardingFlow'
import { FULL_AI_BETA, USE_MIMIR_CORE_V2 } from './config'
import type {
  AiTimeoutSec,
  BackendProgress,
  FrontendScanDiagnostics,
  LocalAiInstallResult,
  LocalAiStatus,
  MimirSession,
  ScanMode,
  ScanRunState,
  SessionHistoryEntry,
  SessionLoadState,
  SystemCheckResult,
} from './types'

const onboardingCompletedKey = 'mimir_onboarding_completed'
const defaultScanModeKey = 'mimir_default_scan_mode'
const betaNoticeAcceptedKey = 'mimir_beta_privacy_notice_accepted'
const selectedVisionModelKey = 'mimir_selected_vision_model'
const experimentalAiEnabledKey = 'experimental_ai_enabled'
const experimentalAiModelKey = 'experimental_ai_model'
const experimentalAiTimeoutSecKey = 'experimental_ai_timeout_sec'
const defaultVisionModel = 'qwen2.5vl:7b'
const defaultAiTimeoutSec = 60
const internalAiReviewBudget = 999

interface LocalScanResult {
  stdout: string
  stderr: string
  session_path?: string
  latest_session_path?: string
  latest_session_modified_time?: string
  active_output_dir?: string
  output_argument_used?: string
  backend_mode?: string
  backend_runner?: 'sidecar' | 'dev_exe' | 'python_script' | string
  backend_command?: string
}

interface LocalScanFailure {
  message: string
  stdout?: string
  stderr?: string
}

interface ScanOutput {
  stdout: string
  stderr: string
}

function appendBackendRunnerDetails(stderr: string, diagnostics?: FrontendScanDiagnostics | null) {
  if (!diagnostics) {
    return stderr
  }

  return [
    stderr,
    diagnostics.backend_runner ? `backend_runner: ${diagnostics.backend_runner}` : '',
    diagnostics.backend_command ? `backend_command: ${diagnostics.backend_command}` : '',
    diagnostics.output_argument_used ? `output_argument_used: ${diagnostics.output_argument_used}` : '',
    diagnostics.active_output_dir ? `active_output_dir: ${diagnostics.active_output_dir}` : '',
    diagnostics.latest_session_path ? `latest_session_path: ${diagnostics.latest_session_path}` : '',
  ].filter(Boolean).join('\n')
}

function diagnosticsFromScanResult(result: LocalScanResult): FrontendScanDiagnostics {
  return {
    backend_runner: result.backend_runner,
    backend_mode: result.backend_mode,
    backend_command: result.backend_command,
    output_argument_used: result.output_argument_used,
    active_output_dir: result.active_output_dir,
    latest_session_path: result.latest_session_path || result.session_path,
    latest_session_modified_time: result.latest_session_modified_time,
  }
}

interface ProgressLineEvent {
  line: string
}

interface LocalAiInstallLineEvent {
  line: string
}

type AppView = 'import' | 'library'

const progressPrefix = 'MIMIR_PROGRESS'

function isScanMode(value: string | null): value is ScanMode {
  return value === 'fast' || value === 'balanced' || value === 'quality'
}

function readStoredScanMode(): ScanMode {
  try {
    const value = window.localStorage.getItem(defaultScanModeKey)
    return isScanMode(value) ? value : 'balanced'
  } catch {
    return 'balanced'
  }
}

function readOnboardingCompleted() {
  try {
    return window.localStorage.getItem(onboardingCompletedKey) === 'true'
  } catch {
    return false
  }
}

function readBetaNoticeAccepted() {
  try {
    return window.localStorage.getItem(betaNoticeAcceptedKey) === 'true'
  } catch {
    return false
  }
}

function readSelectedVisionModel() {
  try {
    return window.localStorage.getItem(selectedVisionModelKey) || defaultVisionModel
  } catch {
    return defaultVisionModel
  }
}

function readStoredExperimentalAiEnabled() {
  try {
    return window.localStorage.getItem(experimentalAiEnabledKey) === 'true'
  } catch {
    return false
  }
}

function readStoredExperimentalAiModel() {
  try {
    return window.localStorage.getItem(experimentalAiModelKey) || defaultVisionModel
  } catch {
    return defaultVisionModel
  }
}

function readStoredNumberOption<T extends number>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = Number(window.localStorage.getItem(key))
    return allowed.includes(value as T) ? value as T : fallback
  } catch {
    return fallback
  }
}

function isLocalScanFailure(error: unknown): error is LocalScanFailure {
  return (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    typeof (error as LocalScanFailure).message === 'string'
  )
}

export default function App() {
  const [selectedFolder, setSelectedFolder] = useState('')
  const [isDraggingFolder, setIsDraggingFolder] = useState(false)
  const [latestSession, setLatestSession] = useState<MimirSession | null>(null)
  const [sessionLoadState, setSessionLoadState] = useState<SessionLoadState>('idle')
  const [scanState, setScanState] = useState<ScanRunState>('idle')
  const [scanError, setScanError] = useState('')
  const [scanOutput, setScanOutput] = useState<ScanOutput | null>(null)
  const [scanProgress, setScanProgress] = useState<BackendProgress | null>(null)
  const [lastProgressMessage, setLastProgressMessage] = useState('')
  const [clipCount, setClipCount] = useState<number | null>(null)
  const [isCountingClips, setIsCountingClips] = useState(false)
  const [appView, setAppView] = useState<AppView>('import')
  const [scanMode, setScanMode] = useState<ScanMode>(() => readStoredScanMode())
  const [showOnboarding, setShowOnboarding] = useState(() => !readOnboardingCompleted())
  const [hasAcceptedBetaNotice, setHasAcceptedBetaNotice] = useState(() => readBetaNoticeAccepted())
  const [isBetaNoticeOpen, setIsBetaNoticeOpen] = useState(false)
  const [systemCheck, setSystemCheck] = useState<SystemCheckResult | null>(null)
  const [isCheckingSystem, setIsCheckingSystem] = useState(true)
  const [localAiStatus, setLocalAiStatus] = useState<LocalAiStatus | null>(null)
  const [isCheckingLocalAi, setIsCheckingLocalAi] = useState(true)
  const [selectedVisionModel] = useState(() => readSelectedVisionModel())
  const [experimentalAiEnabled, setExperimentalAiEnabled] = useState(() => readStoredExperimentalAiEnabled())
  const [experimentalAiModel, setExperimentalAiModel] = useState(() => readStoredExperimentalAiModel())
  const [experimentalAiTimeoutSec, setExperimentalAiTimeoutSec] = useState<AiTimeoutSec>(() =>
    readStoredNumberOption(experimentalAiTimeoutSecKey, [60, 120, 180] as const, defaultAiTimeoutSec),
  )
  const [isLocalAiSetupOpen, setIsLocalAiSetupOpen] = useState(false)
  const [isPullingLocalAiModel, setIsPullingLocalAiModel] = useState(false)
  const [localAiInstallLine, setLocalAiInstallLine] = useState('')
  const [localAiInstallResult, setLocalAiInstallResult] = useState<LocalAiInstallResult | null>(null)
  const [localAiSetupError, setLocalAiSetupError] = useState('')
  const [scanDiagnostics, setScanDiagnostics] = useState<FrontendScanDiagnostics | null>(null)
  const [sessionHistory, setSessionHistory] = useState<SessionHistoryEntry[]>([])
  const [isCancellingScan, setIsCancellingScan] = useState(false)
  const [aiEnrichmentRefreshToken, setAiEnrichmentRefreshToken] = useState(0)
  const activeProgressSessionId = useRef('')
  const scanCancellationRequested = useRef(false)

  const selectFolder = async (folderPath: string) => {
    setSelectedFolder(folderPath)
    setClipCount(null)
    setIsCountingClips(true)

    try {
      const count = await invoke<number>('count_teslacam_clips', {
        selectedFolder: folderPath,
      })

      setClipCount(count)
    } catch {
      setClipCount(null)
    } finally {
      setIsCountingClips(false)
    }
  }

  useEffect(() => {
    let unlisten: (() => void) | undefined

    try {
      getCurrentWebview()
        .onDragDropEvent(event => {
          if (event.payload.type === 'over') {
            setIsDraggingFolder(true)
          }

          if (event.payload.type === 'leave') {
            setIsDraggingFolder(false)
          }

          if (event.payload.type === 'drop') {
            const [firstPath] = event.payload.paths

            if (firstPath) {
              void selectFolder(firstPath)
            }

            setIsDraggingFolder(false)
          }
        })
        .then(unlistenFn => {
          unlisten = unlistenFn
        })
        .catch(() => {
          // Browser-only accessibility checks do not expose a Tauri webview.
        })
    } catch {
      // Browser-only accessibility checks do not expose a Tauri webview.
    }

    return () => {
      unlisten?.()
    }
  }, [])

  const runSystemCheck = useCallback(async () => {
    setIsCheckingSystem(true)

    try {
      const result = await invoke<SystemCheckResult>('check_system_requirements')
      setSystemCheck(result)
      return result
    } catch (error) {
      const fallbackCheck = {
        ok: false,
        checked_at: '',
        items: [
          {
            id: 'system_check',
            label: 'System check',
            ok: false,
            message: 'System check could not run.',
            why_it_matters: 'Mimir checks local requirements before scanning so setup problems are easier to fix.',
            suggested_fix: 'Restart Mimir. If this continues, reinstall the app or check the local scanner setup.',
            technical_details: error instanceof Error ? error.message : String(error),
          },
        ],
      }
      setSystemCheck(fallbackCheck)
      return fallbackCheck
    } finally {
      setIsCheckingSystem(false)
    }
  }, [])

  useEffect(() => {
    void runSystemCheck()
  }, [runSystemCheck])

  const recheckLocalAi = useCallback(async () => {
    setIsCheckingLocalAi(true)
    setLocalAiSetupError('')

    try {
      const result = await invoke<LocalAiStatus>('check_local_ai', {
        selectedModel: selectedVisionModel,
      })

      setLocalAiStatus(result)
      return result
    } catch (error) {
      const fallbackStatus = {
        ok: false,
        ollama_available: false,
        model_installed: false,
        selected_model: selectedVisionModel,
        message: 'Local AI setup is not ready.',
        technical_details: error instanceof Error ? error.message : String(error),
      }
      setLocalAiStatus(fallbackStatus)
      return fallbackStatus
    } finally {
      setIsCheckingLocalAi(false)
    }
  }, [selectedVisionModel])

  useEffect(() => {
    void recheckLocalAi()
  }, [recheckLocalAi])

  useEffect(() => {
    let unlisten: (() => void) | undefined

    listen<ProgressLineEvent>('mimir-progress', event => {
      const line = String(event.payload.line ?? '').trimStart()

      if (!line.startsWith(progressPrefix)) {
        return
      }

      const jsonPayload = line.slice(progressPrefix.length).trim()

      try {
        const parsed = JSON.parse(jsonPayload) as BackendProgress

        if (parsed.session_id) {
          if (activeProgressSessionId.current && activeProgressSessionId.current !== parsed.session_id) {
            return
          }
          activeProgressSessionId.current = parsed.session_id
        }

        setScanProgress(previous => {
          const parsedStage = parsed.stage?.toLowerCase()

          if (
            parsedStage?.includes('error') &&
            previous?.stage &&
            !previous.stage.toLowerCase().includes('complete')
          ) {
            return {
              ...previous,
              ...parsed,
              stage: previous.stage,
            }
          }

          return parsed
        })

        if (parsed.message) {
          setLastProgressMessage(parsed.message)
        }
        if (parsed.phase === 'ai_enrichment' && parsed.stage === 'ai_enrichment_complete') {
          setAiEnrichmentRefreshToken(value => value + 1)
        }
      } catch {
        setLastProgressMessage(line)
      }
    }).then(unlistenFn => {
      unlisten = unlistenFn
    })

    return () => {
      unlisten?.()
    }
  }, [])

  useEffect(() => {
    let unlisten: (() => void) | undefined

    listen<LocalAiInstallLineEvent>('local-ai-install-output', event => {
      const line = String(event.payload.line ?? '').trim()

      if (line) {
        setLocalAiInstallLine(line)
      }
    }).then(unlistenFn => {
      unlisten = unlistenFn
    })

    return () => {
      unlisten?.()
    }
  }, [])

  useEffect(() => {
    if (!showOnboarding && !hasAcceptedBetaNotice) {
      setIsBetaNoticeOpen(true)
    }
  }, [hasAcceptedBetaNotice, showOnboarding])

  const chooseFolder = async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: 'Choose footage folder',
    })

    if (typeof selected === 'string') {
      await selectFolder(selected)
    }
  }

  const completeOnboarding = (defaultScanMode: ScanMode, chooseFolderAfter = false) => {
    setScanMode(defaultScanMode)
    setShowOnboarding(false)

    try {
      window.localStorage.setItem(onboardingCompletedKey, 'true')
      window.localStorage.setItem(defaultScanModeKey, defaultScanMode)
    } catch {
      // Local storage is a convenience for first-run setup; the app should still continue if unavailable.
    }

    if (chooseFolderAfter) {
      window.setTimeout(() => {
        void chooseFolder()
      }, 0)
    }
  }

  const loadLatestSession = useCallback(
    async (clearExisting = true, sessionPath?: string): Promise<MimirSession | null> => {
    setSessionLoadState('loading')

    if (clearExisting) {
      setLatestSession(null)
    }

    try {
      const contents = await invoke<string>('load_latest_session_json', {
        sessionPath: sessionPath || scanDiagnostics?.latest_session_path || null,
      })

      try {
        const parsed = JSON.parse(contents) as MimirSession

        if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.incidents)) {
          throw new Error('Invalid Mimir session shape')
        }

        if (USE_MIMIR_CORE_V2 && parsed.schema_version !== 'mimir_v2') {
          throw new Error('Invalid Mimir Core v2 session schema')
        }

        setLatestSession(parsed)
        setSessionLoadState('loaded')
        return parsed
      } catch {
        setSessionLoadState('error')
        return null
      }
    } catch {
        setSessionLoadState('missing')
        return null
      }
    },
    [scanDiagnostics?.latest_session_path],
  )

  const refreshSessionHistory = useCallback(async () => {
    try {
      const history = await invoke<SessionHistoryEntry[]>('list_session_history')
      setSessionHistory(history)
      return history
    } catch {
      setSessionHistory([])
      return []
    }
  }, [])

  useEffect(() => {
    if (aiEnrichmentRefreshToken > 0) {
      void loadLatestSession(false)
      void refreshSessionHistory()
    }
  }, [aiEnrichmentRefreshToken, loadLatestSession, refreshSessionHistory])

  useEffect(() => {
    if (showOnboarding) {
      return
    }
    void refreshSessionHistory()
    if (!latestSession && sessionLoadState === 'idle') {
      void loadLatestSession(false)
    }
  }, [latestSession, loadLatestSession, refreshSessionHistory, sessionLoadState, showOnboarding])

  const runScan = async (useEnhancedAi: boolean) => {
    if (!selectedFolder || scanState === 'running') {
      return
    }

    setScanState('running')
    setScanError('')
    setScanOutput(null)
    setScanProgress(null)
    activeProgressSessionId.current = ''
    setLastProgressMessage('')
    setIsCancellingScan(false)
    scanCancellationRequested.current = false
    setScanDiagnostics(null)
    setSessionLoadState('loading')

    const effectiveVisionModel = experimentalAiModel.trim() || defaultVisionModel

    try {
      const result = await invoke<LocalScanResult>('run_local_scan', {
        selectedFolder,
        scanMode,
        useEnhancedAi,
        visionModel: useEnhancedAi ? effectiveVisionModel : selectedVisionModel,
        aiReviewBudget: useEnhancedAi ? internalAiReviewBudget : undefined,
        aiTimeoutSec: useEnhancedAi ? experimentalAiTimeoutSec : undefined,
      })
      const diagnostics = diagnosticsFromScanResult(result)
      setScanDiagnostics(diagnostics)

      setScanOutput({
        stdout: result.stdout,
        stderr: appendBackendRunnerDetails(result.stderr, diagnostics),
      })

      const loadedSession = await loadLatestSession(false, diagnostics.latest_session_path)
      const unsupportedSource = loadedSession?.source_report?.is_supported === false

      setScanState(loadedSession && !unsupportedSource ? 'complete' : 'error')

      if (!loadedSession) {
        setScanError('Mimir finished, but no scan result was found.')
        setScanOutput({
          stdout: result.stdout,
          stderr: [
            appendBackendRunnerDetails(result.stderr, diagnostics),
            diagnostics.latest_session_path ? `Expected result path: ${diagnostics.latest_session_path}` : '',
          ].filter(Boolean).join('\n'),
        })
      } else if (unsupportedSource) {
        setScanError(
          'No footage found. Try selecting the USB drive itself, the TeslaCam folder, or a folder containing MP4 clips.',
        )
        setScanOutput({
          stdout: result.stdout,
          stderr: appendBackendRunnerDetails(result.stderr, diagnostics),
        })
      } else {
        setScanProgress(null)
        void refreshSessionHistory()
        setAppView('library')
      }
    } catch (error) {
      setSessionLoadState('idle')
      if (scanCancellationRequested.current) {
        setScanState('idle')
        setScanError('')
        setScanOutput(null)
        setScanProgress(null)
        setLastProgressMessage('Scan canceled.')
        setIsCancellingScan(false)
        return
      }
      setScanState('error')

      if (isLocalScanFailure(error)) {
        setScanError(error.message)
        setScanOutput({
          stdout: error.stdout ?? '',
          stderr: error.stderr ?? '',
        })
      } else {
        setScanError(error instanceof Error ? error.message : String(error))
        setScanOutput(null)
      }
    }
  }

  const cancelScan = async () => {
    if (scanState !== 'running' || isCancellingScan) {
      return
    }
    scanCancellationRequested.current = true
    setIsCancellingScan(true)
    setLastProgressMessage('Stopping scan safely...')
    try {
      await invoke<boolean>('cancel_local_scan')
    } catch (error) {
      scanCancellationRequested.current = false
      setIsCancellingScan(false)
      setScanError(error instanceof Error ? error.message : String(error))
    }
  }

  const analyzeSelectedFolder = async () => {
    if (!selectedFolder || scanState === 'running') {
      return
    }

    if (FULL_AI_BETA && !USE_MIMIR_CORE_V2 && localAiStatus?.ok !== true) {
      setScanState('error')
      setScanError('Mimir needs to finish setup before scanning. AI review is not ready.')
      setSessionLoadState('idle')
      return
    }

    await runScan(USE_MIMIR_CORE_V2 ? experimentalAiEnabled : FULL_AI_BETA ? true : localAiStatus?.ok === true)
  }

  const pullLocalAiModel = async () => {
    if (isPullingLocalAiModel) {
      return
    }

    setIsPullingLocalAiModel(true)
    setLocalAiInstallLine('')
    setLocalAiInstallResult(null)
    setLocalAiSetupError('')

    try {
      const result = await invoke<LocalAiInstallResult>('pull_local_ai_model', {
        selectedModel: selectedVisionModel,
      })

      setLocalAiInstallResult(result)

      if (!result.ok) {
        setLocalAiSetupError(result.message || 'Local AI setup could not finish.')
      }

      const latestStatus = await recheckLocalAi()

      if (result.ok && latestStatus.ok) {
        setIsLocalAiSetupOpen(false)
      }
    } catch (error) {
      setLocalAiSetupError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsPullingLocalAiModel(false)
    }
  }

  const openLocalAiDownloadPage = async () => {
    try {
      await invoke('open_local_ai_download_page')
    } catch (error) {
      setLocalAiSetupError(error instanceof Error ? error.message : String(error))
    }
  }

  const returnToImport = () => {
    setAppView('import')
    setScanState('idle')
    setSessionLoadState('idle')
    setScanError('')
    setScanOutput(null)
    setScanProgress(null)
    setLastProgressMessage('')
  }

  const returnToLatestSession = () => {
    if (!latestSession) {
      return
    }

    setAppView('library')
    setScanState('complete')
    setSessionLoadState('loaded')
    setScanError('')
    setScanOutput(null)
    setScanProgress(null)
    setLastProgressMessage('')
  }

  const openSessionFromHistory = async (sessionPath: string) => {
    const loaded = await loadLatestSession(false, sessionPath)
    if (loaded) {
      setAppView('library')
      setScanState('complete')
      setScanError('')
    }
  }

  const updateScanMode = (mode: ScanMode) => {
    setScanMode(mode)

    try {
      window.localStorage.setItem(defaultScanModeKey, mode)
    } catch {
      // Persisting the default is optional; scanning still works without local storage.
    }
  }

  const updateExperimentalAiEnabled = (enabled: boolean) => {
    setExperimentalAiEnabled(enabled)

    try {
      window.localStorage.setItem(experimentalAiEnabledKey, enabled ? 'true' : 'false')
    } catch {
      // Local AI settings are optional; scans still default to local-only if persistence is unavailable.
    }
  }

  const updateExperimentalAiModel = (model: string) => {
    setExperimentalAiModel(model)

    try {
      window.localStorage.setItem(experimentalAiModelKey, model)
    } catch {
      // Optional preference.
    }
  }

  const updateExperimentalAiTimeoutSec = (timeoutSec: AiTimeoutSec) => {
    setExperimentalAiTimeoutSec(timeoutSec)

    try {
      window.localStorage.setItem(experimentalAiTimeoutSecKey, String(timeoutSec))
    } catch {
      // Optional preference.
    }
  }

  const acceptBetaNotice = () => {
    setHasAcceptedBetaNotice(true)
    setIsBetaNoticeOpen(false)

    try {
      window.localStorage.setItem(betaNoticeAcceptedKey, 'true')
    } catch {
      // The notice is informational; local storage persistence should not block app use.
    }
  }

  const betaNoticeChrome = (
    <>
      <BetaNoticeFooter onOpen={() => setIsBetaNoticeOpen(true)} />
      <BetaPrivacyNotice open={isBetaNoticeOpen} onAccept={acceptBetaNotice} />
    </>
  )

  if (showOnboarding) {
    return (
      <div className="min-h-screen overflow-hidden bg-[var(--mimir-bg)] text-[var(--mimir-text)]">
        <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_-18%,rgba(255,255,255,0.06),transparent_34%),linear-gradient(140deg,rgba(255,255,255,0.028),transparent_46%)]" />
        <div className="relative h-screen overflow-y-auto p-4 sm:p-6">
          <OnboardingFlow initialScanMode={scanMode} onComplete={completeOnboarding} />
        </div>
      </div>
    )
  }

  if (appView === 'library' && latestSession) {
    return (
      <div className="min-h-screen overflow-hidden bg-[var(--mimir-bg)] text-[var(--mimir-text)]">
        <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_-18%,rgba(255,255,255,0.06),transparent_34%),linear-gradient(140deg,rgba(255,255,255,0.028),transparent_46%)]" />
        <div className="relative h-screen overflow-y-auto p-4 sm:p-6">
          <CrashSafeBoundary title="Incident library error" onBack={returnToImport}>
            <IncidentLibraryView
              session={latestSession}
              loadState={sessionLoadState}
              onImportNew={returnToImport}
              onLoadLatest={() => {
                void loadLatestSession(false)
              }}
              onReloadSession={() => loadLatestSession(false)}
              scanDiagnostics={scanDiagnostics}
            />
          </CrashSafeBoundary>
        </div>
        {betaNoticeChrome}
      </div>
    )
  }

  return (
    <div className="min-h-screen overflow-hidden bg-[var(--mimir-bg)] text-[var(--mimir-text)]">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_-18%,rgba(255,255,255,0.06),transparent_34%),linear-gradient(140deg,rgba(255,255,255,0.028),transparent_46%)]" />
      <div className="relative h-screen overflow-y-auto p-4 sm:p-6">
        <ImportPanel
          selectedFolder={selectedFolder}
          isDraggingFolder={isDraggingFolder}
          onChooseFolder={chooseFolder}
          onAnalyze={analyzeSelectedFolder}
          onCancelScan={() => {
            void cancelScan()
          }}
          isCancellingScan={isCancellingScan}
          loadState={sessionLoadState}
          scanState={scanState}
          scanError={scanError}
          scanOutput={scanOutput}
          scanProgress={scanProgress}
          lastProgressMessage={lastProgressMessage}
          clipCount={clipCount}
          isCountingClips={isCountingClips}
          scanMode={scanMode}
          onScanModeChange={updateScanMode}
          systemCheck={systemCheck}
          isCheckingSystem={isCheckingSystem}
          localAiStatus={localAiStatus}
          isCheckingLocalAi={isCheckingLocalAi}
          selectedVisionModel={selectedVisionModel}
          experimentalAiEnabled={experimentalAiEnabled}
          experimentalAiModel={experimentalAiModel}
          experimentalAiTimeoutSec={experimentalAiTimeoutSec}
          onExperimentalAiEnabledChange={updateExperimentalAiEnabled}
          onExperimentalAiModelChange={updateExperimentalAiModel}
          onExperimentalAiTimeoutSecChange={updateExperimentalAiTimeoutSec}
          isLocalAiSetupOpen={isLocalAiSetupOpen}
          onOpenLocalAiSetup={() => setIsLocalAiSetupOpen(true)}
          onCloseLocalAiSetup={() => setIsLocalAiSetupOpen(false)}
          onRecheckSystem={() => {
            void runSystemCheck()
          }}
          onRecheckLocalAi={() => {
            void recheckLocalAi()
          }}
          onOpenLocalAiDownloadPage={openLocalAiDownloadPage}
          onPullLocalAiModel={pullLocalAiModel}
          isPullingLocalAiModel={isPullingLocalAiModel}
          localAiInstallLine={localAiInstallLine}
          localAiInstallResult={localAiInstallResult}
          localAiSetupError={localAiSetupError}
          hasLatestSession={Boolean(latestSession)}
          onReturnToLatestSession={returnToLatestSession}
          sessionHistory={sessionHistory}
          onOpenSession={sessionPath => {
            void openSessionFromHistory(sessionPath)
          }}
        />
      </div>
      {betaNoticeChrome}
    </div>
  )
}
