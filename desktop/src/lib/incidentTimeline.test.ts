import { describe, expect, it } from 'vitest'
import {
  dedupeMarkersByPriority,
  deriveReviewTimelineMarkers,
  finiteNumber,
  formatTime,
  formatTimecode,
  incidentDurationSeconds,
  keyMomentSeverity,
  markerIsNear,
  markerKey,
  markerLabel,
  markerPosition,
  markerPresentationPriority,
  markerTimeFromEvidence,
  markerTypeLabel,
  markersAreNear,
  reviewTimelineDuration,
  timedMarkers,
  validTimelineMarkers,
} from './incidentTimeline'
import type { MimirIncident, MimirTimelineMarker } from '../types'

// deriveReviewTimelineMarkers is a five-level fallback cascade that decides
// what the user actually sees on the scrub bar, and none of it was covered.
// The cascade is also what a cloud-produced session has to reproduce exactly,
// so these tests double as the contract for the golden-session parity check.

const incident = (fields: Partial<MimirIncident>) => ({ id: 'incident_0001', ...fields }) as MimirIncident
const marker = (fields: Partial<MimirTimelineMarker>) => fields as MimirTimelineMarker

describe('formatTime / formatTimecode', () => {
  it('formats seconds as m:ss and mm:ss', () => {
    expect(formatTime(0)).toBe('0:00')
    expect(formatTime(9)).toBe('0:09')
    expect(formatTime(75)).toBe('1:15')
    expect(formatTimecode(75)).toBe('01:15')
    expect(formatTimecode(605)).toBe('10:05')
  })

  it('floors fractional seconds rather than rounding', () => {
    expect(formatTime(59.9)).toBe('0:59')
    expect(formatTimecode(59.9)).toBe('00:59')
  })

  it('clamps negatives to zero', () => {
    expect(formatTime(-5)).toBe('0:00')
    expect(formatTimecode(-5)).toBe('00:00')
  })

  it('uses distinct placeholders for missing input', () => {
    // formatTime is used where a running clock must always read as a time;
    // formatTimecode is used where "unknown" has to be visibly unknown.
    expect(formatTime(undefined)).toBe('0:00')
    expect(formatTime(Number.NaN)).toBe('0:00')
    expect(formatTimecode(undefined)).toBe('--:--')
    expect(formatTimecode(null)).toBe('--:--')
    expect(formatTimecode(Number.POSITIVE_INFINITY)).toBe('--:--')
  })
})

describe('finiteNumber', () => {
  it('accepts finite numbers and numeric strings', () => {
    expect(finiteNumber(12)).toBe(12)
    expect(finiteNumber(0)).toBe(0)
    expect(finiteNumber('12.5')).toBe(12.5)
  })

  it('rejects non-finite, empty, and non-numeric values', () => {
    expect(finiteNumber(Number.NaN)).toBeNull()
    expect(finiteNumber(Number.POSITIVE_INFINITY)).toBeNull()
    expect(finiteNumber('')).toBeNull()
    expect(finiteNumber('   ')).toBeNull()
    expect(finiteNumber('not a number')).toBeNull()
    expect(finiteNumber(null)).toBeNull()
    expect(finiteNumber({})).toBeNull()
  })
})

describe('markerTimeFromEvidence', () => {
  it('prefers time_sec, then falls through the alternate evidence keys in order', () => {
    expect(markerTimeFromEvidence(marker({ time_sec: 4 }))).toBe(4)
    expect(markerTimeFromEvidence(marker({ timestamp_sec: 5 } as never))).toBe(5)
    expect(markerTimeFromEvidence(marker({ time_seconds: 6 } as never))).toBe(6)
    expect(markerTimeFromEvidence(marker({ seconds: 7 } as never))).toBe(7)
    expect(markerTimeFromEvidence(marker({ second: 8 } as never))).toBe(8)
    expect(markerTimeFromEvidence(marker({ time: 9 } as never))).toBe(9)
  })

  it('rejects negative times but keeps zero', () => {
    expect(markerTimeFromEvidence(marker({ timestamp_sec: -1 } as never))).toBeNull()
    expect(markerTimeFromEvidence(marker({ timestamp_sec: 0 } as never))).toBe(0)
  })

  it('returns null when no time is present at all', () => {
    expect(markerTimeFromEvidence(marker({ type: 'motion_spike' }))).toBeNull()
  })
})

