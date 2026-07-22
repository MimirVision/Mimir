import type { FrontendScanDiagnostics, MimirIncident, MimirSession } from '../types'

export type SeverityGroup = 'IMPORTANT' | 'REVIEW' | 'IGNORE'
export type ManualStatusOverrides = Record<string, SeverityGroup>

export interface SeverityResolution {
  mimirSeverity: SeverityGroup
  displaySeverity: SeverityGroup
  isManualOverride: boolean
  source: 'manual' | 'mimir'
}

export function normalizeSeverity(severity?: string | null): SeverityGroup {
  const value = String(severity ?? '').toUpperCase()
  if (value === 'IMPORTANT' || value === 'REVIEW') {
    return value
  }
  return 'IGNORE'
}

export function sessionIdentity(session: MimirSession, diagnostics?: FrontendScanDiagnostics | null) {
  return [
    session.session_id,
    session.session_created_at,
    diagnostics?.latest_session_modified_time,
    session.scan_summary?.scan_completed_at,
    session.scan_summary?.source_path,
    session.scan_summary?.source_name,
    session.input_folder,
    session.selected_input,
    session.started_at,
    session.finished_at,
    session.output_path,
    diagnostics?.latest_session_path,
  ]
    .filter(Boolean)
    .join('|')
}

export function incidentOverrideKey(incident: MimirIncident, identity: string) {
  return [
    identity,
    incident.event_group_id,
    incident.event_timestamp,
    incident.source_filename,
    incident.source_video,
    incident.original_source_video,
    incident.video_path,
    incident.id,
    incident.event_id,
  ]
    .filter(Boolean)
    .join('|')
}

export function resolveIncidentSeverity(
  incident: MimirIncident,
  manualOverrides: ManualStatusOverrides,
  identity: string,
): SeverityResolution {
  const mimirSeverity = normalizeSeverity(incident.final_severity || incident.severity)
  const persistedManualSeverity =
    incident.manual_status_override && incident.user_status ? normalizeSeverity(incident.user_status) : null
  const manualSeverity = manualOverrides[incidentOverrideKey(incident, identity)] || persistedManualSeverity

  if (manualSeverity) {
    return {
      mimirSeverity,
      displaySeverity: manualSeverity,
      isManualOverride: manualSeverity !== mimirSeverity,
      source: 'manual',
    }
  }

  return {
    mimirSeverity,
    displaySeverity: mimirSeverity,
    isManualOverride: false,
    source: 'mimir',
  }
}
