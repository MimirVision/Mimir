import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type SyntheticEvent } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { open as openDialog, save as saveDialog } from '@tauri-apps/plugin-dialog'
import { CrashSafeBoundary, logIncidentDiagnostic } from './CrashSafeBoundary'
import { FEEDBACK_EMAIL } from '../config'
import { formatDateTime, sourceEventTimestamp, sourceFilename } from '../lib/incidentDisplay'
import { type SeverityGroup } from '../lib/incidentStatus'
import {
  actionButtonTone,
  actionErrorMessage,
  currentVideoPath,
  feedbackChoices,
  findIncident,
  incidentActionId,
  incidentFeedbackPayload,
  originalVideoPath,
  parseStorageActionReport,
  storageActionDetails,
  storageActionStatus,
  storageState,
  type AiFeedbackChoice,
  type IncidentAction,
  type IncidentFeedbackResult,
  type StorageActionResult,
  type TrainingContributionResult,
} from '../lib/incidentActions'
import {
  dedupeMarkersByPriority,
  deriveReviewTimelineMarkers,
  formatTime,
  formatTimecode,
  markerDescription,
  markerIsPrimaryMoment,
  markerKey,
  markerLabel,
  markerPosition,
  markerTime,
  reviewTimelineDuration,
  timedMarkers,
} from '../lib/incidentTimeline'
import {
  markerAccentForMarker,
  markerSeverityCopy,
  markerVisualClass,
  severityClass,
  severityCopy,
} from '../lib/incidentViewerStyles'
import {
  aiConfidenceCopy,
  aiEvidence,
  aiModelName,
  aiQualityWarning,
  aiRecommendedSeverity,
  aiReviewed,
  aiSceneType,
  calmerPersonNearWording,
  classificationDebug,
  contactLevelCopy,
  evidenceMetricValue,
  eventDisplayTitle,
  experimentalAiUsed,
  formatNumber,
  hasSupportedContactEvidence,
  hasSupportedImpactEvidence,
  importantNotAppliedNote,
  localEvidence,
  prettyJson,
  reviewBadgeCopy,
  safeText,
  safeTextList,
  severityReasonList,
  yesNo,
} from '../lib/incidentEvidence'
import {
  attemptedVideoPathForIncident,
  cameraFeedsForIncident,
  cameraLabel,
  cleanPath,
  isAbsoluteLocalPath,
  isImagePath,
  isTextInputElement,
  isVideoPath,
  localFileSrc,
  normalizeCameraClips,
  playableCameraPath,
  resolveViewerMedia,
  videoCandidatesForIncident,
  type CameraFeed,
} from '../lib/incidentVideoPaths'
import type { MimirIncident, MimirSession, MimirTimelineMarker } from '../types'

interface IncidentViewerScreenProps {
  incident: MimirIncident
  session?: MimirSession
  severityResolution: SeverityResolution
  onBack: () => void
  onReloadSession: () => Promise<MimirSession | null>
  onIncidentUpdated: (incident: MimirIncident) => void
  onManualStatusChange: (status: SeverityGroup) => void
}

interface ClipActionResult {
  ok: boolean
  action: string
  incident_id: string
  message: string
  updated_session: string
  stdout?: string
  stderr?: string
}

interface SeverityResolution {
  mimirSeverity: SeverityGroup
  displaySeverity: SeverityGroup
  isManualOverride: boolean
  source: 'manual' | 'mimir'
}

type ViewerMode = 'focus' | 'grid'

interface ViewerSeekRequest {
  time: number
  nonce: number
}

interface ViewerPlaybackRequest {
  nonce: number
}

type ViewerFileAction = 'original' | 'current' | 'library' | 'trash'

type GridSecondaryPlaybackMode = 'video' | 'poster'

const GRID_SECONDARY_PLAYBACK_MODE: GridSecondaryPlaybackMode = 'video'
const SECONDARY_SYNC_INTERVAL_MS = 750
const SECONDARY_SYNC_DRIFT_SEC = 0.35
const UI_TIME_UPDATE_MS = 250

// ============================================================================
// Small shared presentational components
// ============================================================================

function SmallSpinner() {
  return <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current/25 border-t-current" />
}

function FolderButton({
  label,
  disabled = false,
  onClick,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="h-8 rounded-md border border-white/[0.07] bg-white/[0.025] px-2.5 text-[11px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-45"
    >
      {label}
    </button>
  )
}

function ViewerFilesDrawer({
  incident,
  onClose,
  onOpenFileAction,
}: {
  incident: MimirIncident
  onClose: () => void
  onOpenFileAction: (action: ViewerFileAction) => void
}) {
  const clips = normalizeCameraClips(incident)
  const state = storageState(incident)
  const currentPath = currentVideoPath(incident)
  const originalPath = originalVideoPath(incident)

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/62 backdrop-blur-sm">
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="Close files drawer"
        onClick={onClose}
      />
      <aside className="relative h-full w-full max-w-[420px] overflow-y-auto border-l border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <div className="text-[12px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">Files</div>
            <h2 className="mt-2 text-[22px] font-semibold text-[var(--mimir-text)]">Incident files</h2>
            <p className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
              Storage state: <span className="text-[var(--mimir-text)]">{state}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="h-9 rounded-lg bg-white/[0.045] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.075] hover:text-[var(--mimir-text)]"
          >
            Close
          </button>
        </div>

        <div className="rounded-2xl border border-white/[0.06] bg-black/14 p-4">
          <div className="mb-3 text-[12px] font-semibold text-[var(--mimir-text)]">Camera clips</div>
          {clips.length > 0 ? (
            <div className="space-y-2">
              {clips.map((clip, index) => {
                const camera = cameraLabel(clip.camera)
                const path = playableCameraPath(clip)
                const available = clip.exists === false ? false : Boolean(path)

                return (
                  <div
                    key={`${camera}-${path || index}`}
                    className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.045] bg-white/[0.018] px-3 py-2.5"
                  >
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-[var(--mimir-text)]">{camera}</div>
                      <div className="mt-0.5 truncate text-[12px] text-[var(--mimir-text-subtle)]">
                        {clip.filename || sourceFilename(path) || 'Filename unavailable'}
                      </div>
                    </div>
                    <span
                      className={`shrink-0 rounded-full border px-2 py-1 text-[10px] font-semibold ${
                        available
                          ? 'border-white/[0.08] bg-white/[0.04] text-[var(--mimir-text-muted)]'
                          : 'border-red-300/18 bg-red-500/10 text-red-100/78'
                      }`}
                    >
                      {available ? 'Available' : 'Missing'}
                    </span>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="rounded-xl border border-white/[0.045] bg-white/[0.018] p-3 text-[13px] text-[var(--mimir-text-muted)]">
              No grouped camera clip list was included.
            </div>
          )}
        </div>

        <div className="mt-4 rounded-2xl border border-white/[0.06] bg-black/14 p-4">
          <div className="mb-3 text-[12px] font-semibold text-[var(--mimir-text)]">Folders</div>
          <div className="grid gap-2">
            <FolderButton label="Open original folder" disabled={!originalPath} onClick={() => onOpenFileAction('original')} />
            <FolderButton label="Open current folder" disabled={!currentPath} onClick={() => onOpenFileAction('current')} />
            <FolderButton label="Open Mimir Library" onClick={() => onOpenFileAction('library')} />
            <FolderButton label="Open Mimir Trash" onClick={() => onOpenFileAction('trash')} />
          </div>
        </div>
      </aside>
    </div>
  )
}

// ============================================================================
// Main screen components: media player, timeline, details/feedback panels,
// review actions. IncidentViewerScreen (bottom of file) composes all of these.
// ============================================================================

function DetailMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="border-t border-white/[0.065] py-3 first:border-t-0">
      <div className="text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">{label}</div>
      <div className="mt-1.5 break-words text-[13px] font-medium leading-5 text-[var(--mimir-text)]">
        {value}
      </div>
    </div>
  )
}

function TechnicalJsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-lg border border-white/[0.045] bg-black/20 p-3">
      <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">{label}</div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-[var(--mimir-text-muted)]">
        {prettyJson(value)}
      </pre>
    </div>
  )
}

