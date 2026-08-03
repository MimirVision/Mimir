import { describe, expect, it } from 'vitest'
import {
  attemptedVideoPathForIncident,
  cameraFeedsForIncident,
  cameraLabel,
  canUseKnownVideoPath,
  cleanPath,
  feedSortScore,
  firstCameraClipPath,
  isAbsoluteLocalPath,
  isImagePath,
  isVideoPath,
  normalizeCameraClips,
  normalizeCameraKey,
  playableCameraPath,
  resolveViewerMedia,
  videoCandidatesForIncident,
} from './incidentVideoPaths'
import type { MimirIncident } from '../types'

// Path resolution is a chain of fallbacks that no test previously covered, and
// it is the module a cloud library has to change first: today every playable
// source is an absolute local path handed to convertFileSrc, and a cloud
// session's source is a signed https URL instead. These tests pin the current
// precedence so that change is a deliberate edit rather than a silent one.

const incident = (fields: Partial<MimirIncident>) => ({ id: 'incident_0001', ...fields }) as MimirIncident

describe('isAbsoluteLocalPath', () => {
  it('accepts Windows drive, UNC, and POSIX absolute paths', () => {
    expect(isAbsoluteLocalPath('C:\\Users\\a\\clip.mp4')).toBe(true)
    expect(isAbsoluteLocalPath('c:/Users/a/clip.mp4')).toBe(true)
    expect(isAbsoluteLocalPath('\\\\server\\share\\clip.mp4')).toBe(true)
    expect(isAbsoluteLocalPath('//server/share/clip.mp4')).toBe(true)
    expect(isAbsoluteLocalPath('/mnt/footage/clip.mp4')).toBe(true)
  })

  it('rejects relative paths and empty input', () => {
    expect(isAbsoluteLocalPath('clips/front.mp4')).toBe(false)
    expect(isAbsoluteLocalPath('./front.mp4')).toBe(false)
    expect(isAbsoluteLocalPath('')).toBe(false)
    expect(isAbsoluteLocalPath(undefined)).toBe(false)
  })

  it('rejects an https URL', () => {
    // Load-bearing for the cloud migration: a signed remote URL must never be
    // mistaken for a local file and pushed through convertFileSrc. When cloud
    // playback lands, this has to become an explicit branch on session
    // provenance -- not an accident of the drive-letter regex.
    expect(isAbsoluteLocalPath('https://example.com/clip.mp4')).toBe(false)
    expect(isAbsoluteLocalPath('http://127.0.0.1:1420/clip.mp4')).toBe(false)
  })
})

describe('isVideoPath / isImagePath', () => {
  it('matches by extension, case-insensitively', () => {
    expect(isVideoPath('C:\\a\\clip.MP4')).toBe(true)
    expect(isVideoPath('C:\\a\\clip.mkv')).toBe(true)
    expect(isImagePath('C:\\a\\hero.JPG')).toBe(true)
    expect(isImagePath('C:\\a\\sheet.webp')).toBe(true)
  })

  it('does not treat an image as a video, or the reverse', () => {
    expect(isVideoPath('C:\\a\\hero.jpg')).toBe(false)
    expect(isImagePath('C:\\a\\clip.mp4')).toBe(false)
    expect(isVideoPath(undefined)).toBe(false)
  })

  it('only matches at the end of the path', () => {
    expect(isVideoPath('C:\\a\\.mp4folder\\thumb.jpg')).toBe(false)
  })
})

describe('cleanPath', () => {
  it('trims strings and rejects non-strings and whitespace', () => {
    expect(cleanPath('  C:\\a\\clip.mp4  ')).toBe('C:\\a\\clip.mp4')
    expect(cleanPath('   ')).toBe('')
    expect(cleanPath(null)).toBe('')
    expect(cleanPath(42)).toBe('')
  })
})

describe('normalizeCameraKey / cameraLabel', () => {
  it('normalizes to a lowercase token', () => {
    expect(normalizeCameraKey('Left Repeater')).toBe('left_repeater')
    expect(normalizeCameraKey('  RIGHT-pillar ')).toBe('right_pillar')
    expect(normalizeCameraKey(null)).toBe('camera')
  })

  it('labels the known Tesla camera positions', () => {
    expect(cameraLabel('back')).toBe('Rear')
    expect(cameraLabel('rear')).toBe('Rear')
    expect(cameraLabel('left_repeater')).toBe('Left repeater')
    expect(cameraLabel('front')).toBe('Front')
  })

  it('title-cases an unknown camera rather than dropping it', () => {
    expect(cameraLabel('roof_pod')).toBe('Roof Pod')
    expect(cameraLabel(null)).toBe('Camera')
  })
})