describe('markerTypeLabel', () => {
  it('maps the known evidence types to reviewer-facing wording', () => {
    expect(markerTypeLabel('impact_contact')).toBe('Impact/contact')
    expect(markerTypeLabel('possible_impact')).toBe('Impact')
    expect(markerTypeLabel('possible_contact')).toBe('Possible contact')
    expect(markerTypeLabel('motion_spike')).toBe('Peak motion')
    expect(markerTypeLabel('person_near')).toBe('Person nearby')
    expect(markerTypeLabel('vehicle_near')).toBe('Vehicle nearby')
    expect(markerTypeLabel('activity_start')).toBe('Activity starts')
    expect(markerTypeLabel('review_point')).toBe('Review point')
  })

  it('falls back to a neutral label for anything unrecognized', () => {
    expect(markerTypeLabel('something_new')).toBe('Key moment')
    expect(markerTypeLabel(undefined)).toBe('Key moment')
  })
})

describe('markerLabel', () => {
  it('says plain "Impact" only when hard local evidence supports it', () => {
    const supported = incident({ local_evidence: { strong_impact_like_motion: true } } as never)
    expect(markerLabel(marker({ type: 'impact_contact' }), supported)).toBe('Impact')
  })

  it('stays on the hedged wording without that evidence', () => {
    expect(markerLabel(marker({ type: 'impact_contact' }), incident({}))).toBe('Impact/contact')
  })

  it('softens an unsupported contact marker to activity wording', () => {
    expect(markerLabel(marker({ type: 'possible_contact' }), incident({}))).toBe('Activity near vehicle')
  })

  it('drops a label that merely restates the type', () => {
    expect(markerLabel(marker({ type: 'motion_spike', label: 'motion spike' }))).toBe('Peak motion')
    expect(markerLabel(marker({ type: 'motion_spike', label: 'Loudest bang' }))).toBe('Loudest bang')
  })
})

describe('validTimelineMarkers / timedMarkers', () => {
  it('drops non-object entries', () => {
    expect(validTimelineMarkers([null, undefined, 'x', { type: 'a' }] as never)).toHaveLength(1)
    expect(validTimelineMarkers(undefined)).toEqual([])
  })

  it('keeps only timed markers and sorts them ascending', () => {
    const sorted = timedMarkers([marker({ time_sec: 9 }), marker({ type: 'no-time' }), marker({ time_sec: 2 })])
    expect(sorted.map(item => item.time_sec)).toEqual([2, 9])
  })
})

describe('incidentDurationSeconds', () => {
  it('uses a numeric duration directly', () => {
    expect(incidentDurationSeconds(incident({ duration: 42 } as never))).toBe(42)
  })

  it('parses a numeric string and an m:ss string', () => {
    expect(incidentDurationSeconds(incident({ duration: '45' } as never))).toBe(45)
    expect(incidentDurationSeconds(incident({ duration: '1:30' } as never))).toBe(90)
  })

  it('falls back to the longest camera clip', () => {
    const value = incidentDurationSeconds(
      incident({
        camera_clips: [
          { camera: 'front', path: 'C:\\a\\f.mp4', duration_sec: 50 },
          { camera: 'back', path: 'C:\\a\\b.mp4', duration_sec: 62 },
        ],
      }),
    )
    expect(value).toBe(62)
  })

  it('falls back to local evidence, then to null', () => {
    expect(incidentDurationSeconds(incident({ local_evidence: { total_duration_sec: 30 } } as never))).toBe(30)
    expect(incidentDurationSeconds(incident({}))).toBeNull()
  })
})

describe('reviewTimelineDuration', () => {
  it('prefers the real video duration once it is known', () => {
    expect(reviewTimelineDuration(incident({ duration: 60 } as never), 58.5)).toBe(58.5)
  })

  it('falls back to the incident duration, then zero', () => {
    expect(reviewTimelineDuration(incident({ duration: 60 } as never), 0)).toBe(60)
    expect(reviewTimelineDuration(incident({}), 0)).toBe(0)
  })
})

