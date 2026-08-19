import { describe, expect, it } from 'vitest'
import { formatDateTime, sourceEventReason, sourceEventTimestamp, sourceFilename } from './incidentDisplay'
import { cleanPath } from './incidentVideoPaths'

describe('formatDateTime', () => {
  it('returns an empty string for missing input', () => {
    expect(formatDateTime(undefined)).toBe('')
    expect(formatDateTime(null)).toBe('')
    expect(formatDateTime('')).toBe('')
  })

  it('returns the original string for unparseable input', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })

  it('formats a valid ISO timestamp', () => {
    const formatted = formatDateTime('2026-04-19T12:43:26Z')
    expect(formatted.length).toBeGreaterThan(0)
    expect(formatted).not.toBe('2026-04-19T12:43:26Z')
  })
})

describe('sourceEventReason', () => {
  it('prefers source_event_reason over tesla_event_reason', () => {
    expect(sourceEventReason({ source_event_reason: 'sentry_aware_object_detection', tesla_event_reason: 'other' } as never)).toBe(
      'sentry_aware_object_detection',
    )
  })

  it('falls back to tesla_event_reason', () => {
    expect(sourceEventReason({ tesla_event_reason: 'user_interaction_dashcam_panic' } as never)).toBe(
      'user_interaction_dashcam_panic',
    )
  })

  it('returns an empty string when neither is present', () => {
    expect(sourceEventReason({} as never)).toBe('')
  })
})

describe('sourceEventTimestamp', () => {
  it('prefers source_event_timestamp, then tesla_event_timestamp, then created_at', () => {
    expect(sourceEventTimestamp({ source_event_timestamp: 'a', tesla_event_timestamp: 'b', created_at: 'c' } as never)).toBe('a')
    expect(sourceEventTimestamp({ tesla_event_timestamp: 'b', created_at: 'c' } as never)).toBe('b')
    expect(sourceEventTimestamp({ created_at: 'c' } as never)).toBe('c')
  })
})

describe('the viewer source label', () => {
  // Mirrors IncidentViewerScreen's sourceLabel chain. The viewer used to derive
  // the name from a path only, which works while the path resolves and loses
  // the filename once the clip is moved to the library or the trash -- exactly
  // when someone wants to know which clip an incident came from.
  const sourceLabel = (incident: {
    source_filename?: string | null
    source_video?: string | null
    video_path?: string | null
  }) =>
    sourceFilename(incident.source_filename ?? undefined) ||
    sourceFilename(cleanPath(incident.source_video) || cleanPath(incident.video_path)) ||
    'Source filename not provided'

  it('uses the recorded filename when the paths are gone', () => {
    expect(sourceLabel({ source_filename: '2026-04-18_16-03-46-back.mp4', video_path: null })).toBe(
      '2026-04-18_16-03-46-back.mp4',
    )
  })

  it('still falls back to the path when no filename was recorded', () => {
    expect(sourceLabel({ video_path: String.raw`C:\Mimir Library\Footage\clip.mp4` })).toBe('clip.mp4')
  })

  it('admits when it has neither rather than showing an empty heading', () => {
    expect(sourceLabel({})).toBe('Source filename not provided')
  })
})
