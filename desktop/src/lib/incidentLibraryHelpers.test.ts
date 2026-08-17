import { describe, expect, it } from 'vitest'
import { sortIncidents } from './incidentLibraryHelpers'
import { incidentEvidenceStrength } from './incidentEvidence'
import type { MimirIncident } from '../types'

// sortIncidents had no tests, which is how it went unnoticed that a 278-event
// REVIEW tier was ordered purely by timestamp -- as long as the raw footage and
// no better sorted.

function incident(fields: Record<string, unknown>): MimirIncident {
  return {
    id: String(fields.id ?? 'incident'),
    created_at: String(fields.created_at ?? '2026-04-01T00:00:00Z'),
    ...fields,
  } as unknown as MimirIncident
}

const order = (incidents: MimirIncident[]) => sortIncidents(incidents, {}, 'tester').map(item => item.id)

describe('sortIncidents', () => {
  it('puts severity before everything else', () => {
    // A REVIEW with overwhelming evidence must still sit below an IMPORTANT.
    // Evidence strength orders within a tier; it never promotes across one.
    const incidents = [
      incident({ id: 'review', final_severity: 'REVIEW', visible_contact: true, hard_contact_candidate: true }),
      incident({ id: 'important', final_severity: 'IMPORTANT' }),
    ]

    expect(order(incidents)).toEqual(['important', 'review'])
  })

  it('puts an observed contact above a merely close-and-moving event', () => {
    const incidents = [
      incident({ id: 'close', final_severity: 'REVIEW', contact_level: 'MEDIUM', created_at: '2026-04-01T00:00:00Z' }),
      incident({ id: 'seen', final_severity: 'REVIEW', visible_contact: true, created_at: '2026-04-09T00:00:00Z' }),
    ]

    // 'seen' happened a week later, so timestamp order would have buried it.
    expect(order(incidents)).toEqual(['seen', 'close'])
  })

  it('ranks a named mechanism above a bare HIGH level', () => {
    const incidents = [
      incident({ id: 'high-level', final_severity: 'REVIEW', contact_level: 'HIGH', impact_level: 'HIGH' }),
      incident({ id: 'rear-impact', final_severity: 'REVIEW', rear_impact_candidate: true }),
    ]

    expect(order(incidents)).toEqual(['rear-impact', 'high-level'])
  })

  it('reads evidence out of classification_debug when it is not on the incident', () => {
    const incidents = [
      incident({ id: 'plain', final_severity: 'REVIEW', contact_level: 'MEDIUM' }),
      incident({ id: 'nested', final_severity: 'REVIEW', classification_debug: { hard_contact_candidate: true } }),
    ]

    expect(order(incidents)).toEqual(['nested', 'plain'])
  })

  it('stays chronological when the evidence is equal', () => {
    const incidents = [
      incident({ id: 'later', final_severity: 'REVIEW', contact_level: 'MEDIUM', created_at: '2026-04-09T00:00:00Z' }),
      incident({ id: 'earlier', final_severity: 'REVIEW', contact_level: 'MEDIUM', created_at: '2026-04-01T00:00:00Z' }),
    ]

    expect(order(incidents)).toEqual(['earlier', 'later'])
  })

  it('does not lose or duplicate incidents', () => {
    const incidents = [
      incident({ id: 'a', final_severity: 'REVIEW', visible_contact: true }),
      incident({ id: 'b', final_severity: 'IGNORE' }),
      incident({ id: 'c', final_severity: 'IMPORTANT' }),
      incident({ id: 'd', final_severity: 'REVIEW' }),
    ]

    expect(order(incidents).sort()).toEqual(['a', 'b', 'c', 'd'])
  })

  it('leaves the caller\'s array alone', () => {
    const incidents = [
      incident({ id: 'second', final_severity: 'REVIEW' }),
      incident({ id: 'first', final_severity: 'IMPORTANT' }),
    ]

    sortIncidents(incidents, {}, 'tester')

    expect(incidents.map(item => item.id)).toEqual(['second', 'first'])
  })
})

describe('incidentEvidenceStrength', () => {
  it('is zero for an incident carrying no evidence at all', () => {
    expect(incidentEvidenceStrength(incident({ id: 'empty' }))).toBe(0)
  })

  it('discounts an event whose detector was returning whole-frame boxes', () => {
    // Those boxes mean the detector was failing on this footage, so what it did
    // produce is worth less, not more.
    const clean = incident({ id: 'clean', contact_level: 'MEDIUM' })
    const failing = incident({
      id: 'failing',
      contact_level: 'MEDIUM',
      local_evidence: { frame_filling_detections: 17 },
    })

    expect(incidentEvidenceStrength(failing)).toBeLessThan(incidentEvidenceStrength(clean))
  })

  it('does not let a tie-break score outweigh a real signal', () => {
    // contact_score is continuous and only separates otherwise equal events;
    // a maxed-out score must not lift a MEDIUM above a visible contact.
    const scored = incident({ id: 'scored', contact_level: 'MEDIUM', contact_score: 1 })
    const seen = incident({ id: 'seen', visible_contact: true })

    expect(incidentEvidenceStrength(seen)).toBeGreaterThan(incidentEvidenceStrength(scored))
  })

  it('survives a malformed score without producing NaN', () => {
    const broken = incident({ id: 'broken', contact_score: 'not a number', impact_score: null })

    expect(Number.isFinite(incidentEvidenceStrength(broken))).toBe(true)
  })
})
