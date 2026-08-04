import { convertFileSrc } from '@tauri-apps/api/core'
import { sourceFilename } from './incidentDisplay'
import type { MimirCameraClip, MimirIncident } from '../types'

export type MediaMode = 'video' | 'image' | 'empty'

export interface ViewerMediaChoice {
  mode: MediaMode
  path: string
  label: string
}

export interface CameraFeed {
  key: string
  camera: string
  label: string
  path: string
  filename: string
  durationSec?: number | null
  exists: boolean | null
  isPrimary: boolean
}

const videoExtensionPattern = /\.(mp4|mov|m4v|avi|mkv|webm)$/i
const imageExtensionPattern = /\.(jpe?g|png|webp|bmp|gif)$/i

export function localFileSrc(path: string) {
  return convertFileSrc(path, 'asset')
}

export function isAbsoluteLocalPath(value?: string) {
  if (!value) {
    return false
  }

  return /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith('\\\\') || value.startsWith('//') || value.startsWith('/')
}

export function isTextInputElement(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  const tagName = target.tagName.toLowerCase()

  return tagName === 'input' || tagName === 'textarea' || tagName === 'select' || target.isContentEditable
}

export function isVideoPath(value?: string) {
  return Boolean(value && videoExtensionPattern.test(value))
}

export function isImagePath(value?: string) {
  return Boolean(value && imageExtensionPattern.test(value))
}