describe('markerIsNear / markersAreNear', () => {
  it('uses a 0.75s default window against a marker list', () => {
    const markers = [marker({ time_sec: 10 })]
    expect(markerIsNear(markers, 10.5)).toBe(true)
    expect(markerIsNear(markers, 11)).toBe(false)
  })

  it('uses a 1s default window between two markers', () => {
    expect(markersAreNear(marker({ time_sec: 10 }), marker({ time_sec: 11 }))).toBe(true)
    expect(markersAreNear(marker({ time_sec: 10 }), marker({ time_sec: 11.5 }))).toBe(false)
    expect(markersAreNear(marker({ time_sec: 10 }), marker({ type: 'untimed' }))).toBe(false)
  })
})

describe('keyMomentSeverity', () => {
  it('escalates impact/contact only when the incident itself is IMPORTANT', () => {
    expect(keyMomentSeverity(marker({ type: 'impact_contact' }), incident({ severity: 'IMPORTANT' } as never))).toBe('IMPORTANT')
    expect(keyMomentSeverity(marker({ type: 'impact_contact' }), incident({ severity: 'REVIEW' } as never))).toBe('REVIEW')
  })

  it('marks motion and object markers REVIEW, and anything else NEUTRAL', () => {
    expect(keyMomentSeverity(marker({ type: 'motion_spike' }), incident({}))).toBe('REVIEW')
    expect(keyMomentSeverity(marker({ type: 'person_near' }), incident({}))).toBe('REVIEW')
    expect(keyMomentSeverity(marker({ type: 'review_point' }), incident({}))).toBe('NEUTRAL')
  })
})

describe('markerPosition', () => {
  it('positions by time as a percentage of duration', () => {
    expect(markerPosition(marker({ time_sec: 30 }), 0, 3, 60)).toBe(50)
  })

  it('clamps out-of-range times into 0-100', () => {
    expect(markerPosition(marker({ time_sec: 120 }), 0, 3, 60)).toBe(100)
    expect(markerPosition(marker({ time_sec: -10 }), 0, 3, 60)).toBe(0)
  })

  it('spreads untimed markers evenly, and centres a lone one', () => {
    expect(markerPosition(marker({}), 0, 1, 0)).toBe(50)
    expect(markerPosition(marker({}), 0, 3, 0)).toBe(0)
    expect(markerPosition(marker({}), 1, 3, 0)).toBe(50)
    expect(markerPosition(marker({}), 2, 3, 0)).toBe(100)
  })
})

describe('markerKey', () => {
  it('stays stable for the same marker and distinguishes different times', () => {
    expect(markerKey(marker({ type: 'motion_spike', time_sec: 4 }), 0)).toBe('motion_spike-0-4-no-frame')
    expect(markerKey(marker({}), 2)).toBe('marker-2-no-time-no-frame')
  })
})

describe('markerPresentationPriority', () => {
  it('ranks a user correction above everything Mimir derived', () => {
    const base = incident({})
    expect(markerPresentationPriority(marker({ type: 'user_corrected' }), base)).toBeGreaterThan(
      markerPresentationPriority(marker({ type: 'impact_contact' }), base),
    )
  })

  it('ranks impact above contact above review above motion above nearby', () => {
    const base = incident({ local_evidence: { strong_impact_like_motion: true } } as never)
    const priority = (type: string) => markerPresentationPriority(marker({ type }), base)
    expect(priority('impact')).toBeGreaterThan(priority('possible_contact'))
    expect(priority('possible_contact')).toBeGreaterThan(priority('review_point'))
    expect(priority('motion_spike')).toBeGreaterThan(priority('person_near'))
  })
})

