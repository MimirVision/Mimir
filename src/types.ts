export type AppMode = 'empty' | 'scanning' | 'review'

export type Severity = 'high' | 'review' | 'ignore'

export interface IncidentMoment {
  id: string
  time: string
  title: string
  description: string
  tone: Severity
}

export interface Incident {
  id: string
  title: string
  summary: string
  assessment: string
  severity: Severity
  duration: string
  camera: 'Front' | 'Left Repeater' | 'Right Repeater' | 'Rear'
  objects: string[]
  clipLabel: string
  moments: IncidentMoment[]
  evidenceNotes: string[]
}

export interface SessionSummary {
  date: string
  time: string
  source: string
  clipCount: number
  storage: string
  incidentCount: number
}

export interface ScanStep {
  id: string
  label: string
  description: string
}
