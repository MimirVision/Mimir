import { useCallback, useEffect, useState } from 'react'
import { open } from '@tauri-apps/api/dialog'
import { listen } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/tauri'
import { appWindow } from '@tauri-apps/api/window'
import { BetaNoticeFooter, BetaPrivacyNotice } from './components/BetaPrivacyNotice'
import { CrashSafeBoundary } from './components/CrashSafeBoundary'
import { ImportPanel } from './components/ImportPanel'
import { IncidentLibraryView } from './components/IncidentLibraryView'
import { OnboardingFlow } from './components/OnboardingFlow'
import { FULL_AI_BETA, USE_MIMIR_CORE_V2 } from './config'
import type {
  BackendProgress,
  LocalAiInstallResult,
  LocalAiStatus,
  MimirSession,
  ScanRunState,
  SessionLoadState,
  SystemCheckResult,
} from './types'

const onboardingCompletedKey = 'mimir_onboarding_completed'
const defaultScanModeKey = 'mimir_default_scan_mode'
const betaNoticeAcceptedKey = 'mimir_beta_privacy_notice_accepted'
const selectedVisionModelKey = 'mimir_selected_vision_model'
const defaultVisionModel = 'qwen2.5vl:7b'

interface LocalScanResult {
  stdout: string
  stderr: string
  session_path?: string
  backend_mode?: string
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

interface ProgressLineEvent {
  line: string
}

interface LocalAiInstallLineEvent {
  line: string
}

type AppView = 'import' | 'library'
type ScanMode = 'fast' | 'balanced' | 'quality'

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
  const [isLocalAiSetupOpen, setIsLocalAiSetupOpen] = useState(false)
  const [isPullingLocalAiModel, setIsPullingLocalAiModel] = useState(false)
  const [localAiInstallLine, setLocalAiInstallLine] = useState('')
  const [localAiInstallResult, setLocalAiInstallResult] = useState<LocalAiInstallResult | null>(null)
  const [localAiSetupError, setLocalAiSetupError] = useState('')

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

    appWindow
      .onFileDropEvent(event => {
        if (event.payload.type === 'hover') {
          setIsDraggingFolder(true)
        }

        if (event.payload.type === 'cancel') {
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

    return () => {
      unlisten?.()
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function runSystemCheck() {
      setIsCheckingSystem(true)

      try {
        const result = await invoke<SystemCheckResult>('check_system_requirements')

        if (!cancelled) {
          setSystemCheck(result)
        }
      } catch (error) {
        if (!cancelled) {
          setSystemCheck({
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
          })
        }
      } finally {
        if (!cancelled) {
          setIsCheckingSystem(false)
        }
      }
    }

    void runSystemCheck()

    return () => {
      cancelled = true
    }
  }, [])

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
      title: 'Choose USB drive or footage folder',
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

  const loadLatestSession = useCallback(async (clearExisting = true): Promise<MimirSession | null> => {
    setSessionLoadState('loading')

    if (clearExisting) {
      setLatestSession(null)
    }

    try {
      const contents = await invoke<string>('load_latest_session_json')

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
  }, [])

  const runScan = async (useEnhancedAi: boolean) => {
    if (!selectedFolder || scanState === 'running') {
      return
    }

    setScanState('running')
    setScanError('')
    setScanOutput(null)
    setScanProgress(null)
    setLastProgressMessage('')
    setLatestSession(null)
    setSessionLoadState('loading')

    try {
      const result = await invoke<LocalScanResult>('run_local_scan', {
        selectedFolder,
        scanMode,
        useEnhancedAi,
        visionModel: selectedVisionModel,
      })

      setScanOutput({
        stdout: result.stdout,
        stderr: result.stderr,
      })

      const loadedSession = await loadLatestSession()
      const unsupportedSource = loadedSession?.source_report?.is_supported === false

      setScanState(loadedSession && !unsupportedSource ? 'complete' : 'error')

      if (!loadedSession) {
        setScanError('Mimir finished, but no scan result was found.')
        setScanOutput({
          stdout: result.stdout,
          stderr: [
            result.stderr,
            result.session_path ? `Expected result path: ${result.session_path}` : '',
          ].filter(Boolean).join('\n'),
        })
      } else if (unsupportedSource) {
        setScanError(
          'No footage found. Try selecting the USB drive itself, the TeslaCam folder, or a folder containing MP4 clips.',
        )
        setScanOutput({
          stdout: result.stdout,
          stderr: result.stderr,
        })
      } else {
        setScanProgress(null)
        setAppView('library')
      }
    } catch (error) {
      setSessionLoadState('idle')
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

    await runScan(USE_MIMIR_CORE_V2 ? false : FULL_AI_BETA ? true : localAiStatus?.ok === true)
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

  const updateScanMode = (mode: ScanMode) => {
    setScanMode(mode)

    try {
      window.localStorage.setItem(defaultScanModeKey, mode)
    } catch {
      // Persisting the default is optional; scanning still works without local storage.
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
          isLocalAiSetupOpen={isLocalAiSetupOpen}
          onOpenLocalAiSetup={() => setIsLocalAiSetupOpen(true)}
          onCloseLocalAiSetup={() => setIsLocalAiSetupOpen(false)}
          onRecheckLocalAi={() => {
            void recheckLocalAi()
          }}
          onOpenLocalAiDownloadPage={openLocalAiDownloadPage}
          onPullLocalAiModel={pullLocalAiModel}
          isPullingLocalAiModel={isPullingLocalAiModel}
          localAiInstallLine={localAiInstallLine}
          localAiInstallResult={localAiInstallResult}
          localAiSetupError={localAiSetupError}
        />
      </div>
      {betaNoticeChrome}
    </div>
  )
}