function ViewerMedia({
  incident,
  seekRequest,
  playbackRequest,
  currentTime,
  onTimeUpdate,
  onDurationChange,
}: {
  incident: MimirIncident
  seekRequest: ViewerSeekRequest | null
  playbackRequest: ViewerPlaybackRequest | null
  currentTime: number
  onTimeUpdate: (time: number) => void
  onDurationChange: (duration: number) => void
}) {
  const cameraFeeds = useMemo(() => cameraFeedsForIncident(incident), [incident])
  const videoCandidates = useMemo(() => videoCandidatesForIncident(incident), [incident])
  const media = useMemo(() => resolveViewerMedia(incident), [incident])
  const fallbackImage =
    [incident.hero_thumbnail, incident.contact_sheet, incident.thumbnail, incident.best_frame_image]
      .map(cleanPath)
      .find(path => isAbsoluteLocalPath(path) && isImagePath(path)) ?? ''
  const [viewerMode, setViewerMode] = useState<ViewerMode>('focus')
  const [selectedFeedKey, setSelectedFeedKey] = useState('')
  const [failedFeedKeys, setFailedFeedKeys] = useState<Set<string>>(() => new Set())
  const [loadedFeedKeys, setLoadedFeedKeys] = useState<Set<string>>(() => new Set())
  const [loadingFeedKeys, setLoadingFeedKeys] = useState<Set<string>>(() => new Set())
  const [imageFailed, setImageFailed] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [selectedGridKey, setSelectedGridKey] = useState('')
  const videoRefs = useRef<Map<string, HTMLVideoElement>>(new Map())
  const lastUiTimeUpdateRef = useRef(0)
  const lastSeekNonceRef = useRef<number | null>(null)
  const lastPlaybackNonceRef = useRef<number | null>(null)
  const isPlayingRef = useRef(false)
  const pendingCameraHandoffRef = useRef<{ key: string; time: number; playing: boolean } | null>(null)
  const primaryFeed = cameraFeeds.find(feed => feed.isPrimary) || cameraFeeds[0]
  const selectedFeed = cameraFeeds.find(feed => feed.key === selectedFeedKey) || primaryFeed
  const playableFeeds = cameraFeeds.filter(feed => feed.path && isAbsoluteLocalPath(feed.path) && isVideoPath(feed.path) && feed.exists !== false)
  const canUseMultiCamera = cameraFeeds.length > 1
  const showSingleVideo = !canUseMultiCamera && Boolean(videoCandidates[0]?.path) && !failedFeedKeys.has('single-video')
  const allVisibleFeeds = canUseMultiCamera ? cameraFeeds : []
  const secondaryFeeds = canUseMultiCamera ? allVisibleFeeds.filter(feed => feed.key !== selectedFeed?.key) : []
  const gridFeeds = canUseMultiCamera ? allVisibleFeeds : []
  const anyVideoFailed = failedFeedKeys.size > 0
  const imagePath =
    (!canUseMultiCamera && !showSingleVideo && fallbackImage) || media.mode === 'image'
      ? fallbackImage || media.path
      : ''
  const showImage = Boolean(imagePath) && !imageFailed
  const showVideoFallbackMessage = !canUseMultiCamera && failedFeedKeys.has('single-video') && Boolean(fallbackImage)
  const showMissingVideoMessage = !showSingleVideo && !canUseMultiCamera && showImage
  const videoSrc = showSingleVideo && videoCandidates[0] ? localFileSrc(videoCandidates[0].path) : ''
  const imageSrc = showImage ? localFileSrc(imagePath) : ''
  const selectedFeedLabel = selectedFeed?.isPrimary
    ? `Best: ${selectedFeed.label}`
    : selectedFeed?.label || 'Camera'
  const masterVideoKey = canUseMultiCamera ? selectedFeed?.key || '' : showSingleVideo ? 'single-video' : ''

  const getMasterVideo = useCallback(() => {
    if (masterVideoKey) {
      const masterVideo = videoRefs.current.get(masterVideoKey)
      if (masterVideo) {
        return masterVideo
      }
    }

    return Array.from(videoRefs.current.values()).find(video => video.readyState > 0) || null
  }, [masterVideoKey])

  const updateUiTime = useCallback(
    (time: number, force = false) => {
      if (!Number.isFinite(time)) {
        return
      }

      const now = window.performance.now()
      if (!force && now - lastUiTimeUpdateRef.current < UI_TIME_UPDATE_MS) {
        return
      }

      lastUiTimeUpdateRef.current = now
      onTimeUpdate(Math.max(0, time))
    },
    [onTimeUpdate],
  )

  const syncSecondaryVideos = useCallback(
    (time: number, options: { playing?: boolean; force?: boolean } = {}) => {
      if (!Number.isFinite(time)) {
        return
      }

      const targetTime = Math.max(0, time)
      const shouldPlay = Boolean(options.playing)

      for (const [key, video] of videoRefs.current.entries()) {
        if (!video || key === masterVideoKey || video.readyState === 0 || failedFeedKeys.has(key)) {
          continue
        }

        try {
          const drift = Math.abs(video.currentTime - targetTime)
          if (options.force || drift > SECONDARY_SYNC_DRIFT_SEC) {
            video.currentTime = targetTime
          }

          if (shouldPlay) {
            void video.play().catch(() => {
              // A secondary camera refusing playback should not stop the master feed.
            })
          } else if (!video.paused) {
            video.pause()
          }
        } catch {
          // Keep other camera feeds usable if one media element refuses a seek/play.
        }
      }
    },
    [failedFeedKeys, masterVideoKey],
  )

  const syncAllVideosTo = useCallback(
    (time: number, playing: boolean) => {
      if (!Number.isFinite(time)) {
        return
      }

      const targetTime = Math.max(0, time)

      for (const [key, video] of videoRefs.current.entries()) {
        if (!video || video.readyState === 0 || failedFeedKeys.has(key)) {
          continue
        }

        try {
          if (Math.abs(video.currentTime - targetTime) > 0.05) {
            video.currentTime = targetTime
          }

          if (playing) {
            void video.play().catch(() => {
              if (key === masterVideoKey) {
                setIsPlaying(false)
              }
            })
          } else if (!video.paused) {
            video.pause()
          }
        } catch {
          // Shared seeking is best-effort per feed.
        }
      }

      updateUiTime(targetTime, true)
    },
    [failedFeedKeys, masterVideoKey, updateUiTime],
  )

  const handleSharedPlayToggle = useCallback(() => {
    const masterVideo = getMasterVideo()
    const baseTime = masterVideo?.currentTime ?? currentTime
    const nextPlaying = !isPlaying

    setIsPlaying(nextPlaying)
    syncAllVideosTo(baseTime, nextPlaying)
  }, [currentTime, getMasterVideo, isPlaying, syncAllVideosTo])

  useEffect(() => {
    isPlayingRef.current = isPlaying
  }, [isPlaying])

  useEffect(() => {
    const nextPrimary = cameraFeeds.find(feed => feed.isPrimary) || cameraFeeds[0]
    setViewerMode('focus')
    setSelectedFeedKey(nextPrimary?.key || '')
    setSelectedGridKey(nextPrimary?.key || '')
    setFailedFeedKeys(new Set())
    setLoadedFeedKeys(nextPrimary?.key ? new Set([nextPrimary.key]) : new Set())
    setLoadingFeedKeys(new Set())
    setImageFailed(false)
    setIsPlaying(false)
    videoRefs.current.clear()
  }, [incident.id, cameraFeeds.map(feed => `${feed.key}:${feed.path}:${feed.exists}`).join('|'), media.path])

  useEffect(() => {
    if (!selectedFeed?.key) {
      return
    }

    setLoadedFeedKeys(previous => {
      if (previous.has(selectedFeed.key)) {
        return previous
      }

      const next = new Set(previous)
      next.add(selectedFeed.key)
      return next
    })
  }, [selectedFeed?.key])

  useEffect(() => {
    if (
      !seekRequest ||
      lastSeekNonceRef.current === seekRequest.nonce ||
      !Number.isFinite(seekRequest.time) ||
      seekRequest.time < 0
    ) {
      return
    }

    lastSeekNonceRef.current = seekRequest.nonce
    const safeTime = Math.max(0, seekRequest.time)
    syncAllVideosTo(safeTime, isPlayingRef.current)
  }, [seekRequest, syncAllVideosTo])

  useEffect(() => {
    if (!playbackRequest || lastPlaybackNonceRef.current === playbackRequest.nonce) {
      return
    }

    lastPlaybackNonceRef.current = playbackRequest.nonce
    handleSharedPlayToggle()
  }, [handleSharedPlayToggle, playbackRequest])

  useEffect(() => {
    if (!isPlaying || !canUseMultiCamera) {
      return
    }

    const intervalId = window.setInterval(() => {
      const masterVideo = getMasterVideo()
      if (!masterVideo || masterVideo.paused || masterVideo.readyState === 0) {
        return
      }

      syncSecondaryVideos(masterVideo.currentTime, { playing: true })
    }, SECONDARY_SYNC_INTERVAL_MS)

    return () => window.clearInterval(intervalId)
  }, [canUseMultiCamera, getMasterVideo, isPlaying, syncSecondaryVideos])

  const setVideoRef = useCallback(
    (key: string) => (node: HTMLVideoElement | null) => {
      if (node) {
        videoRefs.current.set(key, node)
      } else {
        videoRefs.current.delete(key)
      }
    },
    [],
  )

  const markFeedFailed = (feed: CameraFeed | null, errorMessage = 'Video element failed to load local camera feed.') => {
    const key = feed?.key || 'single-video'
    const failedPath = feed?.path || videoCandidates[0]?.path || media.path

    void logIncidentDiagnostic({
      incidentId: incidentActionId(incident),
      attemptedVideoPath: failedPath,
      errorMessage,
    })

    setFailedFeedKeys(previous => {
      const next = new Set(previous)
      next.add(key)
      return next
    })
    setLoadingFeedKeys(previous => {
      const next = new Set(previous)
      next.delete(key)
      return next
    })
  }

  const selectCameraFeed = (feed: CameraFeed) => {
    const previousMaster = getMasterVideo()
    const handoffTime = previousMaster?.currentTime ?? currentTime
    const shouldKeepPlaying = previousMaster ? !previousMaster.paused : isPlayingRef.current
    pendingCameraHandoffRef.current = {
      key: feed.key,
      time: Number.isFinite(handoffTime) ? Math.max(0, handoffTime) : 0,
      playing: shouldKeepPlaying,
    }

    setSelectedFeedKey(feed.key)
    setSelectedGridKey(feed.key)
    setLoadedFeedKeys(previous => {
      const next = new Set(previous)
      next.add(feed.key)
      return next
    })

    window.requestAnimationFrame(() => {
      const nextMaster = videoRefs.current.get(feed.key)
      if (!nextMaster || nextMaster.readyState === 0) {
        return
      }

      try {
        const targetDuration = Number.isFinite(nextMaster.duration) && nextMaster.duration > 0 ? nextMaster.duration : Number.POSITIVE_INFINITY
        if (Number.isFinite(handoffTime)) {
          nextMaster.currentTime = Math.min(Math.max(0, handoffTime), targetDuration)
        }

        if (shouldKeepPlaying) {
          void nextMaster.play().catch(() => {
            setIsPlaying(false)
          })
        }
        pendingCameraHandoffRef.current = null
      } catch {
        // Camera switching should never break the rest of the viewer.
      }
    })
  }

  const applyPendingCameraHandoff = (feed: CameraFeed, video: HTMLVideoElement) => {
    const handoff = pendingCameraHandoffRef.current
    if (!handoff || handoff.key !== feed.key) {
      return
    }

    try {
      const targetDuration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Number.POSITIVE_INFINITY
      video.currentTime = Math.min(handoff.time, targetDuration)

      if (handoff.playing) {
        void video.play().catch(() => {
          setIsPlaying(false)
        })
      } else if (!video.paused) {
        video.pause()
      }
    } catch {
      // Camera handoff is best-effort; loading failures are handled separately.
    } finally {
      pendingCameraHandoffRef.current = null
    }
  }

  const markFeedLoading = (key: string, loading: boolean) => {
    setLoadingFeedKeys(previous => {
      const next = new Set(previous)
      if (loading) {
        next.add(key)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  const handleMasterTimeUpdate = (event: SyntheticEvent<HTMLVideoElement>) => {
    const time = event.currentTarget.currentTime
    updateUiTime(time)
  }

  const renderUnavailable = (label: string, className = '', loadFailed = false) => (
    <div className={`grid min-h-[180px] place-items-center bg-[linear-gradient(145deg,#08090a,#020202)] text-center ${className}`}>
      <div className="px-5">
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-white/12" />
        <div className="text-[15px] font-semibold text-[var(--mimir-text)]">
          {loadFailed ? `Could not load ${label} camera` : `${label} unavailable`}
        </div>
        <p className="mt-2 text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
          The rest of the incident is still available.
        </p>
      </div>
    </div>
  )

  const renderFeedVideo = (feed: CameraFeed, options: { main?: boolean; muted?: boolean; className?: string }) => {
    const failed = failedFeedKeys.has(feed.key)
    const isMasterFeed = feed.key === masterVideoKey
    const shouldRenderSecondaryVideo = options.main || viewerMode !== 'grid' || GRID_SECONDARY_PLAYBACK_MODE === 'video'
    const shouldLoad =
      shouldRenderSecondaryVideo &&
      (options.main ||
        viewerMode === 'grid' ||
        !options.main ||
        loadedFeedKeys.has(feed.key) ||
        feed.key === selectedFeed?.key)
    const loading = loadingFeedKeys.has(feed.key)

    if (failed || !feed.path || !isAbsoluteLocalPath(feed.path) || !isVideoPath(feed.path) || feed.exists === false) {
      return renderUnavailable(feed.label, options.className, failed)
    }

    if (!shouldRenderSecondaryVideo) {
      return renderUnavailable(feed.label, options.className)
    }

    return (
      <div className={`relative overflow-hidden bg-black ${options.className || ''}`}>
        <video
          key={`${feed.key}-${feed.path}`}
          ref={setVideoRef(feed.key)}
          src={shouldLoad ? localFileSrc(feed.path) : undefined}
          preload={options.main || viewerMode === 'grid' ? 'auto' : 'metadata'}
          playsInline
          controls={isMasterFeed}
          muted={!isMasterFeed || Boolean(options.muted)}
          className="h-full w-full bg-black object-contain"
          onLoadedMetadata={event => {
            markFeedLoading(feed.key, false)
            const durationValue = event.currentTarget.duration
            if (isMasterFeed && Number.isFinite(durationValue) && durationValue > 0) {
              onDurationChange(durationValue)
            }
            if (isMasterFeed) {
              applyPendingCameraHandoff(feed, event.currentTarget)
              updateUiTime(event.currentTarget.currentTime, true)
            }
          }}
          onDurationChange={event => {
            const durationValue = event.currentTarget.duration
            if (isMasterFeed && Number.isFinite(durationValue) && durationValue > 0) {
              onDurationChange(durationValue)
            }
          }}
          onTimeUpdate={isMasterFeed ? handleMasterTimeUpdate : undefined}
          onSeeked={event => {
            if (!isMasterFeed) {
              return
            }

            syncSecondaryVideos(event.currentTarget.currentTime, {
              playing: !event.currentTarget.paused,
              force: true,
            })
            updateUiTime(event.currentTarget.currentTime, true)
          }}
          onPlay={event => {
            if (!isMasterFeed) {
              return
            }

            setIsPlaying(true)
            syncSecondaryVideos(event.currentTarget.currentTime, { playing: true, force: true })
          }}
          onPause={event => {
            if (!isMasterFeed) {
              return
            }

            setIsPlaying(false)
            syncSecondaryVideos(event.currentTarget.currentTime, { playing: false })
          }}
          onLoadStart={() => markFeedLoading(feed.key, true)}
          onLoadedData={() => markFeedLoading(feed.key, false)}
          onCanPlay={event => {
            markFeedLoading(feed.key, false)
            if (isMasterFeed) {
              applyPendingCameraHandoff(feed, event.currentTarget)
            }
          }}
          onError={() => markFeedFailed(feed)}
        />
        <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-1.5">
          <span className="rounded-full border border-white/10 bg-black/62 px-2.5 py-1 text-[11px] font-semibold text-white/78 backdrop-blur-md">
            {feed.label}
          </span>
          {feed.isPrimary && (
            <span className="rounded-full border border-white/10 bg-white/[0.095] px-2.5 py-1 text-[11px] font-semibold text-white/72 backdrop-blur-md">
              Best angle
            </span>
          )}
        </div>
        {loading && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center bg-black/28 backdrop-blur-[1px]">
            <span className="h-6 w-6 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="relative overflow-hidden rounded-[20px] bg-[radial-gradient(circle_at_50%_0%,rgba(255,255,255,0.055),transparent_34%),#020202] shadow-[inset_0_1px_0_rgba(255,255,255,0.035)]">
      {canUseMultiCamera && (
        <div className="border-b border-white/[0.07] bg-[linear-gradient(180deg,rgba(255,255,255,0.045),rgba(255,255,255,0.014))] px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {(['focus', 'grid'] as const).map(mode => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setViewerMode(mode)}
                  className={`h-8 rounded-lg px-3 text-[12px] font-semibold capitalize transition ${
                    viewerMode === mode
                      ? 'bg-white text-black'
                      : 'border border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)] hover:bg-white/[0.07] hover:text-[var(--mimir-text)]'
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-white/[0.045] px-2.5 py-1 text-[11px] text-[var(--mimir-text-subtle)]">
                {cameraFeeds.length} angles
              </span>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="mr-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--mimir-text-subtle)]">
              Camera
            </span>
            {cameraFeeds.map(feed => {
              const selected = selectedFeed?.key === feed.key
              const label = feed.isPrimary ? 'Best' : feed.label

              return (
                <button
                  key={feed.key}
                  type="button"
                  onClick={() => selectCameraFeed(feed)}
                  className={`h-8 rounded-full border px-3 text-[12px] font-semibold transition ${
                    selected
                      ? 'border-white/28 bg-white/[0.13] text-[var(--mimir-text)]'
                      : 'border-white/[0.08] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:bg-white/[0.06] hover:text-[var(--mimir-text)]'
                  }`}
                  title={feed.isPrimary ? `Best angle: ${feed.label}` : feed.label}
                >
                  {label}
                  {feed.isPrimary && <span className="ml-1 font-medium text-white/52">{feed.label}</span>}
                </button>
              )
            })}
            <span className="ml-auto hidden text-[11px] text-[var(--mimir-text-subtle)] md:inline">
              Showing {selectedFeedLabel}
            </span>
          </div>
        </div>
      )}

      {canUseMultiCamera && viewerMode === 'focus' && selectedFeed && (
        <div className="grid gap-3 p-3">
          {renderFeedVideo(selectedFeed, {
            main: true,
            muted: false,
            className: 'min-h-[420px] rounded-[18px] lg:min-h-[640px]',
          })}
          {secondaryFeeds.length > 0 && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
              {secondaryFeeds.map(feed => {
                const isSelected = selectedFeed.key === feed.key
                return (
                  <button
                    key={feed.key}
                    type="button"
                    onClick={() => {
                      selectCameraFeed(feed)
                    }}
                    className={`overflow-hidden rounded-[14px] border bg-black/45 text-left shadow-[0_12px_30px_rgba(0,0,0,0.18)] transition ${
                      isSelected
                        ? 'border-white/30 ring-2 ring-white/18'
                        : 'border-white/[0.055] hover:border-white/18 hover:bg-white/[0.025]'
                    }`}
                  >
                    {renderFeedVideo(feed, {
                      muted: true,
                      className: 'aspect-video min-h-0',
                    })}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}

      {canUseMultiCamera && viewerMode === 'grid' && (
        <div className={`grid gap-3 p-3 ${gridFeeds.length === 4 ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-3'}`}>
          {gridFeeds.map(feed => {
            const selected = (selectedGridKey || selectedFeed?.key) === feed.key
            return (
              <div
                key={feed.key}
                role="button"
                tabIndex={0}
                onClick={() => {
                  if (!selected) {
                    selectCameraFeed(feed)
                  }
                }}
                onKeyDown={event => {
                  if (!selected && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault()
                    selectCameraFeed(feed)
                  }
                }}
                className={`overflow-hidden rounded-[18px] border bg-black/45 text-left shadow-[0_18px_46px_rgba(0,0,0,0.22)] transition ${
                  selected ? 'border-white/28 ring-2 ring-white/14' : 'border-white/[0.055] hover:border-white/16'
                }`}
              >
                {renderFeedVideo(feed, {
                  main: feed.key === selectedFeed?.key,
                  muted: feed.key !== selectedFeed?.key,
                  className: 'aspect-video min-h-[240px]',
                })}
              </div>
            )
          })}
        </div>
      )}

      {showSingleVideo && (
        <video
          key={videoCandidates[0].path}
          ref={setVideoRef('single-video')}
          src={videoSrc}
          controls
          preload="metadata"
          playsInline
          className="max-h-[76vh] w-full bg-black object-contain"
          onLoadedMetadata={event => {
            onDurationChange(event.currentTarget.duration)
            onTimeUpdate(event.currentTarget.currentTime)
          }}
          onDurationChange={event => onDurationChange(event.currentTarget.duration)}
          onTimeUpdate={event => onTimeUpdate(event.currentTarget.currentTime)}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onError={() => markFeedFailed(null)}
        />
      )}

      {showImage && (
        <div className="w-full">
          {(showVideoFallbackMessage || showMissingVideoMessage) && (
            <div className="border-b border-white/10 bg-white/[0.035] px-5 py-3 text-[13px] text-[var(--mimir-text-muted)]">
              {showMissingVideoMessage
                ? 'Video file not found. Showing available evidence image instead.'
                : incident.contact_sheet
                ? 'Video could not be loaded. Showing contact sheet instead.'
                : 'Video could not be loaded. Showing available evidence image instead.'}
            </div>
          )}
          <img
            key={imagePath}
            src={imageSrc}
            alt=""
            className="max-h-[76vh] w-full object-contain"
            onError={() => setImageFailed(true)}
          />
        </div>
      )}

      {(media.mode === 'empty' || (!canUseMultiCamera && !showSingleVideo && !showImage)) && (
        <div className="grid min-h-[390px] place-items-center px-8 text-center lg:min-h-[640px]">
          <div>
            <div className="mx-auto mb-5 h-1 w-14 rounded-full bg-white/18" />
            <h3 className="text-[20px] font-semibold text-[var(--mimir-text)]">Video file not found</h3>
            <p className="mt-3 max-w-[360px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir could not find a playable local video for this incident. The incident details are still available.
            </p>
          </div>
        </div>
      )}

      {canUseMultiCamera && anyVideoFailed && (
        <div className="border-t border-white/[0.07] bg-white/[0.025] px-4 py-2 text-[12px] text-[var(--mimir-text-subtle)]">
          One camera feed could not be loaded. The remaining feeds are still available.
        </div>
      )}

      {!canUseMultiCamera && playableFeeds.length === 1 && media.mode === 'empty' && (
        <div className="grid min-h-[390px] place-items-center px-8 text-center lg:min-h-[640px]">
          <div className="mx-auto mb-5 h-1 w-14 rounded-full bg-white/18" />
          <h3 className="text-[20px] font-semibold text-[var(--mimir-text)]">Video file not found</h3>
          <p className="mt-3 max-w-[360px] text-[14px] leading-6 text-[var(--mimir-text-muted)]">
            Mimir could not find a playable local video for this incident. The incident details are still available.
          </p>
        </div>
      )}

    </div>
  )
}

function IncidentTimelineMarkers({
  markers,
  incident,
  currentTime,
  duration,
  onSeek,
  onSetActualMoment,
}: {
  markers: MimirTimelineMarker[]
  incident: MimirIncident
  currentTime: number
  duration: number
  onSeek: (time: number) => void
  onSetActualMoment: (time: number) => void
}) {
  const [hoveredMarkerIndex, setHoveredMarkerIndex] = useState<number | null>(null)
  const [selectedMarkerIndex, setSelectedMarkerIndex] = useState<number | null>(null)
  const knownDuration = reviewTimelineDuration(incident, duration)
  const displayedMarkers = useMemo(
    () => dedupeMarkersByPriority(markers, incident, { allMoments: true }),
    [incident, markers],
  )
  const canSeekRail = Number.isFinite(knownDuration) && knownDuration > 0
  const effectiveDuration = canSeekRail ? knownDuration : 0
  const progressPercent =
    effectiveDuration > 0 ? Math.max(0, Math.min(100, (currentTime / effectiveDuration) * 100)) : 0
  const selectedMarker = selectedMarkerIndex === null ? null : displayedMarkers[selectedMarkerIndex]
  const selectMarker = (index: number) => {
    const marker = displayedMarkers[index]
    setSelectedMarkerIndex(index)

    const time = markerTime(marker)
    if (time !== null) {
      onSeek(time)
    }
  }
  const selectAdjacentMarker = (direction: 'previous' | 'next') => {
    if (displayedMarkers.length === 0) {
      return
    }

    const currentIndex = selectedMarkerIndex ?? 0
    const nextIndex =
      direction === 'previous'
        ? Math.max(0, currentIndex - 1)
        : Math.min(displayedMarkers.length - 1, currentIndex + 1)

    selectMarker(nextIndex)
  }

  useEffect(() => {
    setHoveredMarkerIndex(null)
    setSelectedMarkerIndex(displayedMarkers.length > 0 ? 0 : null)
  }, [incident.id, displayedMarkers.length])

  if (!canSeekRail) {
    return (
      <div className="rounded-2xl bg-white/[0.014] p-5">
        <div className="text-[13px] text-[var(--mimir-text-muted)]">Loading key moments...</div>
      </div>
    )
  }

  if (displayedMarkers.length === 0) {
    return (
      <div className="rounded-2xl bg-white/[0.014] p-5">
        <div className="text-[13px] text-[var(--mimir-text-muted)]">No key moments found.</div>
      </div>
    )
  }

  const handleRailClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!canSeekRail) {
      return
    }

    const bounds = event.currentTarget.getBoundingClientRect()
    if (!Number.isFinite(bounds.width) || bounds.width <= 0) {
      return
    }

    const ratio = (event.clientX - bounds.left) / bounds.width
    const seekTime = Math.max(0, Math.min(knownDuration, ratio * knownDuration))

    onSeek(seekTime)
  }

  return (
    <div className="rounded-2xl bg-black/12 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.018)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[12px] font-semibold text-[var(--mimir-text-subtle)]">Key moments</div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => onSetActualMoment(currentTime)}
            className="h-8 rounded-full border border-white/[0.06] bg-white/[0.018] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)]"
            title="Save the current video position as the actual moment"
          >
            Use current time
          </button>
          {displayedMarkers.length > 1 && (
            <>
              <button
                type="button"
                onClick={() => selectAdjacentMarker('previous')}
                disabled={selectedMarkerIndex === 0}
                className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-35"
              >
                Previous
              </button>
              <button
                type="button"
                onClick={() => selectAdjacentMarker('next')}
                disabled={selectedMarkerIndex === displayedMarkers.length - 1}
                className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-35"
              >
                Next
              </button>
            </>
          )}
          <div className="flex items-center gap-2 rounded-full bg-white/[0.025] px-3 py-1 text-[12px] text-[var(--mimir-text-subtle)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--mimir-accent)]" />
            {formatTime(currentTime)} / {formatTime(effectiveDuration)}
          </div>
        </div>
      </div>

      <div
        role="presentation"
        onClick={handleRailClick}
        className={`relative mx-2 h-[138px] ${canSeekRail ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <div className="absolute left-0 right-0 top-[48px] h-px bg-white/[0.08]" />
        <div className="absolute left-0 right-0 top-[46px] h-3 rounded-full bg-black/28 shadow-[inset_0_1px_1px_rgba(0,0,0,0.34)]" />
        <div
          className="absolute left-0 top-[46px] h-3 rounded-full bg-[rgba(157,183,170,0.34)] transition-[width]"
          style={{ width: `${progressPercent}%` }}
        />
        <div
          className="absolute top-[34px] h-12 w-px bg-white/42"
          style={{ left: `${progressPercent}%` }}
        />
        {displayedMarkers.map((marker, index) => {
          const time = markerTime(marker)
          const position = markerPosition(marker, index, displayedMarkers.length, effectiveDuration)
          const isHovered = hoveredMarkerIndex === index
          const isSelected = selectedMarkerIndex === index
          const isTimed = time !== null
          const isPrimary = markerIsPrimaryMoment(marker, incident)

          return (
            <button
              key={markerKey(marker, index)}
              type="button"
              onMouseEnter={() => setHoveredMarkerIndex(index)}
              onFocus={() => setHoveredMarkerIndex(index)}
              onBlur={() => setHoveredMarkerIndex(null)}
              onClick={event => {
                event.stopPropagation()
                selectMarker(index)
              }}
              onMouseLeave={() => setHoveredMarkerIndex(null)}
              title={`${isPrimary ? 'Primary moment - ' : ''}${markerLabel(marker, incident)} - ${isTimed ? formatTime(time) : 'time unavailable'}${markerDescription(marker, incident) ? `\n${markerDescription(marker, incident)}` : ''}`}
              className="group absolute top-[22px] flex -translate-x-1/2 cursor-pointer flex-col items-center rounded-full outline-none"
              style={{ left: `${position}%` }}
              aria-label={`${isPrimary ? 'Primary moment: ' : ''}${markerLabel(marker, incident)} ${isTimed ? formatTime(time) : 'time unavailable'}`}
            >
              {isPrimary && (
                <span className="absolute -top-4 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-[var(--mimir-accent)] px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.06em] text-[#0c1210]">
                  Primary
                </span>
              )}
              <span className="absolute left-1/2 top-9 h-6 w-px -translate-x-1/2 bg-white/[0.08]" />
              <span
                className={`relative z-[1] block rounded-full border transition duration-150 group-focus-visible:ring-2 group-focus-visible:ring-white/28 ${markerVisualClass(marker)} ${
                  isPrimary ? 'ring-2 ring-[var(--mimir-accent)] ring-offset-2 ring-offset-black' : ''
                } ${
                  isSelected
                    ? 'h-10 w-10 scale-110 ring-2 ring-white/24 brightness-110'
                    : isHovered
                      ? 'h-9 w-9 scale-110'
                      : String(marker.type || '').toLowerCase() === 'impact_contact' || isPrimary
                        ? 'h-8 w-8'
                        : 'h-7 w-7'
                }`}
              />
              <span
                className={`mt-7 max-w-[132px] truncate rounded-full border px-2.5 py-1 text-[11px] font-semibold shadow-[0_10px_26px_rgba(0,0,0,0.22)] transition ${
                  isSelected
                    ? 'border-white/20 bg-white/[0.12] text-[var(--mimir-text)]'
                    : 'border-white/[0.08] bg-black/26 text-[var(--mimir-text-muted)] group-hover:bg-white/[0.075] group-hover:text-[var(--mimir-text)]'
                }`}
              >
                {markerLabel(marker, incident)}
              </span>
              <span className="mt-1 text-[11px] font-medium text-[var(--mimir-text-subtle)]">
                {isTimed ? formatTime(time) : '--:--'}
              </span>
              <span className="sr-only">{markerLabel(marker, incident)}</span>

              {isHovered && (
                <span className="pointer-events-none absolute bottom-[108px] left-1/2 z-10 block w-56 -translate-x-1/2 rounded-lg bg-[var(--mimir-surface-soft)] p-3 text-left shadow-[0_18px_50px_rgba(0,0,0,0.42)]">
                  <span className="block text-[11px] font-semibold text-[var(--mimir-text-subtle)]">
                    {markerLabel(marker, incident)} - {time === null ? 'time unavailable' : formatTime(time)}
                  </span>
                  <span className="mt-2 block text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                    {markerDescription(marker, incident)}
                  </span>
                </span>
              )}
            </button>
          )
        })}
        <div className="absolute bottom-0 left-0 text-[11px] text-[var(--mimir-text-subtle)]">0:00</div>
        <div className="absolute bottom-0 right-0 text-[11px] text-[var(--mimir-text-subtle)]">{formatTime(knownDuration)}</div>
      </div>

      <div className="mt-3 rounded-xl bg-white/[0.018] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.015)]">
        {selectedMarker ? (
          <div className="relative flex flex-wrap items-start gap-4 pl-4">
            <div className={`absolute bottom-1 left-0 top-1 w-1 rounded-full ${markerAccentForMarker(selectedMarker)}`} />
            <div className="min-w-[74px]">
              <div className="text-[10px] font-medium text-[var(--mimir-text-subtle)]">Marker</div>
              <div className="mt-1 text-[18px] font-semibold text-[var(--mimir-text)]">
                {formatTimecode(markerTime(selectedMarker))}
              </div>
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[14px] font-semibold text-[var(--mimir-text)]">{markerLabel(selectedMarker, incident)}</span>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${severityClass(selectedMarker.severity)}`}>
                  {markerSeverityCopy(selectedMarker.severity)}
                </span>
              </div>
              <p className="mt-1.5 text-[12px] leading-5 text-[var(--mimir-text-muted)]">{markerDescription(selectedMarker, incident)}</p>
            </div>
          </div>
        ) : (
          <div className="text-[13px] leading-6 text-[var(--mimir-text-muted)]">Select a marker to inspect the moment.</div>
        )}
      </div>
    </div>
  )
}

function DetailsPanel({
  incident,
  session,
  severityResolution,
}: {
  incident: MimirIncident
  session?: MimirSession
  severityResolution: SeverityResolution
}) {
  const evidence = safeTextList(incident.evidence)
  const impactReasons = safeTextList(incident.impact_reasons)
  const contactReasons = safeTextList(incident.contact_reasons)
  const calmPersonNear = calmerPersonNearWording(incident)
  const supportedContact = hasSupportedContactEvidence(incident)
  const supportedImpact = hasSupportedImpactEvidence(incident)
  const showImpactReasons = impactReasons.length > 0 && (!calmPersonNear || supportedImpact)
  const showContactReasons = contactReasons.length > 0 && (!calmPersonNear || supportedContact)
  const displayEvidence = calmPersonNear ? ['No clear contact detected'] : evidence
  const local = localEvidence(incident)
  const ai = aiEvidence(incident)
  const debug = classificationDebug(incident)
  const aiEvidenceItems = safeTextList(ai.evidence)
  const aiConcernItems = safeTextList(ai.concerns).concat(safeTextList(incident.ai_concerns))
  const aiWarning = aiQualityWarning(incident)
  const resolverReasons = severityReasonList(incident)
  const severityCapReason = safeText(debug.severity_cap_reason || incident.severity_cap_reason, '')
  const severityFloorReason = safeText(debug.severity_floor_reason, '')
  const reviewBadgeTone = aiReviewed(incident)
    ? 'border-sky-200/18 bg-sky-300/10 text-sky-100/88'
    : 'border-white/[0.07] bg-white/[0.035] text-[var(--mimir-text-muted)]'
  const severity = severityResolution.displaySeverity
  const reviewCopy =
    severity === 'IMPORTANT'
      ? 'Possible impact/contact detected.'
      : severity === 'REVIEW'
        ? 'Uncertain activity worth checking.'
        : 'No concerning evidence found.'
  const keyReasons = [
    ...resolverReasons,
    ...displayEvidence,
    ...(showImpactReasons ? impactReasons : []),
    ...(showContactReasons ? contactReasons : []),
    severityCapReason,
    severityFloorReason,
  ].filter(Boolean)
  const localSummary = [
    `Impact: ${calmPersonNear && !supportedImpact ? 'No clear impact detected' : incident.impact_level || evidenceMetricValue(local, 'impact_level')}`,
    `Contact: ${contactLevelCopy(incident)}`,
    `Motion: ${evidenceMetricValue(local, 'max_motion_score')}`,
    `Person detected: ${evidenceMetricValue(local, 'person_detected')}`,
    `Vehicle detected: ${evidenceMetricValue(local, 'vehicle_detected')}`,
  ].filter(item => !item.endsWith('Not provided') && !item.endsWith(''))
  const filePaths = {
    video_path: incident.video_path || null,
    source_video: incident.source_video || null,
    original_source_video: incident.original_source_video || null,
    library_video_path: incident.library_video_path || null,
    trash_video_path: incident.trash_video_path || null,
    thumbnail: incident.thumbnail || null,
    hero_thumbnail: incident.hero_thumbnail || null,
    contact_sheet: incident.contact_sheet || null,
  }
  const backendMetadata = {
    schema_version: session?.schema_version,
    scanner_version: session?.scanner_version,
    core_version: session?.core_version,
    core_build_id: session?.core_build_id,
    backend_runtime: session?.backend_runtime,
    session_created_at: session?.session_created_at,
    output_path: session?.output_path,
    feature_flags: session?.feature_flags,
  }

  return (
    <aside className="mb-3 rounded-2xl border border-white/[0.04] bg-white/[0.016] p-4 shadow-[0_12px_34px_rgba(0,0,0,0.14)]">
      <div className="mb-4 flex flex-wrap gap-2">
        <span className={`rounded-full border px-3 py-1 text-[12px] font-semibold ${severityClass(severityResolution.displaySeverity)}`}>
          {severityCopy(severityResolution.displaySeverity)}
        </span>
        {severityResolution.isManualOverride && (
          <span className="rounded-full border border-white/[0.07] bg-white/[0.035] px-3 py-1 text-[12px] font-medium text-[var(--mimir-text-muted)]">
            Changed by you
          </span>
        )}
        <span className={`rounded-full border px-3 py-1 text-[12px] font-medium ${reviewBadgeTone}`}>
          {reviewBadgeCopy(incident)}
        </span>
      </div>

      <div className="rounded-xl bg-black/12 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.018)]">
        <div className="text-[12px] font-semibold text-[var(--mimir-text-subtle)]">
          Mimir review
        </div>
        <p className="mt-3 text-[15px] font-medium leading-6 text-[var(--mimir-text)]">
          {reviewCopy}
        </p>
        <p className="mt-1.5 text-[12px] leading-5 text-[var(--mimir-text-subtle)]">
          Based on local evidence.
        </p>
        {importantNotAppliedNote(incident) && (
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.026] px-3 py-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
            Important was not applied because no contact or impact evidence was found.
          </div>
        )}
      </div>

      <details className="mt-3 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[13px] font-semibold text-[var(--mimir-text)]">
          Why?
        </summary>
        <div className="mt-4 grid gap-4">
          {keyReasons.length > 0 && (
            <div>
              <div className="mb-2 text-[12px] font-medium text-[var(--mimir-text-subtle)]">
                Key reasons
              </div>
              <ul className="space-y-2">
                {keyReasons.slice(0, 8).map((item, index) => (
                  <li key={`${incident.id}-why-${index}`} className="flex gap-2 text-[13px] leading-5 text-[var(--mimir-text-muted)]">
                    <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-white/35" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {localSummary.length > 0 && (
            <div className="rounded-lg bg-white/[0.012] px-3">
              <DetailMetric label="Mimir result" value={severityCopy(severityResolution.mimirSeverity)} />
              <DetailMetric label="Local evidence" value={localSummary.join(' / ')} />
            </div>
          )}

          {experimentalAiUsed(incident) && (
            <div className="rounded-lg border border-sky-200/10 bg-sky-300/[0.035] p-3">
              <div className="text-[12px] font-semibold text-sky-50/85">AI second opinion</div>
              <div className="mt-2 grid gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                <div>Model: {aiModelName(incident) || 'Not provided'}</div>
                {aiSceneType(incident) && <div>Scene: {aiSceneType(incident)}</div>}
                <div>Suggested severity: {aiRecommendedSeverity(incident)}</div>
                <div>Confidence: {aiConfidenceCopy(incident)}</div>
                {aiWarning && <div className="text-amber-50/78">{aiWarning}</div>}
              </div>
              {aiEvidenceItems.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {aiEvidenceItems.map((item, index) => (
                    <li key={`${incident.id}-ai-evidence-${index}`} className="flex gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-sky-200/45" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
              {aiConcernItems.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {aiConcernItems.map((item, index) => (
                    <li key={`${incident.id}-ai-concern-${index}`} className="flex gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-200/45" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </details>

      <details className="mt-3 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[13px] font-medium text-[var(--mimir-text-muted)]">
          Technical details
        </summary>
        <div className="mt-4 grid gap-3">
          <DetailMetric label="Incident ID" value={safeText(incident.id, 'Not provided')} />
          <DetailMetric label="Event type" value={eventDisplayTitle(incident)} />
          <DetailMetric label="Mimir result" value={severityCopy(severityResolution.mimirSeverity)} />
          <DetailMetric label="Displayed status" value={severityCopy(severityResolution.displaySeverity)} />
          <DetailMetric label="Manual override" value={yesNo(severityResolution.isManualOverride)} />
          <DetailMetric label="Score" value={formatNumber(incident.score)} />
          <DetailMetric label="Persons" value={formatNumber(incident.persons)} />
          <DetailMetric label="Vehicles" value={formatNumber(incident.vehicles)} />
          <DetailMetric label="Active frames" value={formatNumber(incident.active_frames)} />
          <DetailMetric label="Motion score" value={formatNumber(incident.max_motion_score)} />
          <DetailMetric label="Impact score" value={formatNumber(incident.impact_score)} />
          <DetailMetric
            label="Source event timestamp"
            value={formatDateTime(sourceEventTimestamp(incident)) || 'Not provided'}
          />
          <TechnicalJsonBlock label="Backend metadata" value={backendMetadata} />
          <TechnicalJsonBlock label="File paths" value={filePaths} />
          <TechnicalJsonBlock label="Classification debug" value={incident.classification_debug} />
          <TechnicalJsonBlock label="Local evidence JSON" value={incident.local_evidence ?? incident.local_evidence_summary} />
          {experimentalAiUsed(incident) && (
            <>
              <DetailMetric label="AI parse error" value={yesNo(incident.ai_parse_error)} />
              <DetailMetric label="AI skipped reason" value={safeText(incident.ai_review_skipped_reason, 'Not provided')} />
              <TechnicalJsonBlock label="AI raw result" value={incident.ai_evidence_review ?? incident.ai_raw_response ?? incident.ai_evidence} />
            </>
          )}
        </div>
      </details>
    </aside>
  )
}

function AiFeedbackPanel({
  selectedFeedback,
  notes,
  includeVideo,
  busy,
  message,
  error,
  onSelectFeedback,
  onNotesChange,
  onIncludeVideoChange,
  onSubmit,
}: {
  selectedFeedback: AiFeedbackChoice | ''
  notes: string
  includeVideo: boolean
  busy: boolean
  message: string
  error: string
  onSelectFeedback: (value: AiFeedbackChoice) => void
  onNotesChange: (value: string) => void
  onIncludeVideoChange: (value: boolean) => void
  onSubmit: () => void
}) {
  return (
    <div className="mt-4 rounded-xl bg-black/10 p-3.5">
      <div className="mb-3">
        <div className="text-[12px] font-semibold text-[var(--mimir-text)]">AI feedback</div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {feedbackChoices.map(choice => (
          <button
            key={choice}
            type="button"
            onClick={() => onSelectFeedback(choice)}
            disabled={busy}
            className={`min-h-9 rounded-lg border px-2.5 py-2 text-[11px] font-semibold transition disabled:cursor-wait disabled:opacity-60 ${
              selectedFeedback === choice
                ? 'border-white/24 bg-white/[0.095] text-[var(--mimir-text)]'
                : 'border-white/[0.07] bg-white/[0.025] text-[var(--mimir-text-muted)] hover:bg-white/[0.055] hover:text-[var(--mimir-text)]'
            }`}
          >
            {choice}
          </button>
        ))}
      </div>

      <textarea
        value={notes}
        onChange={event => onNotesChange(event.target.value)}
        disabled={busy}
        rows={3}
        className="mt-3 w-full resize-none rounded-lg border border-white/[0.08] bg-black/18 p-3 text-[13px] leading-5 text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:border-white/18 disabled:cursor-wait disabled:opacity-60"
        placeholder="Notes"
      />

      <label className="mt-3 flex items-center gap-2 text-[12px] text-[var(--mimir-text-muted)]">
        <input
          type="checkbox"
          checked={includeVideo}
          onChange={event => onIncludeVideoChange(event.target.checked)}
          disabled={busy}
          className="h-4 w-4 accent-white disabled:cursor-wait"
        />
        Include video clip
      </label>

      <button
        type="button"
        onClick={onSubmit}
        disabled={busy || !selectedFeedback}
        className="mt-3 h-9 w-full rounded-lg bg-[var(--mimir-text)] px-3 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-55"
      >
        <span className="inline-flex items-center gap-2">
          {busy && <SmallSpinner />}
          Send feedback
        </span>
      </button>

      {message && (
        <div className="mt-3 rounded-lg border border-white/[0.075] bg-white/[0.035] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          {message}
        </div>
      )}
      {error && (
        <div className="mt-3 rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/86">
          {error}
        </div>
      )}
    </div>
  )
}

function TrainingContributionPanel({ incident, session }: { incident: MimirIncident; session?: MimirSession }) {
  const [recordedBy, setRecordedBy] = useState('')
  const [rightsBasis, setRightsBasis] = useState<'owned' | 'explicit_permission' | 'public_license'>('owned')
  const [permissionReference, setPermissionReference] = useState('')
  const [permissionRecord, setPermissionRecord] = useState('')
  const [rightsConfirmed, setRightsConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setRecordedBy('')
    setRightsBasis('owned')
    setPermissionReference('')
    setPermissionRecord('')
    setRightsConfirmed(false)
    setMessage('')
    setError('')
  }, [incident.id])

  const choosePermissionRecord = async () => {
    const selected = await openDialog({ multiple: false, directory: false, title: 'Choose permission record' })
    if (typeof selected === 'string') {
      setPermissionRecord(selected)
    }
  }

  const exportPackage = async () => {
    if (!rightsConfirmed || !recordedBy.trim() || !permissionReference.trim()) {
      setError('Confirm your rights and complete the consent details first.')
      return
    }
    const suggestedName = `${safeText(incident.source_stem, incidentActionId(incident))}.mimir-dataset.age`
      .replace(/[<>:"/\\|?*]+/g, '_')
    const destination = await saveDialog({
      title: 'Export encrypted Mimir training package',
      defaultPath: suggestedName,
      filters: [{ name: 'Mimir encrypted dataset', extensions: ['age'] }],
    })
    if (!destination) {
      return
    }
    const outputPath = destination.toLowerCase().endsWith('.mimir-dataset.age')
      ? destination
      : destination.toLowerCase().endsWith('.age')
        ? destination.slice(0, -4) + '.mimir-dataset.age'
        : destination + '.mimir-dataset.age'
    setBusy(true)
    setError('')
    setMessage('Encrypting selected footage locally...')
    try {
      const result = await invoke<TrainingContributionResult>('export_training_contribution', {
        sessionPath: session?.session_archive_path || session?.output_path || null,
        incidentId: incidentActionId(incident),
        outputPath,
        recordedBy: recordedBy.trim(),
        rightsBasis,
        permissionReference: permissionReference.trim(),
        independentPermissionRecord: permissionRecord || null,
      })
      setMessage(result.message)
      setRightsConfirmed(false)
    } catch (value) {
      setMessage('')
      setError(actionErrorMessage(value))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 grid gap-3">
      <p className="text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        Export this incident only after confirming you own the footage or have permission to use it for model development. Nothing uploads automatically.
      </p>
      <div className="rounded-lg border border-[rgba(157,183,170,0.16)] bg-[var(--mimir-accent-soft)] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        <span className="font-semibold text-[var(--mimir-text)]">Where this goes:</span> contributed clips are
        human-annotated and counted toward a review set that a future detector is trained and tested against.
        Detection never changes on its own -- an improved model only ships after it is measured against clips it
        never trained on and a person approves it.
      </div>
      <input
        value={recordedBy}
        onChange={event => setRecordedBy(event.target.value)}
        placeholder="Your name"
        className="h-10 rounded-lg border border-white/[0.08] bg-black/18 px-3 text-[13px] text-[var(--mimir-text)] outline-none focus:border-white/18"
      />
      <select
        value={rightsBasis}
        onChange={event => setRightsBasis(event.target.value as typeof rightsBasis)}
        className="h-10 rounded-lg border border-white/[0.08] bg-[var(--mimir-bg-depth)] px-3 text-[13px] text-[var(--mimir-text)] outline-none focus:border-white/18"
      >
        <option value="owned">I recorded and own this footage</option>
        <option value="explicit_permission">I have explicit permission</option>
        <option value="public_license">A public license permits this use</option>
      </select>
      <input
        value={permissionReference}
        onChange={event => setPermissionReference(event.target.value)}
        placeholder="Ownership, permission, or license reference"
        className="h-10 rounded-lg border border-white/[0.08] bg-black/18 px-3 text-[13px] text-[var(--mimir-text)] outline-none focus:border-white/18"
      />
      <button
        type="button"
        onClick={() => void choosePermissionRecord()}
        className="h-9 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] hover:bg-white/[0.05] hover:text-[var(--mimir-text)]"
      >
        {permissionRecord ? 'Permission record selected' : 'Attach permission record (optional)'}
      </button>
      <label className="flex items-start gap-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
        <input
          type="checkbox"
          checked={rightsConfirmed}
          onChange={event => setRightsConfirmed(event.target.checked)}
          className="mt-0.5 h-4 w-4 accent-white"
        />
        I confirm these rights apply to this selected incident and its grouped camera angles.
      </label>
      <button
        type="button"
        onClick={() => void exportPackage()}
        disabled={busy || !rightsConfirmed}
        className="h-10 rounded-lg bg-[var(--mimir-text)] px-3 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="inline-flex items-center gap-2">{busy && <SmallSpinner />}Export encrypted package</span>
      </button>
      {message && <div className="text-[12px] leading-5 text-[var(--mimir-text-muted)]">{message}</div>}
      {error && <div className="text-[12px] leading-5 text-red-100/85">{error}</div>}
    </div>
  )
}

function ReviewActionsPanel({
  incident,
  session,
  currentSeverity,
  mimirSeverity,
  isManualOverride,
  busyAction,
  actionMessage,
  actionError,
  actionDetails,
  feedbackChoice,
  feedbackNotes,
  feedbackIncludeVideo,
  feedbackMessage,
  feedbackError,
  noteDraft,
  isEditingNote,
  onFeedbackChoiceChange,
  onFeedbackNotesChange,
  onFeedbackIncludeVideoChange,
  onSubmitFeedback,
  onNoteDraftChange,
  onEditNote,
  onCancelNote,
  onSaveNote,
  onSetStatus,
  onMoveToLibrary,
  onConfirmDelete,
  onRestoreFromTrash,
  onOpenFiles,
  onExportReport,
}: {
  incident: MimirIncident
  session?: MimirSession
  currentSeverity: SeverityGroup
  mimirSeverity: SeverityGroup
  isManualOverride: boolean
  busyAction: IncidentAction | null
  actionMessage: string
  actionError: string
  actionDetails: string
  feedbackChoice: AiFeedbackChoice | ''
  feedbackNotes: string
  feedbackIncludeVideo: boolean
  feedbackMessage: string
  feedbackError: string
  noteDraft: string
  isEditingNote: boolean
  onFeedbackChoiceChange: (value: AiFeedbackChoice) => void
  onFeedbackNotesChange: (value: string) => void
  onFeedbackIncludeVideoChange: (value: boolean) => void
  onSubmitFeedback: () => void
  onNoteDraftChange: (value: string) => void
  onEditNote: () => void
  onCancelNote: () => void
  onSaveNote: () => void
  onSetStatus: (status: SeverityGroup) => void
  onMoveToLibrary: () => void
  onConfirmDelete: () => void
  onRestoreFromTrash: () => void
  onOpenFiles: () => void
  onExportReport: () => void
}) {
  const disabled = busyAction !== null
  const [showActionDetails, setShowActionDetails] = useState(false)
  const currentStorageState = storageState(incident)
  const storageBadgeTone =
    currentStorageState.includes('Trash')
      ? 'border-red-300/20 bg-red-500/10 text-red-100/85'
      : currentStorageState.includes('Library')
        ? 'border-emerald-300/18 bg-emerald-400/10 text-emerald-100/85'
        : currentStorageState === 'Missing file'
          ? 'border-amber-300/20 bg-amber-400/10 text-amber-100/85'
          : 'border-white/[0.08] bg-white/[0.035] text-[var(--mimir-text-muted)]'

  return (
    <section className="rounded-2xl border border-white/[0.04] bg-white/[0.016] p-4 shadow-[0_12px_34px_rgba(0,0,0,0.14)]">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-[12px] font-semibold text-[var(--mimir-text-subtle)]">
            Status
          </div>
          {isManualOverride && (
            <div className="mt-1 text-[11px] font-medium text-[var(--mimir-text-subtle)]">
              Changed by you
            </div>
          )}
        </div>
        <span className={`rounded-full border px-3 py-1 text-[11px] font-semibold ${storageBadgeTone}`}>
          {currentStorageState === 'Mimir Library'
            ? 'In Mimir Library'
            : currentStorageState === 'Mimir Trash'
              ? 'In Mimir Trash'
              : currentStorageState}
        </span>
      </div>

      <div>
        <div className="grid grid-cols-3 gap-2">
          {(['IGNORE', 'REVIEW', 'IMPORTANT'] as const).map(status => {
            const actionKey = `set_status_${status}` as IncidentAction
            return (
              <button
                key={status}
                onClick={() => onSetStatus(status)}
                disabled={disabled || currentSeverity === status}
                className={`h-10 rounded-lg border px-3 text-[12px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${actionButtonTone(
                  status,
                  currentSeverity === status,
                )}`}
              >
                <span className="inline-flex items-center gap-2">
                  {busyAction === actionKey && <SmallSpinner />}
                  {severityCopy(status)}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-4 grid gap-2">
        {isManualOverride && (
          <div className="rounded-lg bg-white/[0.02] px-3 py-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
            Mimir result: {severityCopy(mimirSeverity)}
          </div>
        )}
        <button
          onClick={onOpenFiles}
          disabled={disabled}
          className="h-10 rounded-lg border border-white/[0.075] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-55"
        >
          Files
        </button>
        <button
          onClick={onExportReport}
          disabled={disabled}
          className="h-10 rounded-lg border border-white/[0.075] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'export_report' && <SmallSpinner />}
            Export report
          </span>
        </button>
        {incident.user_deleted || currentStorageState.includes('Trash') ? (
          <button
            onClick={onRestoreFromTrash}
            disabled={disabled}
            className="h-10 rounded-lg border border-white/[0.085] bg-white/[0.035] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.065] disabled:cursor-not-allowed disabled:opacity-55"
          >
            <span className="inline-flex items-center gap-2">
              {busyAction === 'restore_from_trash' && <SmallSpinner />}
              Restore from Mimir Trash
            </span>
          </button>
        ) : <button
          onClick={onMoveToLibrary}
          disabled={disabled || incident.user_deleted}
          className="h-10 rounded-lg border border-white/[0.085] bg-white/[0.035] px-3 text-[12px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.065] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'move_to_library' && <SmallSpinner />}
            Move to Mimir Library
          </span>
        </button>}
        {!incident.user_deleted && !currentStorageState.includes('Trash') && <button
          onClick={onConfirmDelete}
          disabled={disabled || incident.user_deleted}
          className="h-10 rounded-lg border border-red-300/14 bg-red-500/[0.045] px-3 text-[12px] font-semibold text-red-100/82 transition hover:bg-red-500/[0.075] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="inline-flex items-center gap-2">
            {busyAction === 'move_to_trash' && <SmallSpinner />}
            Move to Mimir Trash
          </span>
        </button>}
      </div>

      <details className="mt-4 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]">
          Feedback
        </summary>
        <AiFeedbackPanel
          selectedFeedback={feedbackChoice}
          notes={feedbackNotes}
          includeVideo={feedbackIncludeVideo}
          busy={busyAction === 'save_feedback'}
          message={feedbackMessage}
          error={feedbackError}
          onSelectFeedback={onFeedbackChoiceChange}
          onNotesChange={onFeedbackNotesChange}
          onIncludeVideoChange={onFeedbackIncludeVideoChange}
          onSubmit={onSubmitFeedback}
        />
      </details>

      <details className="mt-3 rounded-xl border border-white/[0.035] bg-transparent p-3.5">
        <summary className="cursor-pointer text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]">
          Export for Mimir training
        </summary>
        <TrainingContributionPanel incident={incident} session={session} />
      </details>

      <div className="mt-4 rounded-xl bg-black/10 p-3.5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="text-[12px] font-semibold text-[var(--mimir-text)]">Note</div>
          <button
            onClick={onEditNote}
            disabled={disabled}
            className="h-8 rounded-lg bg-white/[0.035] px-2.5 text-[11px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.065] hover:text-[var(--mimir-text)] disabled:cursor-wait disabled:opacity-60"
            title="Edit note"
          >
            Edit
          </button>
        </div>
        {isEditingNote ? (
          <div>
            <textarea
              value={noteDraft}
              onChange={event => onNoteDraftChange(event.target.value)}
              rows={4}
              className="w-full resize-none rounded-lg border border-white/[0.08] bg-black/18 p-3 text-[13px] leading-5 text-[var(--mimir-text)] outline-none transition placeholder:text-[var(--mimir-text-subtle)] focus:border-white/18"
              placeholder="Add your review note..."
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={onCancelNote}
                disabled={disabled}
                className="h-9 rounded-lg bg-white/[0.035] px-3 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={onSaveNote}
                disabled={disabled}
                className="h-9 rounded-lg bg-[var(--mimir-text)] px-3 text-[12px] font-semibold text-black transition hover:bg-white disabled:cursor-wait disabled:opacity-60"
              >
                <span className="inline-flex items-center gap-2">
                  {busyAction === 'save_note' && <SmallSpinner />}
                  Save note
                </span>
              </button>
            </div>
          </div>
        ) : (
          <p className="text-[13px] leading-6 text-[var(--mimir-text-muted)]">
            {incident.user_note || 'No note yet.'}
          </p>
        )}
      </div>

      {actionMessage && (
        <div className="mt-4 rounded-lg border border-white/[0.075] bg-white/[0.035] p-3 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          <span className="inline-flex items-center gap-2">
            {(busyAction === 'move_to_library' || busyAction === 'move_to_trash') && <SmallSpinner />}
            {actionMessage}
          </span>
        </div>
      )}
      {actionError && (
        <div className="mt-4 rounded-lg border border-red-300/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-100/86">
          <div>{actionError}</div>
          {actionDetails && (
            <button
              type="button"
              onClick={() => setShowActionDetails(value => !value)}
              className="mt-2 rounded-md border border-red-100/18 bg-black/18 px-2.5 py-1 text-[11px] font-semibold text-red-50/86 transition hover:bg-black/30"
            >
              {showActionDetails ? 'Hide details' : 'Show details'}
            </button>
          )}
          {showActionDetails && actionDetails && (
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-black/30 p-3 text-[11px] leading-4 text-red-50/75">
              {actionDetails}
            </pre>
          )}
        </div>
      )}
    </section>
  )
}

export function IncidentViewerScreen({
  incident,
  session,
  severityResolution,
  onBack,
  onReloadSession,
  onIncidentUpdated,
  onManualStatusChange,
}: IncidentViewerScreenProps) {
  const title = eventDisplayTitle(incident)
  const timestamp = formatDateTime(sourceEventTimestamp(incident))
  const sourceLabel =
    sourceFilename(cleanPath(incident.source_video) || cleanPath(incident.original_source_video) || cleanPath(incident.video_path)) ||
    'Source filename not provided'
  const isGenericBestEffort = incident.filename_timestamp_detected === false
  const attemptedVideoPath = attemptedVideoPathForIncident(incident)
  const viewerRef = useRef<HTMLElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [seekRequest, setSeekRequest] = useState<ViewerSeekRequest | null>(null)
  const [playbackRequest, setPlaybackRequest] = useState<ViewerPlaybackRequest | null>(null)
  const [busyAction, setBusyAction] = useState<IncidentAction | null>(null)
  const [actionMessage, setActionMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [actionDetails, setActionDetails] = useState('')
  const [showLibraryConfirm, setShowLibraryConfirm] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showFilesDrawer, setShowFilesDrawer] = useState(false)
  const [isEditingNote, setIsEditingNote] = useState(false)
  const [noteDraft, setNoteDraft] = useState(incident.user_note ?? '')
  const [feedbackChoice, setFeedbackChoice] = useState<AiFeedbackChoice | ''>('')
  const [feedbackNotes, setFeedbackNotes] = useState('')
  const [feedbackIncludeVideo, setFeedbackIncludeVideo] = useState(false)
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [feedbackError, setFeedbackError] = useState('')
  const [showKeyMoments, setShowKeyMoments] = useState(true)
  const markers = useMemo(() => deriveReviewTimelineMarkers(incident, duration), [incident, duration])

  useEffect(() => {
    setCurrentTime(0)
    setDuration(0)
    setSeekRequest(null)
    setPlaybackRequest(null)
    setActionMessage('')
    setActionError('')
    setActionDetails('')
    setShowLibraryConfirm(false)
    setShowDeleteConfirm(false)
    setShowFilesDrawer(false)
    setIsEditingNote(false)
    setNoteDraft(incident.user_note ?? '')
    setFeedbackChoice('')
    setFeedbackNotes('')
    setFeedbackIncludeVideo(false)
    setFeedbackMessage('')
    setFeedbackError('')
    setShowKeyMoments(true)
    viewerRef.current?.focus()
  }, [incident.id])

  const refreshCurrentIncident = async (fallbackMessage: string) => {
    const refreshedSession = await onReloadSession()
    const refreshedIncident = refreshedSession ? findIncident(refreshedSession, incident) : null

    if (refreshedIncident) {
      onIncidentUpdated(refreshedIncident)
    }

    setActionMessage(fallbackMessage)
  }

  const updateManualStatus = (status: SeverityGroup) => {
    onManualStatusChange(status)
    setActionError('')
    setActionDetails('')
    setActionMessage(`Status changed to ${severityCopy(status)}.`)
  }

  const runCoreV2StorageAction = async (action: 'move_to_library' | 'move_to_trash' | 'restore_from_trash') => {
    const busyKey: IncidentAction = action
    const successMessage = action === 'move_to_library'
      ? 'Moved to Mimir Library'
      : action === 'restore_from_trash'
        ? 'Restored from Mimir Trash'
        : 'Moved to Mimir Trash'

    setBusyAction(busyKey)
    setActionError('')
    setActionDetails('')
    setActionMessage('Moving clips...')

    try {
      const result = await invoke<StorageActionResult>('run_core_v2_storage_action', {
        incidentId: incidentActionId(incident),
        action,
      })
      const report = parseStorageActionReport(result.report_json)
      const status = storageActionStatus(report)
      const details = storageActionDetails(result.report_json, result)

      await refreshCurrentIncident(status.partial || status.failed ? '' : successMessage)

      if (status.partial) {
        setActionMessage('')
        setActionError('The file action could not finish cleanly. Mimir kept the recovery details below.')
        setActionDetails(details)
      } else if (status.failed || !result.ok) {
        setActionMessage('')
        setActionError(result.message || 'Mimir could not complete this file action.')
        setActionDetails(details)
      } else {
        setActionError('')
        setActionDetails('')
        setActionMessage(successMessage)
      }
    } catch (error) {
      setActionMessage('')
      setActionError(actionErrorMessage(error))
      setActionDetails('')
    } finally {
      setBusyAction(null)
    }
  }

  const exportReport = async () => {
    setBusyAction('export_report')
    setActionError('')
    setActionDetails('')
    setActionMessage('Building report...')

    try {
      const severityLabel = severityCopy(severityResolution.displaySeverity)
      const evidenceLines = (incident.evidence ?? []).filter((line): line is string => typeof line === 'string' && line.trim().length > 0)
      const impactContactLines = [
        incident.impact_level ? `Impact level: ${incident.impact_level}` : '',
        incident.contact_level ? `Contact level: ${incident.contact_level}` : '',
        ...(incident.impact_reasons ?? []),
        ...(incident.contact_reasons ?? []),
      ].filter(Boolean)
      const momentLines = markers.map(marker => {
        const seconds = typeof marker.time_sec === 'number' ? Math.max(0, Math.round(marker.time_sec)) : null
        const stamp = seconds !== null ? `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` : '--:--'
        const label = marker.label || 'Moment'
        const description = marker.description || marker.reason || ''
        return description ? `${stamp} - ${label}: ${description}` : `${stamp} - ${label}`
      })
      const cameraLines = normalizeCameraClips(incident).map(clip => {
        const camera = clip?.camera || 'Unknown camera'
        const duration = typeof clip?.duration_sec === 'number' ? ` (${Math.round(clip.duration_sec)}s)` : ''
        return `${camera}${duration}`
      })

      const images = [
        { label: 'Key frame', path: cleanPath(incident.hero_thumbnail) },
        { label: 'Contact sheet', path: cleanPath(incident.contact_sheet) },
        { label: 'Before', path: cleanPath(incident.start_frame_image) },
        { label: 'After', path: cleanPath(incident.end_frame_image) },
      ].filter(image => image.path)

      const reportPath = await invoke<string>('export_incident_report', {
        incidentId: incidentActionId(incident),
        title,
        severityLabel,
        subtitle: `${timestamp || 'Timestamp unavailable'} - ${sourceLabel}`,
        generatedAt: new Date().toLocaleString(),
        sections: [
          { heading: 'Summary', lines: incident.summary ? [incident.summary] : [] },
          { heading: 'Local evidence', lines: evidenceLines.length > 0 ? evidenceLines : ['No local evidence notes were recorded.'] },
          { heading: 'Impact/contact signals', lines: impactContactLines },
          { heading: 'Key moments', lines: momentLines },
          { heading: 'Cameras in this incident', lines: cameraLines },
        ],
        images,
      })

      setActionError('')
      setActionDetails('')
      setActionMessage(`Report saved and opened: ${reportPath}`)
    } catch (error) {
      setActionMessage('')
      setActionError(actionErrorMessage(error))
      setActionDetails('')
    } finally {
      setBusyAction(null)
    }
  }

  const saveNote = async () => {
    setBusyAction('save_note')
    setActionError('')
    setActionDetails('')
    setActionMessage('')

    try {
      const result = await invoke<ClipActionResult>('save_incident_note', {
        incidentId: incidentActionId(incident),
        note: noteDraft,
      })

      await refreshCurrentIncident(result.message || 'Note saved.')
      setIsEditingNote(false)
    } catch (error) {
      setActionError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const saveActualMoment = async (timeSec: number) => {
    setBusyAction('save_key_moment')
    setActionError('')
    setActionDetails('')
    try {
      const result = await invoke<ClipActionResult>('save_key_moment_correction', {
        incidentId: incidentActionId(incident),
        timeSec,
      })
      await refreshCurrentIncident(result.message || 'Actual moment saved.')
    } catch (error) {
      setActionMessage('')
      setActionError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const saveFeedback = async () => {
    if (!feedbackChoice) {
      setFeedbackError('Choose a feedback label first.')
      return
    }

    setBusyAction('save_feedback')
    setFeedbackError('')
    setFeedbackMessage('')
    setActionError('')
    setActionDetails('')

    try {
      const videoPath = currentVideoPath(incident)
      const result = await invoke<IncidentFeedbackResult>('save_incident_feedback', {
        feedback: incidentFeedbackPayload(incident, feedbackChoice, feedbackNotes, feedbackIncludeVideo, session),
        includeVideo: feedbackIncludeVideo,
        videoPath: feedbackIncludeVideo ? videoPath : null,
      })

      const subject = `Mimir feedback: ${feedbackChoice} - ${eventDisplayTitle(incident)}`
      const attachmentNote = feedbackIncludeVideo
        ? 'A copy of the video clip was saved alongside the feedback file - please attach both.'
        : 'Please attach the feedback file below (no video was included).'
      const body = [
        `Feedback: ${feedbackChoice}`,
        feedbackNotes.trim() ? `Notes: ${feedbackNotes.trim()}` : '',
        `Incident: ${eventDisplayTitle(incident)} (${incidentActionId(incident)})`,
        '',
        attachmentNote,
        `File location: ${result.feedback_file || result.feedback_folder}`,
      ].filter(Boolean).join('\n')

      // mailto: only works if the OS has a configured desktop mail client, which
      // most people don't have anymore (webmail in a browser is the norm). Clipboard
      // copy is the one step here that works regardless of what email the user
      // actually uses, so it's the primary path -- mailto is just a bonus if it works.
      let copiedToClipboard = false
      try {
        await navigator.clipboard.writeText(`To: ${FEEDBACK_EMAIL}\nSubject: ${subject}\n\n${body}`)
        copiedToClipboard = true
      } catch {
        // Clipboard access can be denied in some contexts; not fatal.
      }

      try {
        await invoke<void>('open_containing_folder', { path: result.feedback_file || result.feedback_folder })
      } catch {
        // Non-fatal: the feedback was already saved successfully.
      }

      try {
        window.location.href = `mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
      } catch {
        // Non-fatal: no default mail app configured is common and expected.
      }

      setFeedbackMessage(
        copiedToClipboard
          ? `${result.message || 'Feedback saved.'} Copied the email text to your clipboard and opened the file's folder -- paste into an email to ${FEEDBACK_EMAIL} and attach the file.`
          : `${result.message || 'Feedback saved.'} Opened the file's folder -- email it to ${FEEDBACK_EMAIL}.`,
      )
      setFeedbackNotes('')
      setFeedbackIncludeVideo(false)
    } catch (error) {
      setFeedbackError(actionErrorMessage(error))
    } finally {
      setBusyAction(null)
    }
  }

  const handleSeek = (time: number) => {
    if (!Number.isFinite(time) || time < 0) {
      return
    }

    const safeTime = Math.max(0, time)
    setCurrentTime(safeTime)
    setSeekRequest({ time: safeTime, nonce: Date.now() })
  }

  const seekAdjacentMarker = (direction: 'previous' | 'next') => {
    const markersWithTime = timedMarkers(markers)

    if (markersWithTime.length === 0) {
      const offset = direction === 'previous' ? -5 : 5
      handleSeek(Math.max(0, currentTime + offset))
      return
    }

    const target =
      direction === 'previous'
        ? [...markersWithTime].reverse().find(marker => (markerTime(marker) ?? 0) < currentTime - 0.25) || markersWithTime[0]
        : markersWithTime.find(marker => (markerTime(marker) ?? 0) > currentTime + 0.25) || markersWithTime[markersWithTime.length - 1]

    const targetTime = markerTime(target)
    if (targetTime !== null) {
      handleSeek(targetTime)
    }
  }

  const openContainingFolder = async (path: string, fallbackMessage: string) => {
    if (!path) {
      setActionError(fallbackMessage)
      return
    }

    setActionError('')

    try {
      await invoke<void>('open_containing_folder', { path })
    } catch (error) {
      setActionError(actionErrorMessage(error))
    }
  }

  const openMimirStorageFolder = async (kind: 'library' | 'trash', path?: string) => {
    setActionError('')

    try {
      if (path) {
        await invoke<void>('open_containing_folder', { path })
        return
      }

      await invoke<void>('open_mimir_storage_folder', { kind })
    } catch (error) {
      setActionError(actionErrorMessage(error))
    }
  }

  const openViewerFileAction = (action: ViewerFileAction) => {
    if (action === 'original') {
      void openContainingFolder(originalVideoPath(incident), 'Original source path is not available.')
      return
    }

    if (action === 'current') {
      void openContainingFolder(currentVideoPath(incident), 'Current video path is not available.')
      return
    }

    if (action === 'library') {
      void openMimirStorageFolder('library', cleanPath(incident.library_video_path))
      return
    }

    void openMimirStorageFolder('trash', incident.trash_video_path)
  }

  const handleViewerKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (isTextInputElement(event.target) || busyAction !== null || showLibraryConfirm || showDeleteConfirm || showFilesDrawer) {
      return
    }

    const key = event.key.toLowerCase()
    const currentSeverity = severityResolution.displaySeverity

    if (event.code === 'Space') {
      event.preventDefault()
      setPlaybackRequest({ nonce: Date.now() })
      return
    }

    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      seekAdjacentMarker('previous')
      return
    }

    if (event.key === 'ArrowRight') {
      event.preventDefault()
      seekAdjacentMarker('next')
      return
    }

    if (key === 'i' || key === '1') {
      event.preventDefault()
      if (currentSeverity !== 'IMPORTANT') {
        updateManualStatus('IMPORTANT')
      }
      return
    }

    if (key === 'r' || key === '2') {
      event.preventDefault()
      if (currentSeverity !== 'REVIEW') {
        updateManualStatus('REVIEW')
      }
      return
    }

    if (key === 'g' || key === '3') {
      event.preventDefault()
      if (currentSeverity !== 'IGNORE') {
        updateManualStatus('IGNORE')
      }
      return
    }

    if (event.key === 'Delete' && !incident.user_deleted) {
      event.preventDefault()
      setShowDeleteConfirm(true)
    }
  }

  return (
    <main
      ref={viewerRef}
      tabIndex={-1}
      onKeyDown={handleViewerKeyDown}
      className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1500px] flex-col overflow-hidden rounded-2xl border border-white/[0.065] bg-[radial-gradient(circle_at_48%_-10%,rgba(157,183,170,0.085),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.012)),var(--mimir-bg-depth)] shadow-[0_34px_110px_rgba(0,0,0,0.54)] outline-none sm:min-h-[calc(100vh-48px)]"
    >
      <header className="flex flex-wrap items-center justify-between gap-4 px-5 py-4 lg:px-7">
        <button
          onClick={onBack}
          className="h-9 rounded-lg bg-white/[0.03] px-3.5 text-[12px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--mimir-text)]"
        >
          Back to Library
        </button>

        <div className="flex items-center gap-2 rounded-full border border-[rgba(157,183,170,0.14)] bg-[var(--mimir-accent-soft)] px-3 py-1.5 text-[12px] text-[var(--mimir-text-muted)]">
          <span className="h-2 w-2 rounded-full bg-[var(--mimir-status-green)]" />
          Local evidence
        </div>
      </header>

      <section className="flex-1 overflow-y-auto px-5 pb-7 pt-2 lg:px-7">
        <div className="mb-4">
          <div className="mb-2 text-[12px] font-medium text-[var(--mimir-text-subtle)]">
            Incident viewer
          </div>
          <h1 className="text-[28px] font-semibold leading-tight text-[var(--mimir-text)] lg:text-[34px]">
            {title}
          </h1>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[var(--mimir-text-muted)]">
            {timestamp && <span>{timestamp}</span>}
            <span className="max-w-full truncate">{sourceLabel}</span>
            {isGenericBestEffort && (
              <span
                className="rounded-full bg-white/[0.035] px-2 py-0.5 text-[11px] text-[var(--mimir-text-subtle)]"
                title="Generic MP4 review is best-effort. Tesla Sentry camera groups receive full support."
              >
                Generic video · best effort
              </span>
            )}
          </div>
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_352px]">
          <div className="min-w-0">
            <section className="rounded-[24px] border border-white/[0.045] bg-black/16 p-2.5 shadow-[0_24px_78px_rgba(0,0,0,0.32)]">
              <CrashSafeBoundary
                title="Video player error"
                incidentId={incidentActionId(incident)}
                attemptedVideoPath={attemptedVideoPath}
                onBack={onBack}
                onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
              >
                <ViewerMedia
                  incident={incident}
                  seekRequest={seekRequest}
                  playbackRequest={playbackRequest}
                  currentTime={currentTime}
                  onTimeUpdate={setCurrentTime}
                  onDurationChange={value => setDuration(Number.isFinite(value) ? value : 0)}
                />
              </CrashSafeBoundary>
              <section className="mt-3 rounded-2xl bg-white/[0.008] p-3">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3 px-1">
                  <div>
                    <h2 className="text-[15px] font-semibold text-[var(--mimir-text)]">Key moments</h2>
                    <p className="mt-1 text-[12px] text-[var(--mimir-text-subtle)]">
                      Jump to moments Mimir found in this clip.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowKeyMoments(value => !value)}
                    className="h-8 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 text-[12px] font-semibold text-[var(--mimir-text-muted)] transition hover:bg-white/[0.055] hover:text-[var(--mimir-text)]"
                  >
                    {showKeyMoments ? 'Hide' : 'Show'}
                  </button>
                </div>
                {showKeyMoments && (
                  <CrashSafeBoundary
                    title="Key moments error"
                    incidentId={incidentActionId(incident)}
                    attemptedVideoPath={attemptedVideoPath}
                    onBack={onBack}
                    onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
                  >
                    <IncidentTimelineMarkers
                      markers={markers}
                      incident={incident}
                      currentTime={currentTime}
                      duration={duration}
                      onSeek={handleSeek}
                      onSetActualMoment={time => void saveActualMoment(time)}
                    />
                  </CrashSafeBoundary>
                )}
              </section>
            </section>
          </div>

          <div className="xl:sticky xl:top-5 xl:self-start">
            <CrashSafeBoundary
              title="Incident details error"
              incidentId={incidentActionId(incident)}
              attemptedVideoPath={attemptedVideoPath}
              onBack={onBack}
              onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
            >
              <DetailsPanel incident={incident} session={session} severityResolution={severityResolution} />
            </CrashSafeBoundary>
            <CrashSafeBoundary
              title="Action panel error"
              incidentId={incidentActionId(incident)}
              attemptedVideoPath={attemptedVideoPath}
              onBack={onBack}
              onOpenFolder={path => void openContainingFolder(path, 'Video path is not available.')}
            >
              <ReviewActionsPanel
                incident={incident}
                session={session}
                currentSeverity={severityResolution.displaySeverity}
                mimirSeverity={severityResolution.mimirSeverity}
                isManualOverride={severityResolution.isManualOverride}
                busyAction={busyAction}
                actionMessage={actionMessage}
                actionError={actionError}
                actionDetails={actionDetails}
                feedbackChoice={feedbackChoice}
                feedbackNotes={feedbackNotes}
                feedbackIncludeVideo={feedbackIncludeVideo}
                feedbackMessage={feedbackMessage}
                feedbackError={feedbackError}
                noteDraft={noteDraft}
                isEditingNote={isEditingNote}
                onFeedbackChoiceChange={setFeedbackChoice}
                onFeedbackNotesChange={setFeedbackNotes}
                onFeedbackIncludeVideoChange={setFeedbackIncludeVideo}
                onSubmitFeedback={saveFeedback}
                onNoteDraftChange={setNoteDraft}
                onEditNote={() => setIsEditingNote(true)}
                onCancelNote={() => {
                  setNoteDraft(incident.user_note ?? '')
                  setIsEditingNote(false)
                }}
                onSaveNote={saveNote}
                onSetStatus={updateManualStatus}
                onMoveToLibrary={() => setShowLibraryConfirm(true)}
                onConfirmDelete={() => setShowDeleteConfirm(true)}
                onRestoreFromTrash={() => void runCoreV2StorageAction('restore_from_trash')}
                onOpenFiles={() => setShowFilesDrawer(true)}
                onExportReport={() => void exportReport()}
              />
            </CrashSafeBoundary>
          </div>
        </div>
      </section>

      {showFilesDrawer && (
        <ViewerFilesDrawer
          incident={incident}
          onClose={() => setShowFilesDrawer(false)}
          onOpenFileAction={openViewerFileAction}
        />
      )}

      {showLibraryConfirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[480px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[18px] font-semibold text-[var(--mimir-text)]">
              Move this incident to Mimir Library?
            </div>
            <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              Mimir will move all camera angles for this incident to Mimir Library and update the review paths after the move is verified.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setShowLibraryConfirm(false)}
                disabled={busyAction !== null}
                className="h-10 rounded-lg bg-white/[0.04] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setShowLibraryConfirm(false)
                  void runCoreV2StorageAction('move_to_library')
                }}
                disabled={busyAction !== null}
                className="h-10 rounded-lg border border-white/[0.1] bg-white/[0.08] px-4 text-[13px] font-semibold text-[var(--mimir-text)] transition hover:bg-white/[0.12] disabled:cursor-wait disabled:opacity-60"
              >
                Move to Mimir Library
              </button>
            </div>
          </section>
        </div>
      )}

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
          <section className="w-full max-w-[460px] rounded-2xl border border-white/[0.08] bg-[var(--mimir-bg-depth)] p-5 shadow-[0_30px_90px_rgba(0,0,0,0.62)]">
            <div className="text-[18px] font-semibold text-[var(--mimir-text)]">
              Move this incident to Mimir Trash?
            </div>
            <p className="mt-3 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
              This moves the clips to Mimir Trash. It does not permanently delete them.
            </p>
            <p className="mt-2 text-[13px] leading-5 text-[var(--mimir-text-subtle)]">
              All camera angles for this incident will be moved together by Mimir.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                disabled={busyAction !== null}
                className="h-10 rounded-lg bg-white/[0.04] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setShowDeleteConfirm(false)
                  void runCoreV2StorageAction('move_to_trash')
                }}
                disabled={busyAction !== null}
                className="h-10 rounded-lg border border-red-300/20 bg-red-500/12 px-4 text-[13px] font-semibold text-red-100 transition hover:bg-red-500/18 disabled:cursor-wait disabled:opacity-60"
              >
                Move to Mimir Trash
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
