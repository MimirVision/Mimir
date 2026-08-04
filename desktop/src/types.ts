export interface MimirTimelineMarker {
  time_sec?: number | null
  frame_index?: number
  type?: string
  severity?: string
  label?: string
  description?: string
  confidence?: number | null
  source?: string | null
  reason?: string | null
  camera?: string | null
}

export interface MimirCameraClip {
  camera?: string | null
  path?: string | null
  original_path?: string | null
  filename?: string | null
  video_path?: string | null
  source_video?: string | null
  source_clip?: string | null
  original_source_video?: string | null
  library_path?: string | null
  trash_path?: string | null
  exists?: boolean | null
  storage_state?: string | null
  duration_sec?: number | null
}

export interface MimirIncident {
  id: string
  source_clip?: string
  source_video?: string | null
  video_path?: string
  video_preview?: string
  video_exists?: boolean
  event_id?: number | string | null
  event_group_id?: string | null
  event_timestamp?: string | null
  display_title?: string | null
  display_timestamp?: string | null
  source_filename?: string | null
  source_stem?: string | null
  filename_timestamp_detected?: boolean | null
  event_folder?: string | null
  source_category?: string | null
  severity?: string | null
  ai_decision?: string | null
  final_severity?: string
  previous_severity?: string
  manual_status_override?: boolean
  user_status?: string
  user_deleted?: boolean
  trash_video_path?: string
  moved_to_library?: boolean
  library_video_path?: string | null
  original_source_video?: string | null
  camera_clips?: MimirCameraClip[] | Record<string, string | MimirCameraClip | null>
  camera_count?: number | null
  primary_camera?: string | null
  available_cameras?: string[] | null
  storage_state?: string | null
  storage_action_applied?: string | boolean
  library_folder?: string | null
  trash_folder?: string | null
  user_note?: string
  user_note_updated_at?: string
  user_action_log?: Array<Record<string, unknown>>
  ai_confidence?: number
  ai_scene_type?: string | null
  ai_recommended_severity?: string | null
  ai_concerns?: string[] | null
  ai_quality_warning?: string | null
  event_type?: string
  summary?: string
  evidence?: string[]
  recommended_action?: string
  score?: number | null
  persons?: number | null
  vehicles?: number | null
  active_frames?: number | null
  duration?: string | number
  max_motion_score?: number
  impact_level?: string
  impact_score?: number
  impact_reasons?: string[]
  possible_impact?: boolean
  contact_level?: string
  contact_score?: number
  contact_reasons?: string[]
  possible_contact?: boolean
  person_near_only?: boolean
  person_passby_evidence?: boolean
  person_interaction_evidence?: boolean
  contact_evidence_level?: string | null
  impact_evidence_level?: string | null
  important_evidence_found?: boolean | null
  severity_cap_applied?: boolean | null
  severity_cap_reason?: string | null
  hero_thumbnail?: string
  thumbnail?: string | null
  start_frame_image?: string
  best_frame_image?: string
  end_frame_image?: string
  contact_sheet?: string
  source_event_timestamp?: string | null
  source_event_reason?: string | null
  source_event_city?: string | null
  source_event_est_lat?: number | null
  source_event_est_lon?: number | null
  source_event_raw?: Record<string, unknown> | null
  tesla_event_timestamp?: string | null
  tesla_event_reason?: string | null
  tesla_event_city?: string | null
  timeline_markers?: MimirTimelineMarker[] | null
  key_moments?: MimirTimelineMarker[] | null
  primary_key_moment_sec?: number | null
  primary_key_moment_label?: string | null
  user_key_moment_sec?: number | null
  user_key_moment_updated_at?: string | null
  key_moment_version?: string | null
  ai_reviewed?: boolean | null
  ai_evidence?: unknown
  ai_evidence_review?: unknown
  ai_raw_response?: string | null
  ai_parse_error?: boolean | null
  ai_review_skipped_reason?: string | null
  ai_model?: string | null
  local_evidence?: unknown
  local_evidence_summary?: unknown
  severity_reasons?: unknown
  classification_debug?: unknown
  created_at?: string | null
}

