export type AppMode = 'empty' | 'results' | 'sample'

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

export interface MimirIncident {
  id: string
  source_video: string
  event_id: number
  severity: string
  ai_decision: string
  score: number
  persons: number
  vehicles: number
  active_frames: number
  thumbnail: string
  created_at: string
}

export interface MimirSession {
  status: string
  started_at: string
  finished_at: string | null
  clips_processed: number
  important: number
  review: number
  ignore: number
  incidents: MimirIncident[]
}

export type SessionLoadState = 'idle' | 'loading' | 'loaded' | 'missing' | 'error'