export function cleanPath(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

export function firstCameraClipPath(incident: MimirIncident) {
  const cameraClips = incident.camera_clips

  if (Array.isArray(cameraClips)) {
    for (const clip of cameraClips) {
      const path = cleanPath(clip?.path) || cleanPath(clip?.video_path) || cleanPath(clip?.source_video) || cleanPath(clip?.source_clip)

      if (path) {
        return path
      }
    }
  }

  if (cameraClips && typeof cameraClips === 'object') {
    for (const value of Object.values(cameraClips)) {
      const path =
        typeof value === 'string'
          ? cleanPath(value)
          : cleanPath(value?.path) || cleanPath(value?.video_path) || cleanPath(value?.source_video) || cleanPath(value?.source_clip)

      if (path) {
        return path
      }
    }
  }

  return ''
}

export function normalizeCameraKey(value?: string | null) {
  return String(value || 'camera').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_') || 'camera'
}

export function cameraLabel(value?: string | null) {
  const normalized = normalizeCameraKey(value)

  if (normalized === 'front') {
    return 'Front'
  }

  if (normalized === 'back' || normalized === 'rear') {
    return 'Rear'
  }

  if (normalized === 'left_repeater') {
    return 'Left repeater'
  }

  if (normalized === 'right_repeater') {
    return 'Right repeater'
  }

  if (normalized === 'left_pillar') {
    return 'Left pillar'
  }

  if (normalized === 'right_pillar') {
    return 'Right pillar'
  }

  if (normalized === 'left') {
    return 'Left'
  }

  if (normalized === 'right') {
    return 'Right'
  }

  return String(value || 'Camera')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ') || 'Camera'
}

export function playableCameraPath(clip: MimirCameraClip) {
  return (
    cleanPath(clip.library_path) ||
    cleanPath(clip.path) ||
    cleanPath(clip.video_path) ||
    cleanPath(clip.source_video) ||
    cleanPath(clip.source_clip) ||
    cleanPath(clip.original_source_video)
  )
}

export function normalizeCameraClips(incident: MimirIncident): MimirCameraClip[] {
  const raw = incident.camera_clips
  const clips: MimirCameraClip[] = []

  if (Array.isArray(raw)) {
    clips.push(...raw.filter((clip): clip is MimirCameraClip => Boolean(clip && typeof clip === 'object')))
  } else if (raw && typeof raw === 'object') {
    for (const [camera, value] of Object.entries(raw)) {
      if (typeof value === 'string') {
        clips.push({ camera, path: value, filename: sourceFilename(value), exists: null })
        continue
      }

      if (value && typeof value === 'object') {
        clips.push({ ...value, camera: value.camera ?? camera })
      }
    }
  }

  const seen = new Set<string>()

  return clips.filter(clip => {
    const camera = normalizeCameraKey(clip.camera)
    const path = (
      cleanPath(clip.library_path) ||
      cleanPath(clip.trash_path) ||
      cleanPath(clip.path) ||
      cleanPath(clip.video_path) ||
      cleanPath(clip.source_video) ||
      cleanPath(clip.source_clip) ||
      cleanPath(clip.original_source_video) ||
      cleanPath(clip.filename)
    ).toLowerCase()
    const key = `${camera}|${path}`

    if (!key.trim() || seen.has(key)) {
      return false
    }

    seen.add(key)
    return true
  })
}

export function feedSortScore(feed: CameraFeed, primaryCamera?: string | null) {
  const normalized = normalizeCameraKey(feed.camera)
  const wantedPrimary = normalizeCameraKey(primaryCamera)

  if (feed.isPrimary || (wantedPrimary !== 'camera' && normalized === wantedPrimary)) {
    return 0
  }

  if (normalized === 'back' || normalized === 'rear') {
    return 1
  }

  if (normalized === 'front') {
    return 2
  }

  if (normalized.includes('left')) {
    return 3
  }

  if (normalized.includes('right')) {
    return 4
  }

  return 5
}

export function cameraFeedsForIncident(incident: MimirIncident) {
  const clips = normalizeCameraClips(incident)
  const primaryCamera = normalizeCameraKey(incident.primary_camera)
  const primaryPath =
    cleanPath(incident.video_path) ||
    cleanPath(incident.library_video_path) ||
    cleanPath(incident.source_video) ||
    cleanPath(incident.original_source_video) ||
    cleanPath(incident.source_clip)
  const feeds = new Map<string, CameraFeed>()

  for (const clip of clips) {
    const rawCamera = cleanPath(clip.camera) || 'camera'
    const keyBase = normalizeCameraKey(rawCamera)
    const path = playableCameraPath(clip)
    const isPrimary = normalizeCameraKey(rawCamera) === primaryCamera || Boolean(primaryPath && path === primaryPath)
    const key = `${keyBase}-${path || cleanPath(clip.filename) || feeds.size}`

    feeds.set(key, {
      key,
      camera: rawCamera,
      label: cameraLabel(rawCamera),
      path,
      filename: cleanPath(clip.filename) || sourceFilename(path),
      durationSec: typeof clip.duration_sec === 'number' ? clip.duration_sec : null,
      exists: typeof clip.exists === 'boolean' ? clip.exists : null,
      isPrimary,
    })
  }

  if (primaryPath && !Array.from(feeds.values()).some(feed => feed.path === primaryPath)) {
    const primaryLabel = cleanPath(incident.primary_camera) || 'Primary'
    feeds.set(`primary-${primaryPath}`, {
      key: `primary-${primaryPath}`,
      camera: primaryLabel,
      label: cameraLabel(primaryLabel),
      path: primaryPath,
      filename: sourceFilename(primaryPath),
      durationSec: null,
      exists: incident.video_exists === false ? false : null,
      isPrimary: true,
    })
  }

  return Array.from(feeds.values())
    .filter(feed => feed.path || feed.exists === false)
    .sort((left, right) => feedSortScore(left, incident.primary_camera) - feedSortScore(right, incident.primary_camera))
}

export function canUseKnownVideoPath(value: string | undefined) {
  return isAbsoluteLocalPath(value) && isVideoPath(value)
}

export function videoCandidatesForIncident(incident: MimirIncident) {
  return [
    { path: cleanPath(incident.video_path), label: 'video_path' },
    { path: cleanPath(incident.library_video_path), label: 'library_video_path' },
    { path: cleanPath(incident.source_video), label: 'source_video' },
    { path: cleanPath(incident.original_source_video), label: 'original_source_video' },
    { path: firstCameraClipPath(incident), label: 'camera_clips' },
  ].filter(candidate => canUseKnownVideoPath(candidate.path))
}

export function attemptedVideoPathForIncident(incident: MimirIncident) {
  return videoCandidatesForIncident(incident)[0]?.path || cleanPath(incident.video_path) || cleanPath(incident.library_video_path) || cleanPath(incident.source_video)
}

export function resolveViewerMedia(incident: MimirIncident): ViewerMediaChoice {
  const videoCandidate = videoCandidatesForIncident(incident)[0]

  if (videoCandidate?.path) {
    return { mode: 'video', path: videoCandidate.path, label: videoCandidate.label }
  }

  const imageCandidates = [
    { path: cleanPath(incident.hero_thumbnail), label: 'hero_thumbnail' },
    { path: cleanPath(incident.contact_sheet), label: 'contact_sheet' },
    { path: cleanPath(incident.thumbnail), label: 'thumbnail' },
  ]
  const imageCandidate = imageCandidates.find(candidate => isAbsoluteLocalPath(candidate.path) && isImagePath(candidate.path))

  if (imageCandidate?.path) {
    return { mode: 'image', path: imageCandidate.path, label: imageCandidate.label }
  }

  return { mode: 'empty', path: '', label: 'none' }
}
