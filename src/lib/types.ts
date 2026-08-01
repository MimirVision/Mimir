// Mirrors the JSON shapes mimir_training_ground.py's --json commands emit
// (C:\Mimir_Backend\mimir_training_ground.py) and gate_progress() in
// mimir_core_v2_pipeline.py. Kept intentionally loose (most fields optional
// or `unknown`) rather than re-declaring every backend field, since the
// backend is the source of truth and this only needs what the UI reads.

export interface Settings {
  dataset_root: string
  inbox: string
  feedback_inbox: string
  identity_path: string
  r2_endpoint: string
  r2_bucket: string
  cvat_url: string
  create_cvat_tasks: boolean
}

export interface SettingsView extends Settings {
  has_r2_access_key_id: boolean
  has_r2_secret_access_key: boolean
  has_cvat_token: boolean
}

export type SecretField = 'r2_access_key_id' | 'r2_secret_access_key' | 'cvat_token'

export interface GateProgress {
  generated_at: string
  collections: number
  items: number
  current: { groups: number; positives: number; hard_negatives: number }
  targets: { groups: number; positives: number; hard_negatives: number }
  remaining: { groups: number; positives: number; hard_negatives: number }
  pilot_gate_met: boolean
  blind_relabels: number
  blind_relabels_required: number
  audit_errors: string[]
}

export interface SyncFeedbackResult {
  file: string
  status: string
  package_id?: string
  error?: string
}

export interface SyncItemResult {
  file: string
  status: string
  error?: string
}

export interface SyncResult {
  schema_version: string
  synced_at: string
  new_contribution_count: number
  new_feedback_count: number
  contribution_intake_exit_code: number | null
  contribution_results: SyncItemResult[]
  feedback_results: SyncFeedbackResult[]
  gate_progress: GateProgress
}

export interface SyncOutcome {
  progress_log: string
  result: SyncResult
}

export interface FeedbackListItem {
  package_id: string
  feedback: {
    user_selected_feedback?: string
    incident_id?: string
    timestamp?: string
    saved_at?: string
    [key: string]: unknown
  }
}

export type FeedbackCategory = 'bug' | 'training_gap' | 'no_action' | ''

export interface FeedbackReview {
  reviewed: boolean
  note: string
  reviewed_at: string
  category: FeedbackCategory
  reported_at: string
}

export interface FeedbackReport {
  markdown: string
  path: string
}

export interface ReportSummary {
  path: string
  filename: string
  modified_unix: number
}

export interface FeedbackDetail {
  package_id: string
  feedback: Record<string, unknown>
  video_path: string
}

export interface CollectionListItem {
  package_id: string
  imported_at: string
  split: string
  cvat_status: string
  cvat_task_count: number
  duplicate_media_rejected: number
}

export interface LiveCvatTask {
  task_id?: number
  name?: string
  status?: string
  size?: number
  error?: string
}

export interface CollectionDetail {
  package_id: string
  record: {
    imported_at?: string
    split?: string
    cvat_status?: string
    cvat_tasks?: Array<{ task_id: number; name?: string }>
    duplicate_media_rejected?: number
    [key: string]: unknown
  }
  consent: {
    recorded_by: string
    rights_basis: string
    permission_reference: string
  }
  item_count: number
  live_cvat_tasks: LiveCvatTask[]
}