describe('firstCameraClipPath', () => {
  it('reads the array shape', () => {
    expect(firstCameraClipPath(incident({ camera_clips: [{ camera: 'front', path: 'C:\\a\\front.mp4' }] }))).toBe(
      'C:\\a\\front.mp4',
    )
  })

  it('reads the object-of-strings shape', () => {
    expect(firstCameraClipPath(incident({ camera_clips: { front: 'C:\\a\\front.mp4' } }))).toBe('C:\\a\\front.mp4')
  })

  it('skips entries with no usable path', () => {
    const value = firstCameraClipPath(
      incident({ camera_clips: [{ camera: 'front' }, { camera: 'back', source_clip: 'C:\\a\\back.mp4' }] }),
    )
    expect(value).toBe('C:\\a\\back.mp4')
  })

  it('returns an empty string when there is nothing to read', () => {
    expect(firstCameraClipPath(incident({}))).toBe('')
    expect(firstCameraClipPath(incident({ camera_clips: [] }))).toBe('')
  })
})

describe('playableCameraPath', () => {
  it('prefers the managed library copy over the original source', () => {
    // Order matters: once a clip has been moved to the Mimir Library, the
    // original path may no longer exist, so library_path has to win.
    const path = playableCameraPath({
      library_path: 'C:\\Videos\\Mimir Library\\front.mp4',
      path: 'D:\\TeslaCam\\front.mp4',
      source_video: 'D:\\TeslaCam\\front.mp4',
    })
    expect(path).toBe('C:\\Videos\\Mimir Library\\front.mp4')
  })

  it('falls through the source fields in order', () => {
    expect(playableCameraPath({ source_clip: 'C:\\a\\c.mp4' })).toBe('C:\\a\\c.mp4')
    expect(playableCameraPath({})).toBe('')
  })
})

describe('normalizeCameraClips', () => {
  it('turns the object-of-strings shape into clip records', () => {
    const clips = normalizeCameraClips(incident({ camera_clips: { front: 'C:\\a\\front.mp4' } }))
    expect(clips).toHaveLength(1)
    expect(clips[0].camera).toBe('front')
    expect(clips[0].filename).toBe('front.mp4')
  })

  it('keeps the explicit camera on an object entry over the record key', () => {
    const clips = normalizeCameraClips(
      incident({ camera_clips: { cam0: { camera: 'left_repeater', path: 'C:\\a\\l.mp4' } } }),
    )
    expect(clips[0].camera).toBe('left_repeater')
  })

  it('dedupes the same camera and path, case-insensitively on the path', () => {
    const clips = normalizeCameraClips(
      incident({
        camera_clips: [
          { camera: 'front', path: 'C:\\a\\front.mp4' },
          { camera: 'Front', path: 'c:\\A\\FRONT.MP4' },
          { camera: 'back', path: 'C:\\a\\back.mp4' },
        ],
      }),
    )
    expect(clips.map(clip => clip.camera)).toEqual(['front', 'back'])
  })

  it('ignores null entries instead of throwing', () => {
    expect(normalizeCameraClips(incident({ camera_clips: [null as never, { camera: 'front', path: 'C:\\a\\f.mp4' }] }))).toHaveLength(1)
  })
})

describe('feedSortScore', () => {
  const feed = (camera: string, isPrimary = false) => ({
    key: camera,
    camera,
    label: camera,
    path: `C:\\a\\${camera}.mp4`,
    filename: `${camera}.mp4`,
    exists: null,
    isPrimary,
  })

  it('puts the primary feed first, then rear, then front, then the repeaters', () => {
    expect(feedSortScore(feed('front', true))).toBe(0)
    expect(feedSortScore(feed('back'))).toBe(1)
    expect(feedSortScore(feed('front'))).toBe(2)
    expect(feedSortScore(feed('left_repeater'))).toBe(3)
    expect(feedSortScore(feed('right_repeater'))).toBe(4)
  })

  it('treats a matching primaryCamera as primary even when the flag is unset', () => {
    expect(feedSortScore(feed('left_repeater'), 'left_repeater')).toBe(0)
  })
})