export interface MimirSession {
  schema_version?: string
  scanner_version?: string
  core_version?: string
  core_build_id?: string
  generated_by?: string
  backend_runtime?: string
  scan_command_version?: string
  feature_flags?: Record<string, boolean | string | number | null | undefined>
  session_created_at?: string
  session_id?: string
  session_revision?: number
  session_updated_at?: string
  session_output_dir?: string
  session_archive_path?: string
  output_path?: string
  evidence_version?: string
  thumbnail_version?: string
  key_moment_version?: string
  ai_review_version?: string
  status: string
  started_at: string
  finished_at: string | null
  input_folder?: string
  selected_input?: string
  detected_source_type?: string | null
  drive_root?: string | null
  teslacam_root?: string | null
  scan_roots?: string[]
  source_categories_found?: string[]
  event_groups_found?: number
  camera_suffixes_found?: string[]
  source_discovery_warnings?: string[]
  source_report?: {
    selected_input?: string
    detected_source_type?: string
    is_supported?: boolean
    teslacam_root_found?: boolean
    categories_found?: string[]
    mp4_files_found?: number
    event_groups_found?: number
    camera_suffixes_found?: string[]
    event_json_files_found?: number
    warnings?: string[]
    user_message?: string
  }
  safe_input_mode?: boolean
  scan_mode?: string
  library_root?: string
  library_scan_folder?: string
  source_action?: string
  files_copied?: number
  files_moved?: number
  files_failed?: number
  usb_cleanup_performed?: boolean
  source_files_removed?: number
  storage_warnings?: string[]
  clips_processed: number
  videos_found?: number | null
  clips_scanned?: number | null
  event_groups_count?: number | null
  incidents_count?: number | null
  scan_summary?: {
    videos_found?: number | null
    clips_scanned?: number | null
    event_groups?: number | null
    incidents?: number | null
    important_count?: number | null
    review_count?: number | null
    ignore_count?: number | null
    source_path?: string | null
    source_name?: string | null
    scan_started_at?: string | null
    scan_completed_at?: string | null
    scan_duration_sec?: number | null
  }
  important: number
  review: number
  ignore: number
  performance?: {
    total_runtime_sec?: number
    videos_processed?: number
    avg_sec_per_video?: number
    yolo_runtime_sec?: number
    ai_runtime_sec?: number
    frames_sampled?: number
    ai_calls?: number
    incidents_created?: number
    version?: string
    cold_cache?: boolean
    local_results_ready_sec?: number
    object_detector_runtime_sec?: number
    object_detector_inference_count?: number
    object_detector_provider?: string
    object_detector_cpu_threads?: number
    detector_cache_hits?: number
    detector_cache_misses?: number
    analysis_cache_hits?: number
    analysis_cache_misses?: number
    parts_sec?: Record<string, number>
    progress_protocol?: string
    stage_runtime_sec?: Record<string, number>
  }
  local_results_ready?: boolean
  ai_requested?: boolean
  ai_enrichment?: {
    status?: string
    model?: string
    requested?: boolean
    can_change_final_severity?: boolean
    session_revision?: number
    started_from_revision?: number
    completed_at?: string
    wall_runtime_sec?: number
  }
  ai_enabled?: boolean | null
  ai_model?: string | null
  ai_reviewed_groups?: number | null
  ai_skipped_groups?: number | null
  ai_failed_groups?: number | null
  event_json_files_found?: number
  source_events_found?: number
  tesla_events_found?: number
  incidents: MimirIncident[]
}

export interface FrontendScanDiagnostics {
  backend_runner?: string
  backend_mode?: string
  backend_command?: string
  output_argument_used?: string
  active_output_dir?: string
  latest_session_path?: string
  latest_session_modified_time?: string
}

export type SessionLoadState = 'idle' | 'loading' | 'loaded' | 'missing' | 'error'

export type ScanRunState = 'idle' | 'running' | 'complete' | 'error'

export type ScanMode = 'fast' | 'balanced' | 'quality'

export type AiTimeoutSec = 60 | 120 | 180

export interface BackendProgress {
  protocol_version?: string
  session_id?: string
  session_revision?: number
  phase?: 'local_scan' | 'ai_enrichment' | string
  stage?: string
  message?: string
  current_video?: string
  current?: number
  completed?: number
  total?: number
  percent?: number | null
  elapsed_sec?: number
  eta_sec?: number | null
  incidents_created?: number
  ai_calls?: number
  scan_engine?: string
  enhanced_ai_available?: boolean
  local_results_ready?: boolean
  ai_enrichment_status?: string
}

export interface SessionHistoryEntry {
  session_id: string
  session_path: string
  source_name: string
  source_path: string
  created_at: string
  incidents: number
  important: number
  review: number
  ignore: number
  modified_time: string
}

export interface SystemCheckItem {
  id: string
  label: string
  ok: boolean
  message: string
  why_it_matters: string
  suggested_fix: string
  technical_details?: string
}

export interface SystemCheckResult {
  ok: boolean
  checked_at: string
  items: SystemCheckItem[]
}

export interface LocalAiStatus {
  ok: boolean
  ollama_available: boolean
  model_installed: boolean
  selected_model: string
  message: string
  technical_details?: string
}

export interface LocalAiInstallResult {
  ok: boolean
  selected_model: string
  stdout: string
  stderr: string
  message: string
}