describe('dedupeMarkersByPriority', () => {
  it('keeps the higher-priority marker when two land within a second', () => {
    const kept = dedupeMarkersByPriority(
      [marker({ type: 'motion_spike', time_sec: 10 }), marker({ type: 'user_corrected', time_sec: 10.5 })],
      incident({}),
      { allMoments: false },
    )
    expect(kept).toHaveLength(1)
    expect(kept[0].type).toBe('user_corrected')
  })

  it('keeps both when they are far enough apart', () => {
    const kept = dedupeMarkersByPriority(
      [marker({ type: 'motion_spike', time_sec: 10 }), marker({ type: 'user_corrected', time_sec: 30 })],
      incident({}),
      { allMoments: false },
    )
    expect(kept.map(item => item.time_sec)).toEqual([10, 30])
  })

  it('allMoments keeps nearby markers that carry different labels', () => {
    const markers = [marker({ type: 'motion_spike', time_sec: 10 }), marker({ type: 'person_near', time_sec: 10.4 })]
    expect(dedupeMarkersByPriority(markers, incident({}), { allMoments: false })).toHaveLength(1)
    expect(dedupeMarkersByPriority(markers, incident({}), { allMoments: true })).toHaveLength(2)
  })

  it('returns results sorted by time, not by priority', () => {
    const kept = dedupeMarkersByPriority(
      [marker({ type: 'user_corrected', time_sec: 30 }), marker({ type: 'motion_spike', time_sec: 5 })],
      incident({}),
      { allMoments: false },
    )
    expect(kept.map(item => item.time_sec)).toEqual([5, 30])
  })
})

describe('deriveReviewTimelineMarkers', () => {
  it('level 1: a user correction is surfaced as its own marker', () => {
    const markers = deriveReviewTimelineMarkers(incident({ user_key_moment_sec: 12 } as never))
    expect(markers).toHaveLength(1)
    expect(markers[0].type).toBe('user_corrected')
    expect(markers[0].label).toBe('Actual moment')
  })

  it('level 2: uses key_moments when there is no user correction', () => {
    const markers = deriveReviewTimelineMarkers(
      incident({ key_moments: [{ type: 'motion_spike', time_sec: 4 }], primary_key_moment_sec: 20 } as never),
    )
    expect(markers.map(item => item.time_sec)).toEqual([4])
  })

  it('level 3: falls back to the primary key moment', () => {
    const markers = deriveReviewTimelineMarkers(incident({ primary_key_moment_sec: 20 } as never))
    expect(markers).toHaveLength(1)
    expect(markers[0].time_sec).toBe(20)
    expect(markers[0].type).toBe('review_point')
  })

  it('level 3: labels the primary moment impact/contact when the incident says so', () => {
    const markers = deriveReviewTimelineMarkers(
      incident({ primary_key_moment_sec: 20, possible_impact: true } as never),
    )
    expect(markers[0].type).toBe('impact_contact')
  })

  it('level 4: falls back to the strongest local motion', () => {
    const markers = deriveReviewTimelineMarkers(
      incident({ local_evidence: { motion_spike_time_sec: 8 } } as never),
    )
    expect(markers).toHaveLength(1)
    expect(markers[0].time_sec).toBe(8)
    expect(markers[0].label).toBe('Peak motion')
  })

  it('level 5: falls back to the middle of the clip, but only if a duration is known', () => {
    expect(deriveReviewTimelineMarkers(incident({ duration: 60 } as never))[0].time_sec).toBe(30)
    expect(deriveReviewTimelineMarkers(incident({}))).toEqual([])
  })

  it('uses the real video duration for the midpoint when it is available', () => {
    expect(deriveReviewTimelineMarkers(incident({ duration: 60 } as never), 40)[0].time_sec).toBe(20)
  })

  it('collapses key moments that land within the dedupe window', () => {
    const markers = deriveReviewTimelineMarkers(
      incident({
        key_moments: [
          { type: 'motion_spike', time_sec: 10 },
          { type: 'person_near', time_sec: 10.5 },
          { type: 'vehicle_near', time_sec: 25 },
        ],
      } as never),
    )
    expect(markers.map(item => item.time_sec)).toEqual([10, 25])
  })

  it('drops key moments that carry no usable time', () => {
    const markers = deriveReviewTimelineMarkers(
      incident({ key_moments: [{ type: 'motion_spike' }, { type: 'person_near', time_sec: 5 }] } as never),
    )
    expect(markers.map(item => item.time_sec)).toEqual([5])
  })

  it('returns markers sorted by time', () => {
    const markers = deriveReviewTimelineMarkers(
      incident({
        user_key_moment_sec: 30,
        key_moments: [{ type: 'motion_spike', time_sec: 5 }],
      } as never),
    )
    expect(markers.map(item => item.time_sec)).toEqual([5, 30])
  })
})