describe('cameraFeedsForIncident', () => {
  it('sorts feeds and marks the primary camera', () => {
    const feeds = cameraFeedsForIncident(
      incident({
        primary_camera: 'back',
        camera_clips: [
          { camera: 'front', path: 'C:\\a\\front.mp4' },
          { camera: 'back', path: 'C:\\a\\back.mp4' },
        ],
      }),
    )
    expect(feeds.map(item => item.camera)).toEqual(['back', 'front'])
    expect(feeds[0].isPrimary).toBe(true)
  })

  it('adds the incident video_path as a synthetic feed when no clip covers it', () => {
    const feeds = cameraFeedsForIncident(
      incident({ video_path: 'C:\\a\\primary.mp4', primary_camera: 'front', camera_clips: [] }),
    )
    expect(feeds).toHaveLength(1)
    expect(feeds[0].path).toBe('C:\\a\\primary.mp4')
    expect(feeds[0].isPrimary).toBe(true)
  })

  it('does not duplicate the primary when a clip already has that path', () => {
    const feeds = cameraFeedsForIncident(
      incident({ video_path: 'C:\\a\\front.mp4', camera_clips: [{ camera: 'front', path: 'C:\\a\\front.mp4' }] }),
    )
    expect(feeds).toHaveLength(1)
  })

  it('keeps a pathless feed only when it is known to be missing', () => {
    const feeds = cameraFeedsForIncident(
      incident({ camera_clips: [{ camera: 'front', filename: 'front.mp4', exists: false }] }),
    )
    expect(feeds).toHaveLength(1)
    expect(feeds[0].exists).toBe(false)
  })
})

describe('videoCandidatesForIncident / attemptedVideoPathForIncident', () => {
  it('orders candidates video_path, library, source, original, then camera clips', () => {
    const candidates = videoCandidatesForIncident(
      incident({
        video_path: 'C:\\a\\1.mp4',
        library_video_path: 'C:\\a\\2.mp4',
        source_video: 'C:\\a\\3.mp4',
        original_source_video: 'C:\\a\\4.mp4',
        camera_clips: [{ camera: 'front', path: 'C:\\a\\5.mp4' }],
      }),
    )
    expect(candidates.map(item => item.label)).toEqual([
      'video_path',
      'library_video_path',
      'source_video',
      'original_source_video',
      'camera_clips',
    ])
  })

  it('drops candidates that are not absolute local video paths', () => {
    const candidates = videoCandidatesForIncident(
      incident({ video_path: 'relative/clip.mp4', library_video_path: 'C:\\a\\hero.jpg', source_video: 'C:\\a\\ok.mp4' }),
    )
    expect(candidates.map(item => item.label)).toEqual(['source_video'])
  })

  it('still reports an attempted path when nothing is playable, for diagnostics', () => {
    // Deliberate: the viewer needs something to show the user when playback
    // fails, so this falls back past canUseKnownVideoPath on purpose.
    expect(attemptedVideoPathForIncident(incident({ video_path: 'relative/clip.mp4' }))).toBe('relative/clip.mp4')
  })
})

describe('canUseKnownVideoPath', () => {
  it('requires both an absolute local path and a video extension', () => {
    expect(canUseKnownVideoPath('C:\\a\\clip.mp4')).toBe(true)
    expect(canUseKnownVideoPath('C:\\a\\hero.jpg')).toBe(false)
    expect(canUseKnownVideoPath('clip.mp4')).toBe(false)
    expect(canUseKnownVideoPath(undefined)).toBe(false)
  })
})

describe('resolveViewerMedia', () => {
  it('prefers a playable video', () => {
    expect(resolveViewerMedia(incident({ video_path: 'C:\\a\\clip.mp4', hero_thumbnail: 'C:\\a\\hero.jpg' }))).toEqual({
      mode: 'video',
      path: 'C:\\a\\clip.mp4',
      label: 'video_path',
    })
  })

  it('falls back to hero thumbnail, then contact sheet, then thumbnail', () => {
    expect(resolveViewerMedia(incident({ hero_thumbnail: 'C:\\a\\hero.jpg' })).label).toBe('hero_thumbnail')
    expect(resolveViewerMedia(incident({ contact_sheet: 'C:\\a\\sheet.png' })).label).toBe('contact_sheet')
    expect(resolveViewerMedia(incident({ thumbnail: 'C:\\a\\thumb.jpg' })).label).toBe('thumbnail')
  })

  it('reports empty when nothing is renderable', () => {
    expect(resolveViewerMedia(incident({}))).toEqual({ mode: 'empty', path: '', label: 'none' })
  })
})
