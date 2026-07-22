import { describe, expect, it } from 'vitest'
import type { MimirIncident, MimirSession } from '../types'
import {
  incidentOverrideKey,
  resolveIncidentSeverity,
  sessionIdentity,
  type ManualStatusOverrides,
} from './incidentStatus'

const incident: MimirIncident = {
  id: 'incident_0001',
  event_group_id: 'group-one',
  source_filename: 'clip.mp4',
  final_severity: 'IGNORE',
}

function session(sessionId: string, sourcePath: string): MimirSession {
  return {
    session_id: sessionId,
    session_created_at: `${sessionId}-created`,
    status: 'complete',
    started_at: `${sessionId}-started`,
    finished_at: `${sessionId}-complete`,
    clips_processed: 1,
    important: 0,
    review: 0,
    ignore: 1,
    incidents: [incident],
    scan_summary: {
      source_path: sourcePath,
      scan_completed_at: `${sessionId}-complete`,
    },
  }
}

describe('incident status isolation', () => {
  it('keeps the original Mimir result when there is no override', () => {
    expect(resolveIncidentSeverity(incident, {}, sessionIdentity(session('one', 'C:\\one')))).toEqual({
      mimirSeverity: 'IGNORE',
      displaySeverity: 'IGNORE',
      isManualOverride: false,
      source: 'mimir',
    })
  })

  it('uses a manual status without replacing the Mimir result', () => {
    const identity = sessionIdentity(session('one', 'C:\\one'))
    const overrides: ManualStatusOverrides = {
      [incidentOverrideKey(incident, identity)]: 'IMPORTANT',
    }
    expect(resolveIncidentSeverity(incident, overrides, identity)).toEqual({
      mimirSeverity: 'IGNORE',
      displaySeverity: 'IMPORTANT',
      isManualOverride: true,
      source: 'manual',
    })
  })

  it('does not leak an incident id override into a new scan', () => {
    const firstIdentity = sessionIdentity(session('one', 'C:\\one'))
    const secondIdentity = sessionIdentity(session('two', 'D:\\two'))
    const overrides: ManualStatusOverrides = {
      [incidentOverrideKey(incident, firstIdentity)]: 'IMPORTANT',
    }
    expect(resolveIncidentSeverity(incident, overrides, secondIdentity).displaySeverity).toBe('IGNORE')
  })
})
